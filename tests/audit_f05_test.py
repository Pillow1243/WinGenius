"""F-05 regression test — 2026-11 independent audit.

The update trust store shipped a *placeholder* public key, and nothing checked
that the key CI signs releases with is the key the binary trusts. The real key
is an owner action (it must never exist in this repo), so these tests use
throw-away isolated keypairs generated in a temp dir.

Status of the finding itself: the plumbing is now tested and CI gets a clear
failure when the signed key does not pair with a pinned one; pinning the real
public key stays BLOCKED-NEEDS-OWNER.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

crypto = pytest.importorskip("cryptography")


def _temp_keypair():
    from app.core.updates import generate_signing_keypair

    # generated in memory for this test only — never written to disk
    return generate_signing_keypair("audit-temp-key")


def test_f05_signed_manifest_verifies_with_pairing_key(tmp_path):
    from app.core.updates import build_release_manifest, verify_release_manifest

    keys = _temp_keypair()
    artifact = tmp_path / "WinGenius-Setup.exe"
    artifact.write_bytes(b"MZ" + b"\0" * 64)

    manifest = build_release_manifest(
        version="7.2.0", tag="v7.2.0", files=[artifact],
        private_key_b64=keys["private_key_b64"], key_id=keys["key_id"],
    )
    assert manifest["signed"] is True

    verified, key_id, assets = verify_release_manifest(
        manifest, expected_version="7.2.0",
        trusted_pubkeys={keys["key_id"]: keys["public_key_b64"]},
    )
    assert verified is True
    assert key_id == keys["key_id"]
    assert "wingenius-setup.exe" in assets


def test_f05_tampered_manifest_is_rejected(tmp_path):
    from app.core.updates import (UpdateTamperedError, build_release_manifest,
                                  verify_release_manifest)

    keys = _temp_keypair()
    artifact = tmp_path / "WinGenius-Setup.exe"
    artifact.write_bytes(b"MZ" + b"\0" * 64)
    manifest = build_release_manifest(
        version="7.2.0", tag="v7.2.0", files=[artifact],
        private_key_b64=keys["private_key_b64"], key_id=keys["key_id"],
    )
    manifest["assets"][0]["sha256"] = "0" * 64          # forge the digest
    with pytest.raises(UpdateTamperedError):
        verify_release_manifest(
            manifest, expected_version="7.2.0",
            trusted_pubkeys={keys["key_id"]: keys["public_key_b64"]},
        )


def test_f05_manifest_from_a_foreign_key_is_rejected(tmp_path):
    from app.core.updates import (UpdateTamperedError, build_release_manifest,
                                  verify_release_manifest)

    signer = _temp_keypair()
    other = _temp_keypair()
    artifact = tmp_path / "WinGenius-Setup.exe"
    artifact.write_bytes(b"MZ" + b"\0" * 64)
    manifest = build_release_manifest(
        version="7.2.0", tag="v7.2.0", files=[artifact],
        private_key_b64=signer["private_key_b64"], key_id=signer["key_id"],
    )
    with pytest.raises(UpdateTamperedError):
        verify_release_manifest(
            manifest, expected_version="7.2.0",
            trusted_pubkeys={other["key_id"]: other["public_key_b64"]},
        )


def test_f05_pairing_check_detects_mismatch():
    from app.core.updates import signing_key_matches_keyring

    keys = _temp_keypair()
    ok, why = signing_key_matches_keyring(
        keys["private_key_b64"], keyring={keys["key_id"]: keys["public_key_b64"]})
    assert ok, why

    other = _temp_keypair()
    ok, why = signing_key_matches_keyring(
        keys["private_key_b64"], keyring={other["key_id"]: other["public_key_b64"]})
    assert not ok and "does not pair" in why


def test_f05_shipped_placeholder_is_not_release_ready():
    """The finding must stay open, not be greened with a fake signature."""
    from app.core.updates import PLACEHOLDER_PUBKEY, TRUSTED_RELEASE_PUBKEYS, keyring_status

    status = keyring_status()
    pinned = set(TRUSTED_RELEASE_PUBKEYS.values())
    if pinned == {PLACEHOLDER_PUBKEY}:
        assert status["ready"] is False
        assert status["problems"], "a placeholder key must be reported as a problem"

    # …and a random throw-away key must never be reported as pairing with it
    keys = _temp_keypair()
    from app.core.updates import signing_key_matches_keyring

    ok, why = signing_key_matches_keyring(keys["private_key_b64"])
    assert ok is False
    assert "placeholder" in why or "does not pair" in why


def test_f05_ci_uses_the_pairing_check():
    """The release gate must call the pairing check when a key is available."""
    from pathlib import Path

    script = Path("scripts/check_release.py").read_text(encoding="utf-8")
    assert "signing_key_matches_keyring" in script
    workflow = Path(".github/workflows/build-windows.yml").read_text(encoding="utf-8")
    assert "WINGENIUS_UPDATE_SIGNING_KEY" in workflow
