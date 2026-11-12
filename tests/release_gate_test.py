"""Ordinary validation must not require owner-only release signing credentials."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "scripts/check_release.py", *args], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_regular_release_consistency_gate_does_not_require_owner_key() -> None:
    result = _run()
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "BLOCKED-NEEDS-OWNER" in output
    assert "WARN  owner-dependent release authorization" in output


def test_tagged_release_authorization_fails_closed_without_owner_key() -> None:
    result = _run("--owner-release-auth")
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "BLOCKED-NEEDS-OWNER" in output
    assert "placeholder" in output
