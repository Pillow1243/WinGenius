"""Shared helpers for the 2026-11 audit regression tests."""
from __future__ import annotations

import http.server
import threading


class _HeaderServer(threading.Thread):
    """Tiny HTTP server returning configurable headers; counts requests."""

    def __init__(self, headers, methods=("GET", "HEAD")):
        import http.server

        self.counts = {"GET": 0, "HEAD": 0}
        self._headers = headers
        self._methods = methods

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _reply(self, method):
                self.send_response(200)
                for key, value in outer._headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", "2")
                self.end_headers()
                if method == "GET":
                    self.wfile.write(b"ok")

            def do_GET(self):
                outer.counts["GET"] += 1
                if "GET" in outer._methods:
                    self._reply("GET")
                else:
                    self.send_error(405)

            def do_HEAD(self):
                outer.counts["HEAD"] += 1
                if "HEAD" in outer._methods:
                    self._reply("HEAD")
                else:
                    self.send_error(405)

            def log_message(self, *args):
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        super().__init__(daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    def run(self):
        self._server.serve_forever()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


WEAK_HEADERS = {
    "Content-Security-Policy": "default-src * 'unsafe-inline' 'unsafe-eval'",
    "Strict-Transport-Security": "max-age=0",
    "X-Content-Type-Options": "sniff",
    "X-Frame-Options": "ALLOWALL",
    "Referrer-Policy": "unsafe-url",
}
STRONG_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
