"""F-14 regression test — 2026-11 independent audit.

archive_decrypt must name a wrong password instead of a generic failure
and must leave no .part file behind when cancelled.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-14
def _make_encrypted_zip(path, password="zip-pass-2026"):
    import pyzipper

    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        zf.writestr("hello.txt", "hello world")


def test_f14_wrong_password_message_is_specific(tmp_path):
    from app.core.spec import RunContext, ToolError
    from app.tools.files_extra3 import archive_decrypt

    src = tmp_path / "a.zip"
    _make_encrypted_zip(src)
    ctx = RunContext(lang="en", output_dir=str(tmp_path))
    with pytest.raises(ToolError) as exc_info:
        archive_decrypt(ctx, {"src": str(src), "password": "wrong-pass", "out_dir": str(tmp_path / "out")})
    msg = str(exc_info.value)
    assert "wrong password" in msg.lower() or "password" in msg.lower(), msg
    assert "not supported" not in msg.lower(), "wrong password must not be reported as unsupported encryption"


def test_f14_cancel_leaves_no_part_file(tmp_path):
    import threading

    from app.core.spec import Cancelled, RunContext
    from app.tools.files_extra3 import archive_decrypt

    src = tmp_path / "a.zip"
    _make_encrypted_zip(src)
    cancel = threading.Event()
    ctx = RunContext(lang="en", output_dir=str(tmp_path), cancel_event=cancel)
    cancel.set()                                  # cancelled before the first entry
    with pytest.raises(Cancelled):
        archive_decrypt(ctx, {"src": str(src), "password": "zip-pass-2026",
                              "out_dir": str(tmp_path / "out")})
    leftovers = list((tmp_path / "out").rglob("*.part*")) if (tmp_path / "out").exists() else []
    assert leftovers == [], f"no .part file may remain after cancel: {leftovers}"
