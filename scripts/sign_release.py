#!/usr/bin/env python3
"""Generate Ed25519 release-signing keys and build/sign/verify `update-manifest.json`.

Usage:
  # 1. Maintainer one-time offline setup (store private_key_b64 in GitHub Secret
  #    WINGENIUS_UPDATE_SIGNING_KEY and place public_key_b64 in app/core/updates.py):
  python scripts/sign_release.py --keygen --key-id wingenius-release-2026-01

  # 2. CI / Release build (signs automatically if WINGENIUS_UPDATE_SIGNING_KEY is set):
  python scripts/sign_release.py --build --dir release --version 7.1.0 --tag v7.1.0

  # 3. Verify an existing release manifest:
  python scripts/sign_release.py --verify release/update-manifest.json --version 7.1.0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.updates import (  # noqa: E402
    MANIFEST_FILENAME,
    build_release_manifest,
    generate_signing_keypair,
    verify_release_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keygen", action="store_true", help="Generate a new Ed25519 signing keypair")
    parser.add_argument("--build", action="store_true", help="Build (and optionally sign) update-manifest.json")
    parser.add_argument("--verify", type=str, default="", help="Verify an update-manifest.json file")
    parser.add_argument("--dir", type=str, default="release", help="Release artifacts directory")
    parser.add_argument("--version", type=str, default="", help="Release version (e.g. 7.1.0)")
    parser.add_argument("--tag", type=str, default="", help="Release git tag (e.g. v7.1.0)")
    parser.add_argument("--key-id", type=str, default="wingenius-release-2026-01", help="Signing key ID")
    args = parser.parse_args()

    if args.keygen:
        kp = generate_signing_keypair(key_id=args.key_id)
        print(json.dumps(kp, indent=2))
        return 0

    if args.build:
        rel_dir = Path(args.dir)
        if not rel_dir.is_dir():
            print(f"error: release directory not found: {rel_dir}", file=sys.stderr)
            return 2
        version = args.version.strip().lstrip("vV")
        if not version:
            print("error: --version is required for --build", file=sys.stderr)
            return 2
        tag = args.tag.strip() or f"v{version}"
        priv_key = os.environ.get("WINGENIUS_UPDATE_SIGNING_KEY", "").strip()
        key_id = os.environ.get("WINGENIUS_UPDATE_KEY_ID", args.key_id).strip() or args.key_id
        files = sorted(p for p in rel_dir.iterdir() if p.is_file() and p.name != MANIFEST_FILENAME)
        manifest = build_release_manifest(
            version=version,
            tag=tag,
            files=files,
            private_key_b64=priv_key,
            key_id=key_id,
        )
        out_path = rel_dir / MANIFEST_FILENAME
        out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        state = f"signed (key_id={key_id})" if manifest.get("signed") else "unsigned (no WINGENIUS_UPDATE_SIGNING_KEY)"
        print(f"wrote {out_path} ({len(manifest['assets'])} assets, {state})")
        return 0

    if args.verify:
        manifest_path = Path(args.verify)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_ver = (args.version or str(data.get("version") or "")).lstrip("vV")
        sig_ok, key_id, assets = verify_release_manifest(data, expected_version=expected_ver)
        print(f"OK  version={expected_ver}  assets={len(assets)}  signed={sig_ok}  key_id={key_id or '-'}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
