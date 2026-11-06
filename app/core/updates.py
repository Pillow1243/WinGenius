"""Secure, opt-in release checker, cryptographic verifier, downloader, and rollback updater.

Security Architecture
---------------------
1. **Privacy & Opt-In Network Discipline**:
   WinGenius never phones home without user consent (`--check-update`, `--update`, clicking
   "Check for updates" in the UI, or explicitly enabling `ui/auto_check_updates`).
2. **Strict Origin & Redirect Pinning**:
   Metadata requests are restricted to `https://api.github.com/repos/Pillow1243/WinGenius/...`.
   Binary downloads and every HTTP redirect hop are restricted to `https://` on GitHub's
   official release asset hosts (`github.com/Pillow1243/WinGenius/releases/...` and
   `*.githubusercontent.com`). Any HTTP downgrade or off-origin redirect is aborted.
3. **Independent Multi-Layer Integrity & Signature Verification**:
   Relying solely on an unsigned `SHA256SUMS.txt` stored beside the binary on the same
   server is insufficient against release-account compromise. Before installation, an
   update package must pass:
     * Windows PE header structure validation (`MZ` + `PE\\0\\0`),
     * Multi-source SHA-256 & byte-length cross-verification (`update-manifest.json`,
       `SHA256SUMS.txt`, and GitHub's independent `asset.digest` API metadata),
     * Anti-replay / anti-downgrade binding (`repo`, `tag`, `version > current`),
     * Windows Authenticode tamper check (`HashMismatch` / `NotTrusted` is always rejected),
     * Cryptographic signature verification via **either** an offline-held `Ed25519`
       release signing key (`TRUSTED_RELEASE_PUBKEYS` baked into the binary) **or** a
       valid OS-trusted Windows Authenticode signature (`require_signature=True` by default).
4. **User Data Preservation & Transactional Rollback**:
   Before applying any update, `prepare_rollback_snapshot()` creates a transactional
   SQLite/profile backup (`upgrade.backup_data_dir`) and backs up the current `.exe`
   with its SHA-256 in `<data_dir>/updates/rollback/`. User settings, Vault `.wgv`
   containers, journal databases, macros, and personal files are never deleted.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from .logger import get_logger
from .paths import APP_VERSION, IS_FROZEN, IS_WINDOWS, data_dir, is_portable, logs_dir, project_root
from .privacy import atomic_json_write

log = get_logger("updates")

REPO_SLUG = "Pillow1243/WinGenius"
RELEASES_URL = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO_SLUG}/releases"

ALLOWED_API_HOSTS = frozenset({"api.github.com"})
ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "codeload.github.com",
})

MAX_METADATA_BYTES = 2 * 1024 * 1024       # 2 MiB JSON/checksum cap
MAX_ASSET_BYTES = 512 * 1024 * 1024        # 512 MiB installer/exe cap
DOWNLOAD_CHUNK_BYTES = 64 * 1024           # 64 KiB streaming chunks
MANIFEST_FILENAME = "update-manifest.json"
CHECKSUMS_FILENAME = "SHA256SUMS.txt"

# Pinned Ed25519 public keys (base64 raw 32-byte Ed25519 public key) compiled into
# the client binary. Private signing keys MUST remain offline / in protected CI secrets
# (`WINGENIUS_UPDATE_SIGNING_KEY`) and NEVER in the repository.
# Generate/rotate keys with: `python scripts/sign_release.py --keygen`
TRUSTED_RELEASE_PUBKEYS: Dict[str, str] = {
    "wingenius-release-2026-01": "9f3K6wR8mQ2vL7nX4pT1yB5cH0jD8sF2gA6eW3uZ9iM=",
}

# Optional pinned Windows Authenticode SHA-1/SHA-256 certificate thumbprints (uppercase hex).
# Authenticode alone establishes publisher identity only when this set is configured.
# An empty set means that a pinned Ed25519 manifest is required by default.
TRUSTED_AUTHENTICODE_THUMBPRINTS: frozenset[str] = frozenset()


# ------------------------------------------------------------------ exceptions
class UpdateError(RuntimeError):
    """Base error for update check, download, verification, or installation."""


class UpdateCancelledError(UpdateError):
    """Raised when the user cancels an in-progress update download."""


class UpdateSecurityError(UpdateError):
    """Raised when an untrusted URL, redirect, or protocol violation occurs."""


class UpdateTamperedError(UpdateSecurityError):
    """Raised on hash mismatch, corrupt PE, invalid signature, or version replay."""


class UpdateSignatureRequiredError(UpdateSecurityError):
    """Raised when hash matches but no cryptographic signature is present."""

    def __init__(self, message: str, sha256: str = "", asset_name: str = "") -> None:
        super().__init__(message)
        self.sha256 = sha256
        self.asset_name = asset_name


# ----------------------------------------------------------------- data models
@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    digest: str = ""          # GitHub API `sha256:<hex>` when present
    content_type: str = ""

    @property
    def github_sha256(self) -> str:
        raw = str(self.digest or "").strip().lower()
        if raw.startswith("sha256:"):
            candidate = raw.split(":", 1)[1].strip()
            if re.fullmatch(r"[0-9a-f]{64}", candidate):
                return candidate
        return ""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    name: str
    body: str
    html_url: str
    published_at: str
    prerelease: bool = False
    draft: bool = False
    assets: Tuple[ReleaseAsset, ...] = ()

    def find_asset(self, name: str) -> Optional[ReleaseAsset]:
        target = name.lower()
        for asset in self.assets:
            if asset.name.lower() == target:
                return asset
        return None


@dataclass(frozen=True)
class AuthenticodeResult:
    checked: bool
    signed: bool
    valid: bool
    status: str
    signer_subject: str = ""
    thumbprint: str = ""
    detail: str = ""


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    cryptographically_verified: bool
    sha256: str
    size: int
    sources_checked: Tuple[str, ...]
    manifest_verified: bool = False
    manifest_key_id: str = ""
    authenticode: Optional[AuthenticodeResult] = None
    pe_valid: bool = False
    trust_summary_fa: str = ""
    trust_summary_en: str = ""


@dataclass(frozen=True)
class VerifiedUpdatePackage:
    version: str
    tag_name: str
    asset_name: str
    file_path: Path
    install_mode: str         # installer | portable | standalone_exe | source
    verification: VerificationReport
    release_url: str = ""
    release_notes: str = ""


ProgressCallback = Callable[[int, int, str], None]


# ------------------------------------------------------------- version & paths
def version_key(value: str) -> Tuple[int, ...]:
    """Turn ``v7.1.0`` (or ``7.1.0-rc1``) into ``(7, 1, 0)`` for numeric comparison."""
    parts: list[int] = []
    cleaned = str(value or "").strip().lstrip("vV")
    # Strip build/prerelease suffix after '-' or '+' on the version string
    core = re.split(r"[-+]", cleaned, maxsplit=1)[0]
    for chunk in core.split("."):
        m = re.match(r"^\d+", chunk.strip())
        parts.append(int(m.group(0)) if m else 0)
    return tuple((parts + [0, 0, 0])[:3])


def updates_dir(root: Optional[Path] = None) -> Path:
    path = (root or data_dir()) / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir(root: Optional[Path] = None) -> Path:
    path = updates_dir(root) / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rollback_dir(root: Optional[Path] = None) -> Path:
    path = updates_dir(root) / "rollback"
    path.mkdir(parents=True, exist_ok=True)
    return path


def rollback_state_path(root: Optional[Path] = None) -> Path:
    return rollback_dir(root) / "rollback_state.json"


def detect_install_mode(
    exe_path: Optional[Path] = None,
    *,
    portable: Optional[bool] = None,
    frozen: Optional[bool] = None,
) -> str:
    """Detect how WinGenius is currently installed (`installer`, `portable`, `standalone_exe`, `source`)."""
    is_frz = IS_FROZEN if frozen is None else bool(frozen)
    is_port = is_portable() if portable is None else bool(portable)
    target = Path(exe_path) if exe_path is not None else (Path(sys.executable) if is_frz else project_root())
    folder = target.parent if target.is_file() or target.suffix.lower() == ".exe" else target

    if is_port or (folder / "portable.flag").is_file():
        return "portable"
    if not is_frz and exe_path is None:
        return "source"
    if (folder / "unins000.exe").is_file() or (folder / "unins001.exe").is_file():
        return "installer"
    return "standalone_exe"


def current_install_info(root: Optional[Path] = None) -> Dict[str, Any]:
    """Return current runtime version, mode, executable path, and rollback availability."""
    mode = detect_install_mode()
    rb = get_rollback_state(root)
    return {
        "version": APP_VERSION,
        "frozen": IS_FROZEN,
        "windows": IS_WINDOWS,
        "portable": is_portable(),
        "install_mode": mode,
        "executable": str(Path(sys.executable).resolve()) if IS_FROZEN else "",
        "data_dir": str(root or data_dir()),
        "rollback_available": bool(rb and rb.get("backup_exe_exists")),
        "rollback_version": str(rb.get("previous_version") or "") if rb else "",
    }


# ------------------------------------------------------- URL & redirect safety
def validate_github_url(
    url: str,
    *,
    kind: str = "download",
    allow_loopback_test: bool = False,
) -> str:
    """Validate that `url` is a clean HTTPS GitHub URL for `Pillow1243/WinGenius`."""
    raw = str(url or "").strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\x00")):
        raise UpdateSecurityError("Invalid or empty update URL.")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.username or parsed.password:
        raise UpdateSecurityError("Credentials in update URLs are forbidden.")

    if allow_loopback_test and host in ("127.0.0.1", "localhost") and parsed.scheme in ("http", "https"):
        return raw

    if parsed.scheme != "https":
        raise UpdateSecurityError(f"Insecure scheme '{parsed.scheme}' refused; HTTPS is required.")

    if kind == "api":
        if host not in ALLOWED_API_HOSTS:
            raise UpdateSecurityError(f"Untrusted release API host: {host!r}")
        expected_prefix = f"/repos/{REPO_SLUG}/releases"
        if not parsed.path.startswith(expected_prefix):
            raise UpdateSecurityError(f"Unexpected GitHub API path: {parsed.path!r}")
        return raw

    if host not in ALLOWED_DOWNLOAD_HOSTS:
        raise UpdateSecurityError(f"Untrusted download host: {host!r}")
    if host == "github.com":
        expected_dl = f"/{REPO_SLUG}/releases/download/"
        if not parsed.path.startswith(expected_dl):
            raise UpdateSecurityError(f"Download URL does not belong to {REPO_SLUG}: {parsed.path!r}")
    return raw


class _SafeGitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Verify every HTTP redirect hop stays on HTTPS and an allowlisted GitHub asset host."""

    def __init__(self, *, allow_loopback_test: bool = False) -> None:
        super().__init__()
        self._allow_loopback_test = allow_loopback_test

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_github_url(newurl, kind="download", allow_loopback_test=self._allow_loopback_test)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(
    url: str,
    *,
    kind: str,
    timeout: float = 15.0,
    accept: str = "application/octet-stream",
    allow_loopback_test: bool = False,
):
    validate_github_url(url, kind=kind, allow_loopback_test=allow_loopback_test)
    opener = urllib.request.build_opener(
        _SafeGitHubRedirectHandler(allow_loopback_test=allow_loopback_test)
    )
    req = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": f"WinGenius/{APP_VERSION}",
        },
    )
    return opener.open(req, timeout=timeout)


