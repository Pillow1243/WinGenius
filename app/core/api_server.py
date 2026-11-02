"""Local HTTP API — drive WinGenius from scripts, editors or another app.

The server binds to 127.0.0.1 only, requires a per-install token and speaks
plain JSON, so `curl`, PowerShell, AutoHotkey or a browser console can all use
the same 225+ tools the UI uses.

    python main.py --serve            # http://127.0.0.1:8770
    curl -H "X-WinGenius-Token: …" http://127.0.0.1:8770/health

Routes
    GET  /health                     version, tool count, uptime
    GET  /tools[?q=&category=]       tool catalogue
    GET  /tools/<id>                 one spec, with its fields
    POST /run      {"tool": id, "values": {…}}
    GET  /workflows                  workflow list
    GET  /plan?q=<sentence>          planner chain for a sentence
    GET  /journal                    recent undoable operations
    POST /undo     {"count": 1}
    GET  /jobs                       queue statistics
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .logger import get_logger
from .paths import APP_VERSION, data_dir

log = get_logger("api")


def token_path() -> Path:
    return data_dir() / "api_token.txt"


def api_token(create: bool = True) -> str:
    path = token_path()
    try:
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                try:
                    path.chmod(0o600)   # tighten a token written by an older build
                except OSError:
                    pass
                return token
        if not create:
            return ""
        token = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The token is a credential: create it 0600 instead of relying on the
        # process umask (which is usually 0644 on Linux).
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
        try:
            path.chmod(0o600)
        except OSError:                                  # Windows ACLs, best effort
            pass
        return token
    except OSError:
        return ""


DRAIN_LIMIT = 8 * 1024 * 1024          # largest oversized body we bother to swallow


class RateLimiter:
    """Token-bucket per client, so a runaway script cannot freeze the app.

    The server only ever listens on 127.0.0.1, but "local" is not the same as
    "trusted": any process (or a stray browser tab pointed at the port) can hammer
    it. Bursts are allowed up to `burst`, then the bucket refills at
    `limit` requests per `window` seconds.
    """

    def __init__(self, limit: int = 300, window: float = 60.0, burst: Optional[int] = None) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window))
        self.burst = max(1, int(limit) if burst is None else int(burst))
        self._state: Dict[str, tuple] = {}        # client → (tokens, last_seen)
        self._lock = threading.Lock()
        self.rejected = 0

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * (self.limit / self.window))
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                self.rejected += 1
                return False
            self._state[key] = (tokens - 1.0, now)
            if len(self._state) > 512:            # never grow without bound
                self._state = {k: v for k, v in self._state.items() if now - v[1] < self.window}
            return True

    def retry_after(self) -> float:
        return round(self.window / self.limit, 2)


class ApiServer:
    """Tiny threaded JSON server around the tool registry."""

    max_body = 1_048_576                  # 1 MiB is plenty for a tool call

    def __init__(self, host: str = "127.0.0.1", port: int = 8770,
                 token: Optional[str] = None, lang: str = "fa",
                 output_dir: str = "", jobs: Any = None,
                 rate_limit: int = 300, rate_window: float = 60.0,
                 allow_query_token: bool = False) -> None:
        self.host = host
        self.port = int(port)
        self.token = token if token is not None else api_token()
        self.lang = lang
        self.output_dir = output_dir
        self.jobs = jobs
        self.limiter = RateLimiter(limit=rate_limit, window=rate_window)
        # A token in the URL ends up in logs and shell history, so it is OFF by
        # default.  Enable explicitly only for a throwaway local demo.
        self.allow_query_token = bool(allow_query_token)
        self.started_at = 0.0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ----------------------------------------------------------------- control
    def start(self) -> int:
        """Start serving in a daemon thread; returns the real port."""
        if self._server is not None:
            return self.port
        server = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self.started_at = time.time()
        self._thread = threading.Thread(target=server.serve_forever, name="wg-api", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        self._server = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def guard_stats(self) -> Dict[str, Any]:
        return {"rate_limit": self.limiter.limit, "rate_window": self.limiter.window,
                "rejected": self.limiter.rejected, "max_body": self.max_body,
                "allow_query_token": self.allow_query_token}

    # -------------------------------------------------------------------- core
    def dispatch(self, method: str, path: str, query: Dict[str, list],
                 body: Optional[Dict[str, Any]]) -> tuple:
        """Route a request. Returns (status_code, payload)."""
        from . import planner, registry
        from .journal import journal
        from .spec import RunContext
        from .workflows import store as workflow_store

        route = path.rstrip("/") or "/"

        if route == "/health":
            return 200, {
                "app": "WinGenius", "version": APP_VERSION, "status": "ok",
                "tools": len(registry.all_tools()),
                "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
                "guard": self.guard_stats(),
            }

        if route == "/tools":
            items = registry.all_tools()
            needle = (query.get("q", [""])[0] or "").strip().lower()
            category = (query.get("category", [""])[0] or "").strip()
            if category:
                items = [spec for spec in items if spec.category == category]
            if needle:
                items = [spec for spec in items
                         if needle in spec.id.lower() or needle in spec.name.lower()
                         or needle in spec.name_en.lower()]
            return 200, {"count": len(items), "tools": [
                {"id": s.id, "name": s.name, "name_en": s.name_en, "category": s.category,
                 "desc": s.desc, "desc_en": s.desc_en} for s in items[:400]]}

        if route.startswith("/tools/"):
            spec = registry.get(route.split("/", 2)[2])
            if spec is None:
                return 404, {"error": "unknown tool"}
            return 200, {"id": spec.id, "name": spec.name, "name_en": spec.name_en,
                         "category": spec.category, "desc": spec.desc,
                         "fields": [{"key": f.key, "kind": f.kind, "label": f.label,
                                     "label_en": f.label_en, "required": f.required,
                                     "default": f.default} for f in spec.fields]}

        if route == "/workflows":
            return 200, {"workflows": [
                {"id": w.id, "name": w.name_for(self.lang), "steps": [s.tool_id for s in w.steps]}
                for w in workflow_store.all()]}

        if route == "/plan":
            sentence = (query.get("q", [""])[0] or "").strip()
            workflow = planner.build_plan(sentence, [], self.lang) if sentence else None
            if workflow is None:
                return 404, {"error": "no plan matched", "query": sentence}
            return 200, {"name": workflow.name_for(self.lang),
                         "steps": [{"tool": s.tool_id, "values": dict(s.values)} for s in workflow.steps]}

        if route == "/journal":
            ops = journal().entries(limit=40)
            return 200, {"operations": [op.as_dict() for op in ops],
                         "stats": journal().stats()}

        if route == "/jobs":
            if self.jobs is None:
                return 200, {"jobs": [], "hint": "no queue attached"}
            return 200, {"stats": self.jobs.stats()}

        if route == "/run" and method == "POST":
            payload = body or {}
            tool_id = str(payload.get("tool") or "")
            values = payload.get("values") or {}
            if not isinstance(values, dict):
                return 400, {"error": "values must be an object"}
            spec = registry.get(tool_id)
            if spec is None:
                return 404, {"error": f"unknown tool: {tool_id}"}
            ctx = RunContext(lang=self.lang, output_dir=self.output_dir or str(Path.home()))
            try:
                result = spec.run(ctx, values)
            except Exception as exc:                       # tool error → 200 with ok=false
                return 200, {"ok": False, "error": str(exc), "tool": tool_id}
            return 200, {"ok": True, "tool": tool_id,
                         "message": getattr(result, "message", ""),
                         "outputs": getattr(result, "outputs", []) or [],
                         "details": getattr(result, "details", "")}

        if route == "/undo" and method == "POST":
            try:
                count = max(1, min(50, int((body or {}).get("count", 1) or 1)))
            except (TypeError, ValueError):
                return 400, {"error": "count must be an integer"}
            results = journal().undo_last(count)
            return 200, {"undone": [{"id": r["id"], "status": r["status"],
                                     "restored": r["restored"], "problems": r["problems"]}
                                    for r in results]}

        return 404, {"error": "not found", "path": route}


def _make_handler(server: ApiServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WinGeniusAPI/" + APP_VERSION
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------------- helpers
        def _host_ok(self) -> bool:
            """Refuse DNS-rebinding: only our own loopback hostnames are valid."""
            host = (self.headers.get("Host") or "").strip().lower()
            if host.startswith("["):                    # IPv6 literal
                host = host[1:].split("]")[0]
            else:
                host = host.split(":")[0]
            return host in {"127.0.0.1", "localhost", "::1"}

        def _authorised(self, query: Dict[str, list]) -> bool:
            if not server.token:
                return True
            header = self.headers.get("X-WinGenius-Token", "")
            if header and secrets.compare_digest(header, server.token):
                return True
            if not server.allow_query_token:
                return False
            given = (query.get("token", [""])[0] or "")
            return bool(given) and secrets.compare_digest(given, server.token)

        def _throttled(self) -> bool:
            client = self.client_address[0] if self.client_address else "?"
            if server.limiter.allow(client):
                return False
            self._reply(429, {"error": "too many requests",
                              "retry_after": server.limiter.retry_after()})
            return True

        def _reply(self, status: int, payload: Dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(raw)
            # Audit trail without ever recording the token or the query string.
            log.info("api %s %s -> %s", self.command, urlparse(self.path).path, status)

        def _body(self) -> Optional[Dict[str, Any]]:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                return None
            if length <= 0:
                return {}
            if length > server.max_body:
                # Drain a *reasonable* oversized body so the client can read our 413
                # (closing the socket mid-upload just gives it a broken pipe), but
                # never read a hostile multi-gigabyte payload.
                if length <= DRAIN_LIMIT:
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                else:
                    self.close_connection = True
                return "too-large"                    # sentinel, handled by the caller
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None

        # -------------------------------------------------------------- verbs
        def do_GET(self) -> None:                      # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if self._throttled():
                return
            if not self._host_ok():
                self._reply(403, {"error": "host not allowed"})
                return
            if not self._authorised(query):
                self._reply(401, {"error": "unauthorised"})
                return
            try:
                status, payload = server.dispatch("GET", parsed.path, query, None)
            except Exception:                           # noqa: BLE001
                log.exception("api GET %s failed", parsed.path)
                status, payload = 500, {"error": "internal error"}
            self._reply(status, payload)

        def do_POST(self) -> None:                     # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if self._throttled():
                return
            if not self._host_ok():
                self._reply(403, {"error": "host not allowed"})
                return
            if not self._authorised(query):
                self._reply(401, {"error": "unauthorised"})
                return
            body = self._body()
            if body == "too-large":
                self._reply(413, {"error": "payload too large", "limit": server.max_body})
                return
            if body is None:
                self._reply(400, {"error": "invalid JSON body"})
                return
            try:
                status, payload = server.dispatch("POST", parsed.path, query, body)
            except Exception:                           # noqa: BLE001
                log.exception("api POST %s failed", parsed.path)
                status, payload = 500, {"error": "internal error"}
            self._reply(status, payload)

        def log_message(self, fmt: str, *args) -> None:   # keep the console quiet
            return

    return Handler


# ------------------------------------------------------------------- helpers
def serve_blocking(port: int = 8770, host: str = "127.0.0.1", lang: str = "fa") -> int:
    """Run the API in the foreground (used by `main.py --serve`)."""
    server = ApiServer(host=host, port=port, lang=lang)
    server.start()
    print(f"WinGenius API → {server.base_url()}   token: {server.token}")
    print("press Ctrl+C to stop")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping…")
        server.stop()
    return 0
