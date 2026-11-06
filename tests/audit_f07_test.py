"""F-07 regression test — 2026-11 independent audit.

A saved schedule must validate the tool's real input, refuse secrets on
disk, and refuse operations that would need interactive confirmation.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-07
def test_f07_schedule_payload_validation_and_secret_blocking():
    from app.core.registry import get, load_all
    from app.core.scheduler import build_schedule_payload

    load_all()
    # a tool that requires a file must not be schedulable with empty input
    payload, errors = build_schedule_payload("archive_decrypt", {"src": "", "password": ""})
    assert errors, "file-requiring tool with empty input must be rejected"
    assert payload == {}

    # a secret value must never be accepted into a schedule payload
    payload, errors = build_schedule_payload(
        "archive_decrypt", {"src": "x.zip", "password": "super-secret-pw"})
    assert errors and any("secret" in e.lower() or "رمز" in e for e in errors)
    assert payload == {} or "password" not in payload

    # valid, safe input is stored (sanitised)
    payload, errors = build_schedule_payload(
        "hash_file", {"file": "x.bin", "algo": "sha256"}) if get("hash_file") else ({}, [])
    if get("hash_file"):
        assert errors == [] and payload.get("file") == "x.bin"

    # a dangerous tool must be refused for automatic scheduling
    payload, errors = build_schedule_payload("vault_remove", {"vault": "v.wgv", "password": "p"})
    assert errors, "dangerous tools must not be auto-scheduled"
