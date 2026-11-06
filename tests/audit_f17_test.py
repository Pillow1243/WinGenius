"""F-17 regression test — 2026-11 independent audit.

site_check probes with HEAD first (one GET fallback) and download_file
with verify=False requires confirmation.
"""
from __future__ import annotations

import os

from tests.audit_helpers import _HeaderServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-17
def test_f17_site_check_prefers_head_and_falls_back_once():
    from app.core.spec import RunContext
    from app.tools.network_tools import site_check

    server = _HeaderServer({}, methods=("GET", "HEAD"))
    server.start()
    try:
        ctx = RunContext(lang="en", output_dir="/tmp")
        result = site_check(ctx, {"urls": server.url})
        assert "200" in result.details
        assert server.counts["HEAD"] == 1 and server.counts["GET"] == 0, \
            f"HEAD-first: exactly one HEAD, no GET (got {server.counts})"
    finally:
        server.stop()

    server2 = _HeaderServer({}, methods=("GET",))     # HEAD unsupported → 405
    server2.start()
    try:
        ctx = RunContext(lang="en", output_dir="/tmp")
        result = site_check(ctx, {"urls": server2.url})
        assert "200" in result.details
        assert server2.counts["HEAD"] == 1 and server2.counts["GET"] == 1, \
            f"fallback to GET exactly once (got {server2.counts})"
    finally:
        server2.stop()


def test_f17_verify_false_requires_confirmation():
    from app.core.execution import OperationRisk, classify_invocation
    from app.core.registry import load_all

    load_all()
    risky = classify_invocation("download_file", {"url": "https://x/f.bin", "verify": False})
    assert risky.risk >= OperationRisk.CONFIRM, risky
    safe = classify_invocation("download_file", {"url": "https://x/f.bin", "verify": True})
    assert safe.risk == OperationRisk.SAFE, safe
    default = classify_invocation("download_file", {"url": "https://x/f.bin"})
    assert default.risk == OperationRisk.SAFE, default
