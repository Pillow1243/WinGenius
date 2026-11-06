"""F-09 regression test — 2026-11 independent audit.

Permission hardening must actually mean something on Windows: `os.chmod` only
toggles the read-only attribute there, so "0600" was a no-op and the data tree,
journals and the API token kept inherited ACLs. On POSIX the 0600/0700 modes
must still be applied.
"""
from __future__ import annotations

import os
import stat
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


# --------------------------------------------------------------------- POSIX
@posix_only
def test_f09_posix_file_is_0600(tmp_path):
    from app.core.paths import secure_private_file

    target = tmp_path / "journal.db"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    secure_private_file(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@posix_only
def test_f09_posix_dir_is_0700(tmp_path):
    from app.core.paths import data_dir

    created = data_dir()
    mode = stat.S_IMODE(created.stat().st_mode)
    assert mode & 0o077 == 0, oct(mode)


# ------------------------------------------------------------------- Windows
def test_f09_windows_uses_icacls_not_chmod(tmp_path, monkeypatch):
    """On Windows the helper must restrict the ACL, not silently do nothing."""
    from app.core import paths

    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setenv("USERNAME", "audit-user")

    target = tmp_path / "queue.json"
    target.write_text("[]", encoding="utf-8")
    mode_before = stat.S_IMODE(target.stat().st_mode)
    paths.secure_private_file(target)

    assert calls, "Windows hardening must invoke icacls"
    flat = " ".join(" ".join(call) for call in calls)
    assert "icacls" in flat
    assert "/inheritance:r" in flat
    assert "audit-user" in flat
    assert stat.S_IMODE(target.stat().st_mode) == mode_before, (
        "chmod must not be used on Windows (it only flips the read-only bit)")


def test_f09_windows_dir_gets_inheritable_grant(tmp_path, monkeypatch):
    from app.core import paths

    calls = []
    monkeypatch.setattr(paths.subprocess, "run",
                        lambda args, **kw: calls.append(list(args)) or
                        subprocess.CompletedProcess(args, 0, b"", b""))
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setenv("USERNAME", "audit-user")

    paths._secure_dir(tmp_path)
    flat = " ".join(" ".join(call) for call in calls)
    assert "(OI)(CI)F" in flat, f"directory grant must be inheritable: {flat}"


def test_f09_failing_icacls_never_raises(tmp_path, monkeypatch):
    """A locked-down machine may refuse icacls; startup must still work."""
    from app.core import paths

    def boom(*args, **kwargs):
        raise OSError("icacls missing")

    monkeypatch.setattr(paths.subprocess, "run", boom)
    monkeypatch.setattr(paths, "IS_WINDOWS", True)

    target = tmp_path / "vault.wgv"
    target.write_bytes(b"x")
    assert paths.secure_private_file(target) == target
    assert paths._secure_dir(tmp_path) == tmp_path


def test_f09_api_token_is_hardened(tmp_path, monkeypatch):
    """The API token file goes through the shared hardening helper."""
    from app.core import api_server

    seen = []
    monkeypatch.setattr(api_server, "secure_private_file",
                        lambda path: seen.append(path) or path)
    token_path = tmp_path / "api_token.txt"
    monkeypatch.setattr(api_server, "token_path", lambda: token_path)

    token = api_server.api_token(create=True)
    assert token and seen == [token_path]

    seen.clear()
    assert api_server.api_token(create=True) == token   # existing token is re-hardened
    assert seen == [token_path]
