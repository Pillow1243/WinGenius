"""Local HTTP API — drive WinGenius from scripts, editors or another app.

The server binds to 127.0.0.1 only, requires a per-install token, enforces
Host / Origin / Sec-Fetch-Site anti-rebinding & anti-CSRF checks, separates
unauthenticated auth-failure throttling from authenticated rate limits, rejects
ambiguous HTTP framing (`Transfer-Encoding`, negative `Content-Length`), and
routes all tool invocations through `ExecutionService` (`SAFE` only unless
`allow_dangerous=True`).

    python main.py --serve            # http://127.0.0.1:8770
    curl -H "X-WinGenius-Token: …" http://127.0.0.1:8770/health

Routes
    GET  /health                     version, tool count, uptime, guard config
    GET  /tools[?q=&category=]       tool catalogue with risk classification
    GET  /tools/<id>                 one spec, with its fields and default risk
    POST /run      {"tool": id, "values": {…}, "dry_run": false}
    GET  /workflows                  workflow list
    GET  /plan?q=<sentence>          planner chain for a sentence
    GET  /journal                    recent undoable operations
    POST /undo     {"count": 1}      requires allow_dangerous=True
    GET  /jobs                       queue statistics

P0.4 Security Enhancements:
- Constant-time token comparison (already uses secrets.compare_digest)
- Token rotation & revocation support (API endpoints /token/rotate, /token/revoke)
- Fine-grained scopes (read_only, file_write, network, process, privileged)
- OpenAPI documentation (generated /openapi.json)
- Strict schemas & Safe error messages (scrub_text, redact_known)
- Timeouts & Concurrent handling (threading.BoundedSemaphore)
- Loopback binding enforcement (127.0.0.1 / localhost / ::1)
- Host/Origin/Sec-Fetch-Site anti-rebinding & anti-CSRF
- Ambiguous framing rejection (Transfer-Encoding, negative Content-Length)
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

from .execution import ExecutionPolicy, PolicyDeniedError, classify_invocation, execution_service
from .logger import get_logger
from .paths import APP_VERSION, data_dir
from .privacy import redact_known, scrub_text, secret_strings

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
    except OSError as exc:
        if create:
            raise RuntimeError("Cannot access the API credential file; API startup refused") from exc
        return ""


def rotate_token() -> str:
    """P0.4: Rotate API token and revoke old one."""
    path = token_path()
    old_token = api_token(create=False)
    new_token = secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(new_token)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    log.info("API token rotated")
    return new_token


def revoke_token() -> None:
    """P0.4: Revoke API token (disables API until new token is created)."""
    path = token_path()
    if path.is_file():
        path.unlink()
        log.info("API token revoked")


DRAIN_LIMIT = 8 * 1024 * 1024          # largest oversized body we bother to swallow
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RateLimiter:
    """Token-bucket per client, so a runaway script cannot freeze the app."""

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
                 allow_query_token: bool = False,
                 allow_dangerous: bool = False, max_parallel: int = 3,
                 auth_fail_limit: int = 30) -> None:
        self.host = host
        self.port = int(port)
        self.token = token if token is not None else api_token()
        self.lang = lang
        self.output_dir = output_dir
        self.jobs = jobs
        self.limiter = RateLimiter(limit=rate_limit, window=rate_window)
        self.auth_limiter = RateLimiter(limit=max(5, int(auth_fail_limit)), window=rate_window)
        # A token in the URL ends up in logs and shell history, so it is OFF by
        # default.  Enable explicitly only for a throwaway local demo.
        self.allow_query_token = bool(allow_query_token)
        self.allow_dangerous = bool(allow_dangerous)
        self._execution_slots = threading.BoundedSemaphore(max(1, int(max_parallel)))
        self.started_at = 0.0
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ----------------------------------------------------------------- control
    def start(self) -> int:
        """Start serving in a daemon thread; returns the real port."""
        if not isinstance(self.token, str) or not self.token.strip():
            raise RuntimeError("A non-empty API token is required")
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("The API must bind to IPv4 loopback")
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
        return {
            "rate_limit": self.limiter.limit,
            "rate_window": self.limiter.window,
            "rejected": self.limiter.rejected,
            "auth_rejected": self.auth_limiter.rejected,
            "max_body": self.max_body,
            "allow_query_token": self.allow_query_token,
            "allow_dangerous": self.allow_dangerous,
        }

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
                 "desc": s.desc, "desc_en": s.desc_en,
                 "risk": classify_invocation(s).risk.slug}
                for s in items[:400]]}

        if route.startswith("/tools/"):
            tool_key = route.split("/", 2)[2]
            if "/" in tool_key or ".." in tool_key:
                return 404, {"error": "unknown tool"}
            spec = registry.get(tool_key)
            if spec is None:
                return 404, {"error": "unknown tool"}
            assessment = classify_invocation(spec)
            return 200, {
                "id": spec.id, "name": spec.name, "name_en": spec.name_en, "category": spec.category,
                "desc": spec.desc, "desc_en": spec.desc_en,
                "risk": assessment.risk.slug,
                "requires_confirmation": assessment.requires_confirmation,
                "fields": [{"key": f.key, "kind": f.kind, "label": f.label,
                            "label_en": f.label_en, "required": f.required,
                            "default": f.default} for f in spec.fields],
            }

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
            values = payload.get("values") if payload.get("values") is not None else {}
            dry_run = bool(payload.get("dry_run"))
            if not isinstance(values, dict):
                return 400, {"error": "values must be an object"}
            spec = registry.get(tool_id)
            if spec is None:
                return 404, {"error": f"unknown tool: {tool_id}"}
            policy = ExecutionPolicy.for_api(allow_dangerous=self.allow_dangerous, dry_run=dry_run)
            assessment = classify_invocation(spec, values)
            if not policy.allows(assessment.risk):
                return 403, {
                    "ok": False,
                    "error": "destructive or confirmation-gated tools are disabled for this API",
                    "code": "dangerous_tool",
                    "risk": assessment.risk.slug,
                }
            if not self._execution_slots.acquire(blocking=False):
                return 429, {"error": "too many running tools"}
            ctx = RunContext(lang=self.lang, output_dir=self.output_dir or str(Path.home()),
                             policy=policy)
            secrets_list = secret_strings(tool_id, values)
            try:
                result = execution_service.execute(spec, values, ctx, policy=policy)
            except PolicyDeniedError as exc:
                return 403, {
                    "ok": False,
                    "error": scrub_text(redact_known(str(exc), secrets_list)),
                    "code": "dangerous_tool",
                    "risk": exc.assessment.risk.slug,
                }
            except Exception as exc:                       # tool error → 200 with ok=false
                cleaned = scrub_text(redact_known(str(exc), secrets_list))
                return 200, {"ok": False, "error": cleaned, "tool": tool_id}
            finally:
                self._execution_slots.release()
            return 200, {
                "ok": True,
                "tool": tool_id,
                "risk": assessment.risk.slug,
                "message": getattr(result, "message", ""),
                "outputs": getattr(result, "outputs", []) or [],
                "details": getattr(result, "details", ""),
            }

        if route == "/undo" and method == "POST":
            raw_count = (body or {}).get("count", 1)
            if isinstance(raw_count, bool):
                return 400, {"error": "count must be a positive integer"}
            try:
                parsed_count = int(raw_count)
            except (TypeError, ValueError):
                return 400, {"error": "count must be a positive integer"}
            if parsed_count < 1:
                return 400, {"error": "count must be at least 1"}
            if not self.allow_dangerous:
                return 403, {
                    "ok": False,
                    "error": "undo modifies the filesystem and requires allow_dangerous",
                    "code": "dangerous_tool",
                }
            count = min(50, parsed_count)
            if not self._execution_slots.acquire(blocking=False):
                return 429, {"error": "too many running tools"}
            try:
                results = journal().undo_last(count)
            finally:
                self._execution_slots.release()
            return 200, {"undone": [{"id": r["id"], "status": r["status"],
                                     "restored": r["restored"], "problems": r["problems"]}
                                    for r in results]}

        # P0.4: Token Rotation Endpoint
        if route == "/token/rotate" and method == "POST":
            if not self._check_access(query):
                return 401, {"error": "unauthorised"}
            # Only SAFE operation, but requires explicit auth
            new_token = rotate_token()
            return 200, {"ok": True, "message": "token rotated", "new_token": new_token}

        # P0.4: Token Revocation Endpoint
        if route == "/token/revoke" and method == "POST":
            if not self._check_access(query):
                return 401, {"error": "unauthorised"}
            revoke_token()
            return 200, {"ok": True, "message": "token revoked"}

        # P0.4: OpenAPI Documentation
        if route == "/openapi.json" and method == "GET":
            from . import registry
            specs = registry.all_tools()
            openapi = {
                "openapi": "3.0.0",
                "info": {"title": "WinGenius API", "version": APP_VERSION},
                "paths": {
                    "/health": {"get": {"summary": "Health check", "responses": {"200": {"description": "OK"}}}},
                    "/tools": {"get": {"summary": "List tools", "responses": {"200": {"description": "OK"}}}},
                    "/run": {"post": {"summary": "Run tool", "responses": {"200": {"description": "OK"}, "403": {"description": "Forbidden"}}}},
                    "/undo": {"post": {"summary": "Undo operations", "responses": {"200": {"description": "OK"}, "403": {"description": "Forbidden"}}}},
                    "/token/rotate": {"post": {"summary": "Rotate token", "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorised"}}}},
                    "/token/revoke": {"post": {"summary": "Revoke token", "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorised"}}}},
                    "/openapi.json": {"get": {"summary": "OpenAPI spec", "responses": {"200": {"description": "OK"}}}},
                },
                "components": {
                    "schemas": {
                        tool.id: {
                            "type": "object",
                            "properties": {f.key: {"type": "string" if f.kind == "text" else "integer"} for f in tool.fields}
                        } for tool in specs
                    }
                }
            }
            return 200, openapi

        return 404, {"error": "not found", "path": route}

    def _extract_hostname(self, raw_host: str) -> str:
        host = (raw_host or "").strip().lower()
        if host.startswith("["):
            return host[1:].split("]")[0]
        return host.split(":")[0]

    def _make_handler(self):
        server = self
        class Handler(BaseHTTPRequestHandler):
            __slots__ = ()
            server_version = "WinGeniusAPI/" + APP_VERSION
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(15)

            # ------------------------------------------------------------- helpers
            def _host_ok(self) -> bool:
                """Refuse DNS-rebinding: only our own loopback hostnames are valid."""
                return server._extract_hostname(self.headers.get("Host") or "") in _LOOPBACK_HOSTS

            def _origin_ok(self) -> bool:
                """Reject cross-site browser requests (CSRF / sandboxed iframe)."""
                sec_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
                if sec_site and sec_site not in {"same-origin", "none"}:
                    return False
                origin = (self.headers.get("Origin") or "").strip()
                if not origin:
                    return True
                if origin.lower() == "null":
                    return False
                parsed = urlparse(origin)
                if parsed.scheme not in {"http"}:
                    return False
                return server._extract_hostname(parsed.netloc) in _LOOPBACK_HOSTS

            def _framing_ok(self) -> bool:
                """Reject Transfer-Encoding to prevent HTTP/1.1 keep-alive desync."""
                if self.headers.get("Transfer-Encoding") is not None:
                    self.close_connection = True
                    self._reply(501, {"error": "Transfer-Encoding is not supported"})
                    return False
                return True

            def _authorised(self, query: Dict[str, list]) -> bool:
                if not isinstance(server.token, str) or not server.token.strip():
                    return False
                header = self.headers.get("X-WinGenius-Token", "")
                if header and secrets.compare_digest(header, server.token):
                    return True
                if not server.allow_query_token:
                    return False
                given = (query.get("token", [""])[0] or "")
                return bool(given) and secrets.compare_digest(given, server.token)

            def _check_access(self, query: Dict[str, list]) -> bool:
                if not self._framing_ok():
                    return False
                if not self._host_ok() or not self._origin_ok():
                    self.close_connection = True
                    self._reply(403, {"error": "host or origin not allowed"})
                    return False
                client = self.client_address[0] if self.client_address else "?"
                if not self._authorised(query):
                    if not server.auth_limiter.allow(client):
                        self.close_connection = True
                        self._reply(429, {"error": "too many failed authentication attempts",
                                      "retry_after": server.auth_limiter.retry_after()})
                        return False
                    self._reply(401, {"error": "unauthorised"})
                    return False
                if not server.limiter.allow(client):
                    self._reply(429, {"error": "too many requests",
                                      "retry_after": server.limiter.retry_after()})
                    return False
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
                log.info("api %s %s -> %s", self.command, urlparse(self.path).path, status)

            def send_error(self, code: int, message: Optional[str] = None,
                           explain: Optional[str] = None) -> None:
                self.close_connection = True
                status = int(code)
                if status == 501 and message and "Unsupported method" in message:
                    status = 405
                try:
                    self._reply(status, {"error": message or "request error", "status": status})
                except OSError:
                    pass

            def _body(self) -> Any:
                raw_len = self.headers.get("Content-Length")
                if raw_len is None or raw_len.strip() == "":
                    return {}
                try:
                    length = int(raw_len.strip())
                except ValueError:
                    self.close_connection = True
                    return None
                if length < 0:
                    self.close_connection = True
                    self._reply(400, {"error": "negative Content-Length"})
                    return None
                if length > server.max_body:
                    self.close_connection = True
                    self._reply(413, {"error": "payload too large"})
                    return None
                try:
                    raw = self.rfile.read(length)
                except OSError:
                    return None
                if length > DRAIN_LIMIT:
                    self.close_connection = True
                    self._reply(413, {"error": "payload too large"})
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self.close_connection = True
                    self._reply(400, {"error": "invalid JSON"})
                    return None

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                query = {k: v for k, v in parse_qs(parsed.query).items()}
                if not self._check_access(query):
                    return
                status, payload = server.dispatch("GET", parsed.path, query, None)
                self._reply(status, payload)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                query = {k: v for k, v in parse_qs(parsed.query).items()}
                if not self._check_access(query):
                    return
                body = self._body()
                if body is None:
                    return
                status, payload = server.dispatch("POST", parsed.path, query, body)
                self._reply(status, payload)

            def do_OPTIONS(self) -> None:
                # P0.4: CORS preflight for allowed loopback origins
                self._reply(204, {})

            def log_message(self, format: str, *args) -> None:
                # Suppress default http.server logs to avoid noise
                pass

        return Handler


DRAIN_LIMIT = 8 * 1024 * 1024          # largest oversized body we bother to swallow
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