# ----------------------------------------------------- release check & parsing
def _parse_release_payload(data: Mapping[str, Any]) -> ReleaseInfo:
    if not isinstance(data, Mapping):
        raise UpdateError("Release API response is not a JSON object.")
    tag_name = str(data.get("tag_name") or "").strip()
    version = tag_name.lstrip("vV").strip()
    if not version or not re.match(r"^\d+(\.\d+){1,3}", version):
        raise UpdateError(f"Invalid release tag version: {tag_name!r}")

    assets: List[ReleaseAsset] = []
    raw_assets = data.get("assets")
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            dl_url = str(item.get("browser_download_url") or "").strip()
            if not name or not dl_url:
                continue
            try:
                size = max(0, int(item.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            assets.append(ReleaseAsset(
                name=name,
                url=dl_url,
                size=size,
                digest=str(item.get("digest") or ""),
                content_type=str(item.get("content_type") or ""),
            ))

    return ReleaseInfo(
        version=version,
        tag_name=tag_name or f"v{version}",
        name=str(data.get("name") or f"WinGenius {version}"),
        body=str(data.get("body") or "").strip(),
        html_url=str(data.get("html_url") or RELEASES_PAGE_URL),
        published_at=str(data.get("published_at") or ""),
        prerelease=bool(data.get("prerelease")),
        draft=bool(data.get("draft")),
        assets=tuple(assets),
    )


def select_best_asset(release: ReleaseInfo, install_mode: Optional[str] = None) -> Optional[ReleaseAsset]:
    """Choose the best `.exe` asset for `install_mode` (`installer` vs `portable`/`standalone_exe`)."""
    mode = install_mode or detect_install_mode()
    exe_assets = [
        a for a in release.assets
        if a.name.lower().endswith(".exe") and "/" not in a.name and "\\" not in a.name
    ]
    if not exe_assets:
        return None

    setup_candidates = [a for a in exe_assets if "setup" in a.name.lower()]
    standalone_candidates = [a for a in exe_assets if "setup" not in a.name.lower()]

    if mode in ("portable", "standalone_exe"):
        return (standalone_candidates[0] if standalone_candidates else setup_candidates[0])
    return (setup_candidates[0] if setup_candidates else standalone_candidates[0])


def fetch_latest_release(
    timeout: float = 10.0,
    url: str = RELEASES_URL,
    *,
    include_prerelease: bool = False,
    allow_loopback_test: bool = False,
) -> ReleaseInfo:
    """Fetch and validate the latest published GitHub Release metadata."""
    with _open_url(
        url,
        kind="api",
        timeout=timeout,
        accept="application/vnd.github+json",
        allow_loopback_test=allow_loopback_test,
    ) as resp:
        raw = resp.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise UpdateSecurityError("Release metadata response exceeded safety size limit.")
    data = json.loads(raw.decode("utf-8"))
    info = _parse_release_payload(data)
    if info.draft:
        raise UpdateError("Latest release is marked as draft.")
    if info.prerelease and not include_prerelease:
        raise UpdateError("Latest release is a pre-release.")
    return info


def check_latest(
    timeout: float = 8.0,
    url: str = RELEASES_URL,
    *,
    current_version: str = APP_VERSION,
    install_mode: Optional[str] = None,
    allow_loopback_test: bool = False,
) -> Dict[str, Any]:
    """Return a serialisable update-check dictionary. Never raises on network errors."""
    mode = install_mode or detect_install_mode()
    try:
        release = fetch_latest_release(
            timeout=timeout,
            url=url,
            allow_loopback_test=allow_loopback_test,
        )
    except (urllib.error.URLError, OSError, ValueError, TimeoutError, UpdateError) as exc:
        return {"ok": False, "error": str(exc), "current": current_version, "install_mode": mode}

    chosen = select_best_asset(release, mode)
    manifest_asset = release.find_asset(MANIFEST_FILENAME)
    checksums_asset = release.find_asset(CHECKSUMS_FILENAME)
    update_available = version_key(release.version) > version_key(current_version)
    return {
        "ok": True,
        "current": current_version,
        "latest": release.version,
        "tag_name": release.tag_name,
        "name": release.name,
        "body": release.body,
        "published_at": release.published_at,
        "url": release.html_url,
        "install_mode": mode,
        "update_available": update_available,
        "asset": asdict(chosen) if chosen else None,
        "has_signed_manifest": manifest_asset is not None,
        "has_checksums": checksums_asset is not None,
        "assets": [asdict(a) for a in release.assets],
    }


def release_info_from_check(data: Mapping[str, Any]) -> ReleaseInfo:
    """Reconstruct a `ReleaseInfo` object from a successful `check_latest()` dictionary."""
    raw_assets = data.get("assets") or []
    assets = tuple(
        ReleaseAsset(
            name=str(a.get("name") or ""),
            url=str(a.get("url") or ""),
            size=int(a.get("size") or 0),
            digest=str(a.get("digest") or ""),
            content_type=str(a.get("content_type") or ""),
        )
        for a in raw_assets
        if isinstance(a, Mapping) and a.get("name") and a.get("url")
    )
    ver = str(data.get("latest") or "")
    return ReleaseInfo(
        version=ver,
        tag_name=str(data.get("tag_name") or f"v{ver}"),
        name=str(data.get("name") or f"WinGenius {ver}"),
        body=str(data.get("body") or ""),
        html_url=str(data.get("url") or RELEASES_PAGE_URL),
        published_at=str(data.get("published_at") or ""),
        assets=assets,
    )


# -------------------------------------------- cryptographic manifest & signing
def generate_signing_keypair(key_id: str = "wingenius-release-key") -> Dict[str, str]:
    """Generate a fresh Ed25519 release-signing keypair (for maintainer offline setup)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "key_id": key_id,
        "private_key_b64": base64.b64encode(priv_raw).decode("ascii"),
        "public_key_b64": base64.b64encode(pub_raw).decode("ascii"),
    }


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Deterministic canonical JSON payload covered by the Ed25519 signature."""
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        raise UpdateTamperedError("Manifest 'assets' list is missing or malformed.")
    norm_assets = []
    for item in raw_assets:
        if not isinstance(item, Mapping):
            continue
        norm_assets.append({
            "name": str(item.get("name") or ""),
            "sha256": str(item.get("sha256") or "").lower(),
            "size": int(item.get("size") or 0),
        })
    norm_assets.sort(key=lambda entry: entry["name"])
    canonical = {
        "assets": norm_assets,
        "repo": str(manifest.get("repo") or ""),
        "schema": int(manifest.get("schema") or 1),
        "tag": str(manifest.get("tag") or ""),
        "version": str(manifest.get("version") or ""),
    }
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def build_release_manifest(
    *,
    version: str,
    tag: str,
    files: List[Path],
    repo: str = REPO_SLUG,
    private_key_b64: str = "",
    key_id: str = "wingenius-release-2026-01",
) -> Dict[str, Any]:
    """Build (and optionally Ed25519-sign) `update-manifest.json` for release artifacts."""
    assets = []
    for file_path in sorted(files, key=lambda p: p.name):
        if not file_path.is_file() or file_path.name == MANIFEST_FILENAME:
            continue
        assets.append({
            "name": file_path.name,
            "sha256": sha256_file(file_path),
            "size": file_path.stat().st_size,
        })
    manifest: Dict[str, Any] = {
        "schema": 1,
        "repo": repo,
        "tag": tag,
        "version": version,
        "assets": assets,
        "signed": False,
        "key_id": "",
        "signature": "",
    }
    if private_key_b64.strip():
        return sign_release_manifest(manifest, private_key_b64.strip(), key_id=key_id)
    return manifest


def sign_release_manifest(
    manifest: Mapping[str, Any],
    private_key_b64: str,
    *,
    key_id: str = "wingenius-release-2026-01",
) -> Dict[str, Any]:
    """Attach a detached Ed25519 signature over `canonical_manifest_bytes(manifest)`."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw_priv = base64.b64decode(private_key_b64.strip())
    if len(raw_priv) != 32:
        raise ValueError("Ed25519 raw private key must be exactly 32 bytes (base64-encoded).")
    priv = Ed25519PrivateKey.from_private_bytes(raw_priv)
    payload = canonical_manifest_bytes(manifest)
    sig = priv.sign(payload)
    out = dict(manifest)
    out["signed"] = True
    out["key_id"] = key_id
    out["signature"] = base64.b64encode(sig).decode("ascii")
    return out


def verify_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_version: str,
    expected_repo: str = REPO_SLUG,
    trusted_pubkeys: Optional[Mapping[str, str]] = None,
) -> Tuple[bool, str, Dict[str, Dict[str, Any]]]:
    """Validate `update-manifest.json` structure, anti-replay version binding, and Ed25519 signature.

    Returns `(signature_verified, key_id, {asset_name_lower: {"sha256": ..., "size": ...}})`.
    Raises `UpdateTamperedError` if ANY field is forged, replayed, or has a bad signature.
    """
    if not isinstance(manifest, Mapping):
        raise UpdateTamperedError("Update manifest is not a valid JSON object.")
    repo = str(manifest.get("repo") or "")
    version = str(manifest.get("version") or "").lstrip("vV")
    if repo != expected_repo:
        raise UpdateTamperedError(f"Manifest repo mismatch: expected {expected_repo!r}, got {repo!r}.")
    if version != expected_version.lstrip("vV"):
        raise UpdateTamperedError(
            f"Manifest version replay/mismatch: expected {expected_version!r}, got {version!r}."
        )

    asset_map: Dict[str, Dict[str, Any]] = {}
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise UpdateTamperedError("Update manifest contains no asset entries.")
    for entry in raw_assets:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        size = int(entry.get("size") or 0)
        if not name or not re.fullmatch(r"[0-9a-f]{64}", digest) or size <= 0:
            raise UpdateTamperedError(f"Invalid asset entry in manifest for {name!r}.")
        asset_map[name.lower()] = {"name": name, "sha256": digest, "size": size}

    sig_b64 = str(manifest.get("signature") or "").strip()
    signed_flag = bool(manifest.get("signed"))
    key_id = str(manifest.get("key_id") or "").strip()

    if not sig_b64 and not signed_flag:
        return False, "", asset_map

    if not sig_b64 or not key_id:
        raise UpdateTamperedError("Manifest claims to be signed but is missing signature or key_id.")

    keyring = dict(TRUSTED_RELEASE_PUBKEYS)
    if trusted_pubkeys:
        keyring.update(trusted_pubkeys)
    pub_b64 = keyring.get(key_id)
    if not pub_b64:
        raise UpdateTamperedError(f"Manifest signed by unknown or untrusted key_id: {key_id!r}.")

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw_pub = base64.b64decode(pub_b64)
        raw_sig = base64.b64decode(sig_b64)
        pub = Ed25519PublicKey.from_public_bytes(raw_pub)
        pub.verify(raw_sig, canonical_manifest_bytes(manifest))
    except (ValueError, InvalidSignature) as exc:
        raise UpdateTamperedError("Ed25519 cryptographic signature verification FAILED!") from exc

    return True, key_id, asset_map


# ------------------------------------ binary structure, checksums, Authenticode
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def parse_sha256sums(text: str) -> Dict[str, str]:
    """Parse `SHA256SUMS.txt` lines (`<64-hex>  <filename>`) into `{filename_lower: hex}`."""
    mapping: Dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip().lstrip("*").strip()
        if re.fullmatch(r"[0-9a-f]{64}", digest) and name and "/" not in name and "\\" not in name:
            mapping[name.lower()] = digest
    return mapping


def verify_pe_executable(path: Path) -> bool:
    """Verify `path` has a valid Windows Portable Executable (`MZ` + `PE\\0\\0`) header."""
    try:
        size = path.stat().st_size
        if size < 1024:
            raise UpdateTamperedError(f"Downloaded executable is suspiciously small ({size} bytes).")
        with path.open("rb") as fh:
            dos = fh.read(64)
            if len(dos) < 64 or dos[:2] != b"MZ":
                raise UpdateTamperedError("Downloaded file is not a valid Windows executable (missing MZ).")
            pe_offset = struct.unpack("<I", dos[0x3C:0x40])[0]
            if pe_offset < 64 or pe_offset > size - 4:
                raise UpdateTamperedError("Corrupt PE header offset in downloaded executable.")
            fh.seek(pe_offset)
            if fh.read(4) != b"PE\x00\x00":
                raise UpdateTamperedError("Downloaded file is missing valid PE\\0\\0 signature.")
    except OSError as exc:
        raise UpdateError(f"Could not inspect downloaded executable: {exc}") from exc
    return True


def verify_authenticode(
    path: Path,
    *,
    trusted_thumbprints: Optional[frozenset[str]] = None,
) -> AuthenticodeResult:
    """Inspect Windows Authenticode signature using WinTrust (`Get-AuthenticodeSignature`).

    * On non-Windows hosts, returns `AuthenticodeResult(checked=False, signed=False, valid=False, ...)`.
    * On Windows, ANY corrupt or untrusted signature (`HashMismatch`, `NotTrusted`, etc.)
      raises `UpdateTamperedError` immediately.
    """
    if not IS_WINDOWS:
        return AuthenticodeResult(
            checked=False, signed=False, valid=False, status="UnsupportedPlatform",
            detail="Authenticode verification runs on Windows hosts.",
        )
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:WG_AUTH_TARGET; "
        "[PSCustomObject]@{"
        "Status=[string]$s.Status; "
        "StatusMessage=[string]$s.StatusMessage; "
        "Thumbprint=if($s.SignerCertificate){[string]$s.SignerCertificate.Thumbprint}else{''}; "
        "Subject=if($s.SignerCertificate){[string]$s.SignerCertificate.Subject}else{''}"
        "} | ConvertTo-Json -Compress"
    )
    env = dict(os.environ, WG_AUTH_TARGET=str(path))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=flags,
        )
        payload = json.loads((proc.stdout or "").strip() or "{}")
    except Exception as exc:
        return AuthenticodeResult(
            checked=False, signed=False, valid=False, status="CheckError", detail=str(exc),
        )

    status = str(payload.get("Status") or "Unknown")
    thumb = str(payload.get("Thumbprint") or "").strip().upper()
    subject = str(payload.get("Subject") or "").strip()
    detail = str(payload.get("StatusMessage") or "").strip()

    if status == "NotSigned":
        return AuthenticodeResult(
            checked=True, signed=False, valid=False, status=status, detail=detail,
        )
    if status != "Valid":
        raise UpdateTamperedError(
            f"Windows Authenticode rejected the binary (status={status}: {detail})."
        )

    allowed_thumbs = TRUSTED_AUTHENTICODE_THUMBPRINTS if trusted_thumbprints is None else trusted_thumbprints
    if allowed_thumbs and thumb not in allowed_thumbs:
        raise UpdateTamperedError(
            f"Authenticode certificate thumbprint {thumb!r} is not in trusted publisher list."
        )
    return AuthenticodeResult(
        checked=True,
        signed=True,
        valid=True,
        status="Valid",
        signer_subject=subject,
        thumbprint=thumb,
        detail=detail,
    )


def verify_downloaded_package(
    file_path: Path,
    *,
    asset: ReleaseAsset,
    expected_version: str,
    manifest_data: Optional[Mapping[str, Any]] = None,
    sha256sums_text: str = "",
    require_signature: bool = True,
    trusted_pubkeys: Optional[Mapping[str, str]] = None,
    authenticode_checker: Optional[Callable[[Path], AuthenticodeResult]] = None,
) -> VerificationReport:
    """Run full multi-source integrity, PE structure, Ed25519 manifest, and Authenticode checks."""
    verify_pe_executable(file_path)
    actual_size = file_path.stat().st_size
    if asset.size > 0 and actual_size != asset.size:
        raise UpdateTamperedError(
            f"Downloaded size mismatch: expected {asset.size} bytes, got {actual_size} bytes."
        )

    actual_sha256 = sha256_file(file_path)
    expected_hashes: Dict[str, str] = {}

    if asset.github_sha256:
        expected_hashes["github_api_digest"] = asset.github_sha256

    if sha256sums_text.strip():
        sums_map = parse_sha256sums(sha256sums_text)
        if asset.name.lower() not in sums_map:
            raise UpdateTamperedError(f"{CHECKSUMS_FILENAME} does not list {asset.name!r}.")
        expected_hashes["sha256sums_txt"] = sums_map[asset.name.lower()]

    manifest_verified = False
    manifest_key_id = ""
    if manifest_data is not None:
        manifest_verified, manifest_key_id, manifest_assets = verify_release_manifest(
            manifest_data,
            expected_version=expected_version,
            trusted_pubkeys=trusted_pubkeys,
        )
        m_entry = manifest_assets.get(asset.name.lower())
        if m_entry is None:
            raise UpdateTamperedError(f"{MANIFEST_FILENAME} does not list {asset.name!r}.")
        if m_entry["size"] != actual_size:
            raise UpdateTamperedError(
                f"Manifest size mismatch for {asset.name!r}: expected {m_entry['size']}, got {actual_size}."
            )
        expected_hashes["update_manifest"] = m_entry["sha256"]

    if not expected_hashes:
        raise UpdateTamperedError(
            "No SHA-256 verification metadata available (neither manifest, SHA256SUMS.txt, nor API digest)."
        )

    unique_expected = set(expected_hashes.values())
    if len(unique_expected) > 1:
        raise UpdateTamperedError(
            f"Conflicting SHA-256 digests across release metadata sources: {expected_hashes}"
        )

    for source_name, exp_digest in expected_hashes.items():
        if actual_sha256 != exp_digest:
            raise UpdateTamperedError(
                f"SHA-256 mismatch against {source_name}: expected {exp_digest}, got {actual_sha256}."
            )

    auth_fn = authenticode_checker or verify_authenticode
    auth_res = auth_fn(file_path)
    if auth_res.signed and not auth_res.valid:
        raise UpdateTamperedError(
            f"Windows Authenticode rejected the binary (status={auth_res.status}: {auth_res.detail})."
        )

    publisher_pinned = (auth_res.valid and bool(TRUSTED_AUTHENTICODE_THUMBPRINTS)
                        and auth_res.thumbprint.upper() in TRUSTED_AUTHENTICODE_THUMBPRINTS)
    crypto_ok = bool(manifest_verified or publisher_pinned)
    if require_signature and not crypto_ok:
        raise UpdateSignatureRequiredError(
            f"SHA-256 ({actual_sha256[:16]}…) matched {', '.join(expected_hashes)}, "
            "but neither a signed Ed25519 release manifest nor a pinned publisher "
            "Authenticode signature was present. Installation blocked by default policy.",
            sha256=actual_sha256,
            asset_name=asset.name,
        )

    if crypto_ok:
        summary_fa = "امضای دیجیتال و هش SHA-256 فایل به‌صورت مستقل تأیید شد ✓"
        summary_en = "Cryptographic signature and multi-source SHA-256 verified ✓"
    else:
        summary_fa = "هش SHA-256 تطبیق داده شد (بدون امضای دیجیتال — تأیید دستی کاربر)"
        summary_en = "SHA-256 verified (unsigned build — explicit user override)"

    return VerificationReport(
        ok=True,
        cryptographically_verified=crypto_ok,
        sha256=actual_sha256,
        size=actual_size,
        sources_checked=tuple(sorted(expected_hashes.keys())),
        manifest_verified=manifest_verified,
        manifest_key_id=manifest_key_id,
        authenticode=auth_res,
        pe_valid=True,
        trust_summary_fa=summary_fa,
        trust_summary_en=summary_en,
    )


# -------------------------------------------- streaming download + verification
def _fetch_text_asset(
    asset: Optional[ReleaseAsset],
    *,
    timeout: float,
    allow_loopback_test: bool,
) -> str:
    if asset is None:
        return ""
    with _open_url(
        asset.url,
        kind="download",
        timeout=timeout,
        accept="text/plain, application/json, */*",
        allow_loopback_test=allow_loopback_test,
    ) as resp:
        data = resp.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise UpdateSecurityError(f"Metadata asset {asset.name!r} exceeded size cap.")
    return data.decode("utf-8", errors="strict")


def download_and_verify_update(
    release: ReleaseInfo,
    *,
    asset: Optional[ReleaseAsset] = None,
    install_mode: Optional[str] = None,
    current_version: str = APP_VERSION,
    dest_dir: Optional[Path] = None,
    require_signature: bool = True,
    trusted_pubkeys: Optional[Mapping[str, str]] = None,
    authenticode_checker: Optional[Callable[[Path], AuthenticodeResult]] = None,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    timeout: float = 30.0,
    allow_loopback_test: bool = False,
) -> VerifiedUpdatePackage:
    """Download the target release `.exe` in chunks and verify it before staging."""
    if version_key(release.version) <= version_key(current_version):
        raise UpdateTamperedError(
            f"Refusing downgrade or same-version package ({release.version} <= {current_version})."
        )

    mode = install_mode or detect_install_mode()
    chosen = asset or select_best_asset(release, mode)
    if chosen is None:
        raise UpdateError("No compatible Windows .exe asset found in this GitHub Release.")
    if chosen.size > MAX_ASSET_BYTES:
        raise UpdateSecurityError(f"Asset {chosen.name!r} exceeds {MAX_ASSET_BYTES} byte limit.")

    safe_name = Path(chosen.name).name
    if safe_name != chosen.name or not safe_name.lower().endswith(".exe"):
        raise UpdateSecurityError(f"Unsafe release asset filename: {chosen.name!r}")

    if progress:
        progress(0, chosen.size, "metadata")

    manifest_asset = release.find_asset(MANIFEST_FILENAME)
    checksums_asset = release.find_asset(CHECKSUMS_FILENAME)
    manifest_data: Optional[Dict[str, Any]] = None
    if manifest_asset is not None:
        raw_manifest = _fetch_text_asset(
            manifest_asset, timeout=timeout, allow_loopback_test=allow_loopback_test
        )
        manifest_data = json.loads(raw_manifest)
        # Fail fast BEFORE downloading 100+ MB if the manifest signature/version is forged!
        verify_release_manifest(
            manifest_data,
            expected_version=release.version,
            trusted_pubkeys=trusted_pubkeys,
        )

    sha256sums_text = _fetch_text_asset(
        checksums_asset, timeout=timeout, allow_loopback_test=allow_loopback_test
    )

    stage_folder = dest_dir or staging_dir()
    stage_folder.mkdir(parents=True, exist_ok=True)
    part_path = stage_folder / f"{safe_name}.{os.getpid()}.part"
    final_path = stage_folder / safe_name

    received = 0
    try:
        with _open_url(
            chosen.url,
            kind="download",
            timeout=timeout,
            accept="application/octet-stream",
            allow_loopback_test=allow_loopback_test,
        ) as resp, part_path.open("wb") as out_fh:
            total = chosen.size or int(resp.headers.get("Content-Length") or 0)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise UpdateCancelledError("Update download cancelled by user.")
                chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_ASSET_BYTES:
                    raise UpdateSecurityError("Download exceeded maximum allowed file size.")
                out_fh.write(chunk)
                if progress:
                    progress(received, total, "downloading")
            out_fh.flush()
            os.fsync(out_fh.fileno())

        if progress:
            progress(received, received, "verifying")

        report = verify_downloaded_package(
            part_path,
            asset=chosen,
            expected_version=release.version,
            manifest_data=manifest_data,
            sha256sums_text=sha256sums_text,
            require_signature=require_signature,
            trusted_pubkeys=trusted_pubkeys,
            authenticode_checker=authenticode_checker,
        )
        os.replace(part_path, final_path)
    except Exception:
        try:
            part_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    log.info(
        "verified update package version=%s asset=%s sha256=%s crypto=%s",
        release.version, safe_name, report.sha256[:16], report.cryptographically_verified,
    )
    return VerifiedUpdatePackage(
        version=release.version,
        tag_name=release.tag_name,
        asset_name=safe_name,
        file_path=final_path,
        install_mode=mode,
        verification=report,
        release_url=release.html_url,
        release_notes=release.body,
    )


# ----------------------------------------- backup, apply, & rollback lifecycle
def get_rollback_state(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = rollback_state_path(root)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        backup_exe = Path(str(state.get("backup_exe_path") or ""))
        state["backup_exe_exists"] = bool(str(state.get("backup_exe_path") or "") and backup_exe.is_file())
        data_bak = Path(str(state.get("data_backup_dir") or ""))
        state["data_backup_exists"] = bool(str(state.get("data_backup_dir") or "") and data_bak.is_dir())
        return state
    except (OSError, ValueError):
        return None


def prepare_rollback_snapshot(
    *,
    target_version: str,
    install_mode: str,
    current_version: str = APP_VERSION,
    current_exe: Optional[Path] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Back up user profile data + current executable before running any update."""
    from . import upgrade

    base = root or data_dir()
    rb_folder = rollback_dir(base)
    data_backup = upgrade.backup_data_dir(root=base, reason=f"pre-update-{current_version}")

    exe_source = current_exe if current_exe is not None else (Path(sys.executable) if IS_FROZEN else None)
    backup_exe_path = ""
    backup_exe_sha = ""
    target_exe_path = ""

    if exe_source is not None and exe_source.is_file():
        target_exe_path = str(exe_source.resolve())
        dest_exe = rb_folder / f"WinGenius-{current_version}.exe"
        if exe_source.resolve() != dest_exe.resolve():
            shutil.copy2(exe_source, dest_exe)
        backup_exe_path = str(dest_exe)
        backup_exe_sha = sha256_file(dest_exe)

    state: Dict[str, Any] = {
        "previous_version": current_version,
        "target_version": target_version,
        "install_mode": install_mode,
        "target_exe_path": target_exe_path,
        "backup_exe_path": backup_exe_path,
        "backup_exe_sha256": backup_exe_sha,
        "data_backup_dir": str(data_backup) if data_backup else "",
        "created_at": int(time.time()),
        "status": "pending",
    }
    atomic_json_write(rollback_state_path(base), state)
    return state


def apply_portable_swap(
    staged_exe: Path,
    target_exe: Path,
    *,
    expected_sha256: str,
    health_check: Optional[Callable[[Path], bool]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Atomically swap a standalone/portable executable with automatic rollback on failure."""
    if not staged_exe.is_file():
        raise UpdateError(f"Staged update file not found: {staged_exe}")
    if sha256_file(staged_exe) != expected_sha256.lower():
        raise UpdateTamperedError("Staged update file failed pre-install SHA-256 re-verification!")

    prev_path = target_exe.with_name(target_exe.name + ".prev")
    had_existing = target_exe.is_file()
    if had_existing:
        shutil.copy2(target_exe, prev_path)

    try:
        os.replace(staged_exe, target_exe)
        if health_check is not None and not health_check(target_exe):
            raise UpdateError("Post-swap executable health check failed.")
    except Exception as exc:
        if had_existing and prev_path.is_file():
            try:
                os.replace(prev_path, target_exe)
            except OSError:
                shutil.copy2(prev_path, target_exe)
        state = get_rollback_state(root)
        if state is not None:
            state["status"] = "rolled_back"
            state["last_error"] = str(exc)
            atomic_json_write(rollback_state_path(root), state)
        raise UpdateError(f"Update swap failed; restored previous executable ({exc}).") from exc
    finally:
        try:
            prev_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True, "installed_path": str(target_exe)}


def _build_windows_supervisor_script() -> str:
    """PowerShell out-of-process update & rollback supervisor (reads only env vars)."""
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "$parentPid = [int]$env:WG_PARENT_PID\n"
        "for ($i = 0; $i -lt 60; $i++) {\n"
        "  if (-not (Get-Process -Id $parentPid -ErrorAction SilentlyContinue)) { break }\n"
        "  Start-Sleep -Milliseconds 500\n"
        "}\n"
        "$mode = $env:WG_UPDATE_MODE\n"
        "$staged = $env:WG_STAGED_EXE\n"
        "$target = $env:WG_TARGET_EXE\n"
        "$backup = $env:WG_BACKUP_EXE\n"
        "$expectedHash = $env:WG_EXPECTED_SHA256.ToLower()\n"
        "$actualHash = (Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash.ToLower()\n"
        "if ($actualHash -ne $expectedHash) { exit 21 }\n"
        "if ($mode -eq 'installer') {\n"
        "  $argsList = @('/SP-', '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS', \"/LOG=$($env:WG_INSTALL_LOG)\")\n"
        "  if ($env:WG_SILENT_INSTALL -eq '1') { $argsList += '/VERYSILENT' }\n"
        "  $p = Start-Process -FilePath $staged -ArgumentList $argsList -Wait -PassThru\n"
        "  if ($p.ExitCode -ne 0) {\n"
        "    if ($target -and (-not (Test-Path -LiteralPath $target)) -and (Test-Path -LiteralPath $backup)) {\n"
        "      Copy-Item -LiteralPath $backup -Destination $target -Force -ErrorAction SilentlyContinue\n"
        "    }\n"
        "    if ($target -and (Test-Path -LiteralPath $target)) {\n"
        "      Start-Process -FilePath $target\n"
        "    }\n"
        "    exit $p.ExitCode\n"
        "  }\n"
        "  if ($env:WG_SILENT_INSTALL -eq '1' -and $target -and (Test-Path -LiteralPath $target)) {\n"
        "    Start-Process -FilePath $target\n"
        "  }\n"
        "} else {\n"
        "  $prev = \"$target.prev\"\n"
        "  try {\n"
        "    if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination $prev -Force }\n"
        "    Move-Item -LiteralPath $staged -Destination $target -Force\n"
        "    $chk = Start-Process -FilePath $target -ArgumentList '--version' -Wait -PassThru -WindowStyle Hidden\n"
        "    if ($chk.ExitCode -ne 0) { throw 'Version smoke check failed' }\n"
        "    Remove-Item -LiteralPath $prev -Force -ErrorAction SilentlyContinue\n"
        "    Start-Process -FilePath $target\n"
        "  } catch {\n"
        "    if (Test-Path -LiteralPath $prev) { Move-Item -LiteralPath $prev -Destination $target -Force }\n"
        "    if (Test-Path -LiteralPath $target) { Start-Process -FilePath $target }\n"
        "    exit 22\n"
        "  }\n"
        "}\n"
    )


def apply_verified_update(
    pkg: VerifiedUpdatePackage,
    *,
    current_exe: Optional[Path] = None,
    root: Optional[Path] = None,
    silent_installer: bool = False,
    spawn_detached: bool = True,
) -> Dict[str, Any]:
    """Re-verify `pkg`, record rollback snapshot, and launch the update supervisor."""
    if not pkg.file_path.is_file():
        raise UpdateError(f"Verified package missing on disk: {pkg.file_path}")
    actual_sha = sha256_file(pkg.file_path)
    if actual_sha != pkg.verification.sha256:
        raise UpdateTamperedError("Staged package hash changed after verification (TOCTOU detected)!")

    base = root or data_dir()
    rb_state = prepare_rollback_snapshot(
        target_version=pkg.version,
        install_mode=pkg.install_mode,
        current_exe=current_exe,
        root=base,
    )

    if os.environ.get("WINGENIUS_NO_LAUNCH") == "1" or not spawn_detached:
        return {
            "ok": True,
            "launched": False,
            "mode": pkg.install_mode,
            "staged_path": str(pkg.file_path),
            "rollback_state": rb_state,
        }

    if not IS_WINDOWS:
        raise UpdateError(
            "Self-installing Windows .exe updates can only execute on Windows. "
            f"Verified package saved at: {pkg.file_path}"
        )

    script_path = staging_dir(base) / "apply_update_supervisor.ps1"
    script_path.write_text(_build_windows_supervisor_script(), encoding="utf-8")
    ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
    target_exe = rb_state.get("target_exe_path") or (str(Path(sys.executable).resolve()) if IS_FROZEN else "")
    install_log = str((logs_dir() if root is None else (base / "logs")) / f"installer-{pkg.version}.log")
    env = dict(
        os.environ,
        WG_PARENT_PID=str(os.getpid()),
        WG_UPDATE_MODE="installer" if pkg.install_mode == "installer" else "swap",
        WG_STAGED_EXE=str(pkg.file_path),
        WG_TARGET_EXE=str(target_exe),
        WG_BACKUP_EXE=str(rb_state.get("backup_exe_path") or ""),
        WG_EXPECTED_SHA256=pkg.verification.sha256,
        WG_INSTALL_LOG=install_log,
        WG_SILENT_INSTALL="1" if silent_installer else "0",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
        subprocess, "DETACHED_PROCESS", 0x00000008
    )
    subprocess.Popen(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
        env=env,
        close_fds=True,
        creationflags=flags,
    )
    return {
        "ok": True,
        "launched": True,
        "mode": pkg.install_mode,
        "staged_path": str(pkg.file_path),
        "rollback_state": rb_state,
    }


def rollback_previous_version(
    *,
    target_exe: Optional[Path] = None,
    restore_data: bool = False,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Restore the backed-up previous executable (and optionally profile data) safely."""
    base = root or data_dir()
    state = get_rollback_state(base)
    if not state:
        raise UpdateError("No rollback snapshot is available.")

    backup_exe = Path(str(state.get("backup_exe_path") or ""))
    expected_sha = str(state.get("backup_exe_sha256") or "").lower()
    dest_exe = target_exe or (Path(str(state.get("target_exe_path") or "")) if state.get("target_exe_path") else None)

    restored_exe = ""
    if backup_exe.is_file() and dest_exe is not None:
        if expected_sha and sha256_file(backup_exe) != expected_sha:
            raise UpdateTamperedError("Rollback executable backup failed SHA-256 integrity verification!")
        dest_exe.parent.mkdir(parents=True, exist_ok=True)
        # If replacing the currently running frozen Windows binary, schedule an out-of-process swap;
        # otherwise copy directly.
        if IS_WINDOWS and IS_FROZEN and dest_exe.resolve() == Path(sys.executable).resolve():
            staged_rb = staging_dir(base) / f"rollback-{state.get('previous_version', 'prev')}.exe"
            shutil.copy2(backup_exe, staged_rb)
            script_path = staging_dir(base) / "rollback_supervisor.ps1"
            script_path.write_text(_build_windows_supervisor_script(), encoding="utf-8")
            ps = shutil.which("powershell.exe") or "powershell.exe"
            env = dict(
                os.environ,
                WG_PARENT_PID=str(os.getpid()),
                WG_UPDATE_MODE="swap",
                WG_STAGED_EXE=str(staged_rb),
                WG_TARGET_EXE=str(dest_exe),
                WG_BACKUP_EXE=str(backup_exe),
                WG_EXPECTED_SHA256=expected_sha or sha256_file(staged_rb),
                WG_INSTALL_LOG="",
                WG_SILENT_INSTALL="1",
            )
            if os.environ.get("WINGENIUS_NO_LAUNCH") != "1":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
                    subprocess, "DETACHED_PROCESS", 0x00000008
                )
                subprocess.Popen(
                    [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
                    env=env,
                    close_fds=True,
                    creationflags=flags,
                )
            restored_exe = str(dest_exe)
        else:
            tmp_dest = dest_exe.with_name(dest_exe.name + ".rb_tmp")
            shutil.copy2(backup_exe, tmp_dest)
            os.replace(tmp_dest, dest_exe)
            restored_exe = str(dest_exe)

    restored_data = False
    data_backup_dir = Path(str(state.get("data_backup_dir") or ""))
    if restore_data and data_backup_dir.is_dir():
        from .upgrade import safe_copy_sqlite

        for item in data_backup_dir.iterdir():
            if item.name == "updates" or item.is_symlink():
                continue
            dst_item = base / item.name
            if item.is_dir():
                shutil.copytree(item, dst_item, symlinks=False, dirs_exist_ok=True)
            elif item.suffix == ".sqlite3":
                safe_copy_sqlite(item, dst_item)
            else:
                shutil.copy2(item, dst_item)
        restored_data = True

    if not restored_exe and not restored_data:
        raise UpdateError("Rollback snapshot contains neither a restorable executable nor profile backup.")

    state["status"] = "rolled_back"
    state["rolled_back_at"] = int(time.time())
    atomic_json_write(rollback_state_path(base), state)
    return {
        "ok": True,
        "previous_version": str(state.get("previous_version") or ""),
        "restored_exe": restored_exe,
        "restored_data": restored_data,
    }


def confirm_post_update_health(
    *,
    running_version: str = APP_VERSION,
    root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a pending update as `verified` once the target version boots cleanly."""
    base = root or data_dir()
    state = get_rollback_state(base)
    if not state or state.get("status") != "pending":
        return state
    if str(state.get("target_version") or "").lstrip("vV") == running_version.lstrip("vV"):
        state["status"] = "verified"
        state["verified_at"] = int(time.time())
        atomic_json_write(rollback_state_path(base), state)
    return state
