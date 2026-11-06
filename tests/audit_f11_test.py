"""F-11 regression test — 2026-11 independent audit.

The vault must reject oversized files before reading them into memory.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-11
def test_f11_oversized_file_rejected_before_read(tmp_path):
    from app.core.vault import MAX_ENTRY_BYTES, Vault

    vault_path = tmp_path / "v.wgv"
    vault = Vault(vault_path, "pw-test-2026")
    vault.create()
    big = tmp_path / "big.bin"
    big.touch()
    os.truncate(big, MAX_ENTRY_BYTES + 1)          # sparse: no real allocation/read
    with pytest.raises(Exception) as exc_info:
        vault.add([big])
    text = str(exc_info.value)
    assert "too large" in text.lower() or "بزرگ" in text, text
    # vault is still healthy and normal files still work
    small = tmp_path / "small.txt"
    small.write_text("hello", encoding="utf-8")
    assert vault.add([small]) == ["small.txt"]
    vault2 = Vault(vault_path, "pw-test-2026")
    assert vault2.unlock() and vault2.names() == ["small.txt"]


def test_f11_total_vault_cap_enforced(tmp_path, monkeypatch):
    from app.core import vault as vault_mod
    from app.core.vault import Vault

    vault_path = tmp_path / "v.wgv"
    vault = Vault(vault_path, "pw-test-2026")
    vault.create()
    # lower the total cap for the test so no huge files are needed
    monkeypatch.setattr(vault_mod, "MAX_VAULT_BYTES", 100 * 1024 * 1024)
    chunk = tmp_path / "chunk.bin"
    chunk.touch()
    os.truncate(chunk, 64 * 1024 * 1024)            # sparse: 64 MiB, no real read
    vault.add([chunk])
    with pytest.raises(Exception) as exc_info:
        vault.add([chunk])                          # 64 + 64 > 100 MiB cap
    assert "limit" in str(exc_info.value).lower() or "سقف" in str(exc_info.value)
