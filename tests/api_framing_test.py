"""Regression tests for HTTP request framing and connection reuse."""
from __future__ import annotations

import http.client
import json
import socket

import pytest

from app.core import api_server


def _raw_response(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.sendall(request)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except ConnectionResetError:
                # Some platforms reset a socket closed with unread request bytes.
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


def test_rejected_request_cannot_pipeline_its_unread_body():
    server = api_server.ApiServer(port=0, token="test-token")
    server.dispatch = lambda *args: (200, {"ok": True})
    port = server.start()
    try:
        injected = (b"GET /health HTTP/1.1\r\nHost: localhost\r\n"
                    b"X-WinGenius-Token: test-token\r\nConnection: close\r\n\r\n")
        prefix = (b"POST /run HTTP/1.1\r\nHost: localhost\r\nContent-Length: "
                  + str(len(injected)).encode() + b"\r\n\r\n")
        response = _raw_response(port, prefix + injected)
        assert b" 401 " in response and response.count(b"HTTP/1.1 ") == 1
    finally:
        server.stop()


@pytest.mark.parametrize("headers", [
    b"Content-Length: 0\r\nContent-Length: 0\r\n",
    b"Content-Length: 0, 0\r\n",
    b"Content-Length: +1\r\n",
    b"Content-Length: " + b"9" * 100 + b"\r\n",
    b"Content-Length: 1\r\n",  # a GET body would be left unread
])
def test_ambiguous_or_unconsumed_framing_closes_connection(headers):
    server = api_server.ApiServer(port=0, token="test-token")
    server.dispatch = lambda *args: (200, {"ok": True})
    port = server.start()
    try:
        request = (b"GET /health HTTP/1.1\r\nHost: localhost\r\n"
                   b"X-WinGenius-Token: test-token\r\n" + headers + b"\r\nx")
        response = _raw_response(port, request)
        assert b" 400 " in response and response.count(b"HTTP/1.1 ") == 1
    finally:
        server.stop()


def test_normal_keep_alive_still_works():
    server = api_server.ApiServer(port=0, token="test-token")
    server.dispatch = lambda *args: (200, {"ok": True})
    port = server.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        for _ in range(2):
            conn.request("GET", "/health", headers={"X-WinGenius-Token": "test-token"})
            response = conn.getresponse()
            assert response.status == 200 and json.loads(response.read())["ok"]
        conn.close()
    finally:
        server.stop()
