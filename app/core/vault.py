"""Encrypted vault — a single portable file that holds many secrets/files.

Two container formats are supported:

**v2 (current, what this module writes)**

    magic   b"WGV2"
    kdf     uint8              1 = scrypt
    log_n   uint8              scrypt cost (15 → N = 32768, ≈32 MB)
    r       uint32 big endian  scrypt block size
    p       uint32 big endian  scrypt parallelisation
    salt    16 bytes
    nonce   12 bytes           AES-GCM nonce for the index blob
    length  uint32 big endian  ciphertext length of the index blob
    index   ciphertext         {"files": [{name, size, mtime, nonce, data}]}

The key comes from **scrypt** (memory-hard, so a GPU cannot spray guesses the way
it can against PBKDF2) and the whole header is fed to AES-GCM as *associated
data* — editing `log_n` or the salt now fails authentication instead of quietly
changing how the key is derived.  Each payload is additionally bound to its entry
name, so ciphertext cannot be swapped between entries.

**v1 (legacy, still readable)**

    b"WGV1" | salt16 | iterations(uint32) | nonce12 | length(uint32) | ciphertext

Old vaults keep working untouched; ``upgrade()`` (or any ``change_password()``)
rewrites them into v2 after proving the password.  Adding or removing an entry
rewrites the container atomically (tmp file + replace), so a crash can never
leave a half-written vault behind.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MAGIC_V1 = b"WGV1"
MAGIC_V2 = b"WGV2"
MAGIC = MAGIC_V2

FORMAT_V1 = 1
FORMAT_V2 = 2

KDF_SCRYPT = 1

DEFAULT_ITERATIONS = 240_000      # v1 / PBKDF2 only
MIN_ITERATIONS = 10_000           # hard floor: anything below is a damaged header
MAX_ITERATIONS = 5_000_000        # ceiling: keeps a hostile header from freezing the app

SCRYPT_LOG_N = 15                 # N = 32768 → ~32 MB per guess
SCRYPT_R = 8
SCRYPT_P = 1
MIN_LOG_N = 12                    # refuse cheap headers (a hostile file could ask for N=2)
MAX_LOG_N = 20                    # ...and refuse ones that would freeze the machine
MAX_R, MAX_P = 16, 4
MAX_SCRYPT_MEMORY = 128 * 1024 * 1024
MAX_SCRYPT_WORK = (1 << 17) * 8 * 4

SALT_LEN = 16
NONCE_LEN = 12

V1_AAD = MAGIC_V1
V2_AAD_PREFIX = b"WinGenius vault v2|"


class VaultError(Exception):
    """Anything the user should see as a friendly message."""


# --------------------------------------------------------------------- crypto
def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:                              # pragma: no cover
        raise VaultError("cryptography is not installed — pip install cryptography") from exc
    return AESGCM


def _crypto():
    """Kept for backwards compatibility with older call sites."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except Exception as exc:                              # pragma: no cover
        raise VaultError("cryptography is not installed — pip install cryptography") from exc
    return _aesgcm(), hashes, PBKDF2HMAC


def derive_key(password: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    """PBKDF2 key — used by the legacy v1 container only."""
    _, hashes, PBKDF2HMAC = _crypto()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def derive_key_scrypt(password: str, salt: bytes, log_n: int = SCRYPT_LOG_N,
                      r: int = SCRYPT_R, p: int = SCRYPT_P) -> bytes:
    """Memory-hard key derivation used by v2 containers."""
    try:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except Exception as exc:                              # pragma: no cover
        raise VaultError("cryptography is not installed — pip install cryptography") from exc
    if not (MIN_LOG_N <= int(log_n) <= MAX_LOG_N and 1 <= r <= MAX_R and 1 <= p <= MAX_P):
        raise VaultError("vault header is damaged")
    n = 1 << int(log_n)
    if 128 * n * r > MAX_SCRYPT_MEMORY or n * r * p > MAX_SCRYPT_WORK:
        raise VaultError("vault key-derivation cost exceeds the safe memory/time budget")
    try:
        return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))
    except (ValueError, MemoryError) as exc:
        raise VaultError("vault header asks for an impossible key-derivation cost") from exc


def _safe_entry_name(name: str) -> str:
    """Reduce any user-supplied name to a single, harmless file name.

    Vault entries used to be written straight into ``out_dir / entry["name"]``;
    a name like ``../../Desktop/payload.exe`` therefore escaped the destination
    folder on extraction.  Names are stored as data, never as paths.
    """
    cleaned = str(name or "").replace("\\", "/").strip().strip("/")
    cleaned = Path(cleaned).name
    cleaned = cleaned.strip(" .") or "entry.bin"
    if ":" in cleaned or "\x00" in cleaned or len(cleaned) > 200:
        cleaned = cleaned.replace(":", "_").replace("\x00", "")[:200] or "entry.bin"
    return cleaned


def human_size(num: float, isolate: bool = True) -> str:
    """Bytes as "12.3 MB".

    The number and its unit are wrapped in Unicode isolates so the pair keeps its
    left-to-right order when it is embedded in a Persian (right-to-left) sentence.
    """
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            text = f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
            return f"\u2066{text}\u2069" if isolate else text
        num /= 1024.0
    text = f"{num:.1f} GB"
    return f"\u2066{text}\u2069" if isolate else text


class Vault:
    """Create, inspect and modify a `.wgv` container."""

    def __init__(self, path: Path, password: str = "", iterations: int = DEFAULT_ITERATIONS,
                 log_n: int = SCRYPT_LOG_N) -> None:
        self.path = Path(path)
        self.password = password
        self.iterations = max(MIN_ITERATIONS, min(MAX_ITERATIONS, int(iterations)))
        self.log_n = max(MIN_LOG_N, min(MAX_LOG_N, int(log_n)))
        self.format = FORMAT_V2            # what a brand-new vault is written as
        self.r, self.p = SCRYPT_R, SCRYPT_P
        self._entries: Optional[List[Dict[str, Any]]] = None
        self._salt: bytes = b""
        self._key: bytes = b""

    # ------------------------------------------------------------------ basics
    def exists(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > len(MAGIC) + SALT_LEN + 20

    def unlocked(self) -> bool:
        return bool(self._key)

    def format_version(self) -> int:
        return int(self.format)

    def kdf_name(self) -> str:
        return "scrypt" if self.format == FORMAT_V2 else "PBKDF2-SHA256"

    def strength(self) -> str:
        if self.format == FORMAT_V2:
            return f"scrypt N=2^{self.log_n} r={self.r} p={self.p}"
        return f"PBKDF2-SHA256 {self.iterations:,}"

    # ------------------------------------------------------------------ header
    def _header_v2(self, salt: bytes, nonce: bytes, length: int) -> bytes:
        return (MAGIC_V2 + struct.pack(">BBII", KDF_SCRYPT, self.log_n, self.r, self.p)
                + salt + nonce + struct.pack(">I", length))

    def _aad_v2(self, header_without_nonce: bytes) -> bytes:
        # everything that decides *how* the key is derived is authenticated
        return V2_AAD_PREFIX + header_without_nonce

    def _entry_aad(self, name: str) -> bytes:
        if self.format == FORMAT_V2:
            return b"WinGenius vault v2|entry|" + name.encode("utf-8")
        return V1_AAD

    # ------------------------------------------------------------------- write
    def create(self) -> None:
        if self.exists():
            raise VaultError("vault already exists")
        self.format = FORMAT_V2
        self._salt = os.urandom(SALT_LEN)
        self._key = derive_key_scrypt(self.password, self._salt, self.log_n, self.r, self.p)
        self._entries = []
        self._flush()

    def _flush(self) -> None:
        AESGCM = _aesgcm()
        if self._entries is None:
            self._entries = []
        if not self._key:                                # writing a not-yet-unlocked vault
            self.unlock()
        blob = json.dumps({"files": self._entries}, ensure_ascii=False).encode("utf-8")
        nonce = os.urandom(NONCE_LEN)
        if self.format == FORMAT_V2:
            header = self._header_v2(self._salt, b"", 0)[:-4]        # without length
            aad = self._aad_v2(header)
            ciphertext = AESGCM(self._key).encrypt(nonce, blob, aad)
            payload = (header + nonce + struct.pack(">I", len(ciphertext)) + ciphertext)
        else:                                            # legacy container
            ciphertext = AESGCM(self._key).encrypt(nonce, blob, V1_AAD)
            payload = (MAGIC_V1 + self._salt + struct.pack(">I", self.iterations)
                       + nonce + struct.pack(">I", len(ciphertext)) + ciphertext)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            if os.name != "nt":
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            if os.name != "nt":
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    # -------------------------------------------------------------------- read
    def unlock(self) -> List[Dict[str, Any]]:
        if not self.exists():
            raise VaultError("vault not found")
        raw = self.path.read_bytes()
        if raw.startswith(MAGIC_V2):
            entries = self._unlock_v2(raw)
        elif raw.startswith(MAGIC_V1):
            entries = self._unlock_v1(raw)
        else:
            raise VaultError("not a WinGenius vault")
        self._entries = entries
        return self._entries

    def _unlock_v1(self, raw: bytes) -> List[Dict[str, Any]]:
        offset = len(MAGIC_V1)
        salt = raw[offset:offset + SALT_LEN]
        offset += SALT_LEN
        if len(raw) < offset + 4 + NONCE_LEN + 4:
            raise VaultError("vault file is truncated")
        iterations = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        if not (MIN_ITERATIONS <= iterations <= MAX_ITERATIONS):
            raise VaultError("vault header is damaged")
        nonce = raw[offset:offset + NONCE_LEN]
        offset += NONCE_LEN
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        ciphertext = raw[offset:offset + length]
        if len(ciphertext) != length:
            raise VaultError("vault file is truncated")
        key = derive_key(self.password, salt, iterations)
        try:
            plain = _aesgcm()(key).decrypt(nonce, ciphertext, V1_AAD)
        except Exception as exc:                          # InvalidTag / short read
            raise VaultError("wrong password or damaged vault") from exc
        self._salt, self._key = salt, key
        self.iterations = iterations        # adopt the container's own cost factor
        self.format = FORMAT_V1
        return self._parse_index(plain)

    def _unlock_v2(self, raw: bytes) -> List[Dict[str, Any]]:
        offset = len(MAGIC_V2)
        if len(raw) < offset + 10 + SALT_LEN + NONCE_LEN + 4:
            raise VaultError("vault file is truncated")
        kdf, log_n, r, p = struct.unpack(">BBII", raw[offset:offset + 10])
        offset += 10
        if kdf != KDF_SCRYPT:
            raise VaultError("vault uses an unknown key-derivation function")
        if not (MIN_LOG_N <= log_n <= MAX_LOG_N) or not (1 <= r <= MAX_R) or not (1 <= p <= MAX_P):
            raise VaultError("vault header is damaged")
        header = raw[:offset]                            # magic … p (the AAD prefix)
        salt = raw[offset:offset + SALT_LEN]
        offset += SALT_LEN
        header += salt
        nonce = raw[offset:offset + NONCE_LEN]
        offset += NONCE_LEN
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        ciphertext = raw[offset:offset + length]
        if len(ciphertext) != length:
            raise VaultError("vault file is truncated")
        key = derive_key_scrypt(self.password, salt, log_n, r, p)
        try:
            plain = _aesgcm()(key).decrypt(nonce, ciphertext, self._aad_v2(header))
        except Exception as exc:
            raise VaultError("wrong password or damaged vault") from exc
        self._salt, self._key = salt, key
        self.log_n, self.r, self.p = log_n, r, p
        self.format = FORMAT_V2
        return self._parse_index(plain)

    @staticmethod
    def _parse_index(plain: bytes) -> List[Dict[str, Any]]:
        try:
            data = json.loads(plain.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise VaultError("vault index is damaged") from exc
        entries = data.get("files", [])
        if not isinstance(entries, list):
            raise VaultError("vault index is damaged")
        return [entry for entry in entries if isinstance(entry, dict) and "name" in entry]

    def entries(self) -> List[Dict[str, Any]]:
        if self._entries is None:
            self.unlock()
        return list(self._entries or [])

    def names(self) -> List[str]:
        return [entry["name"] for entry in self.entries()]

    def info(self) -> Dict[str, Any]:
        entries_ = self.entries()
        total = sum(int(entry.get("size", 0)) for entry in entries_)
        return {"entries": len(entries_), "bytes": total,
                "size": self.path.stat().st_size if self.path.is_file() else 0,
                "format": self.format, "kdf": self.kdf_name(),
                "strength": self.strength(), "log_n": self.log_n,
                "iterations": self.iterations}

    # -------------------------------------------------------------- migration
    def upgrade(self) -> str:
        """Rewrite a legacy (v1) vault as v2 — after proving the password.

        Every payload is decrypted and re-encrypted with the scrypt key, so a
        half-upgraded container is impossible: the file is replaced atomically.
        """
        if self._entries is None:
            self.unlock()
        if self.format == FORMAT_V2:
            return "already-v2"
        AESGCM = _aesgcm()
        old_key, old_format = self._key, self.format
        salt = os.urandom(SALT_LEN)
        new_key = derive_key_scrypt(self.password, salt, self.log_n, self.r, self.p)
        rebuilt: List[Dict[str, Any]] = []
        for entry in self.entries():
            try:
                plain = AESGCM(old_key).decrypt(base64.b64decode(entry["nonce"]),
                                                base64.b64decode(entry["data"]), V1_AAD)
            except Exception as exc:
                raise VaultError(f"cannot decrypt {entry['name']}") from exc
            nonce = os.urandom(NONCE_LEN)
            updated = dict(entry)
            updated["nonce"] = base64.b64encode(nonce).decode()
            updated["data"] = base64.b64encode(
                AESGCM(new_key).encrypt(nonce, plain, b"WinGenius vault v2|entry|"
                                        + entry["name"].encode("utf-8"))).decode()
            rebuilt.append(updated)
        self._entries = rebuilt
        self._salt, self._key = salt, new_key
        self.format = FORMAT_V2
        assert old_format == FORMAT_V1
        self._flush()
        return "upgraded"

    # ------------------------------------------------------------------- CRUD
    def _unique_name(self, name: str) -> str:
        existing = {entry["name"] for entry in (self._entries or [])}
        if name not in existing:
            return name
        stem, suffix = Path(name).stem, Path(name).suffix
        counter = 2
        while f"{stem}-{counter}{suffix}" in existing:
            counter += 1
        return f"{stem}-{counter}{suffix}"

    def _put(self, name: str, data: bytes, mtime: float) -> str:
        nonce = os.urandom(NONCE_LEN)
        entry_name = self._unique_name(_safe_entry_name(name))
        ciphertext = _aesgcm()(self._key).encrypt(nonce, data, self._entry_aad(entry_name))
        self._entries = [entry for entry in (self._entries or []) if entry["name"] != entry_name]
        self._entries.append({
            "name": entry_name, "size": len(data), "mtime": int(mtime), "added": time.time(),
            "nonce": base64.b64encode(nonce).decode(),
            "data": base64.b64encode(ciphertext).decode(),
        })
        return entry_name

    def add(self, files: Iterable[Path], name: Optional[str] = None) -> List[str]:
        if not self.unlocked():
            self.unlock()
        items = [Path(item) for item in files]
        added: List[str] = []
        for path in items:
            if not path.is_file():
                continue
            custom = name if (name and len(items) == 1) else None
            added.append(self._put(custom or path.name, path.read_bytes(), path.stat().st_mtime))
        if added:
            self._flush()
        return added

    def add_text(self, name: str, text: str) -> str:
        if not self.unlocked():
            self.unlock()
        entry_name = self._put(name, text.encode("utf-8"), time.time())
        self._flush()
        return entry_name

    def _entry(self, name: str) -> Optional[Dict[str, Any]]:
        for entry in self.entries():
            if entry["name"] == name:
                return entry
        return None

    def extract(self, names: Iterable[str], out_dir: Path) -> List[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        for name in names:
            entry = self._entry(name)
            if entry is None:
                continue
            nonce = base64.b64decode(entry["nonce"])
            ciphertext = base64.b64decode(entry["data"])
            try:
                plain = _aesgcm()(self._key).decrypt(nonce, ciphertext,
                                                     self._entry_aad(entry["name"]))
            except Exception as exc:
                raise VaultError(f"cannot decrypt {name}") from exc
            root = out_dir.resolve()
            safe_base = _safe_entry_name(entry["name"])
            target = (root / safe_base).resolve()
            if target == root or root not in target.parents:
                raise VaultError(f"unsafe entry name in vault: {entry['name']!r}")
            stem, suffix = Path(safe_base).stem, Path(safe_base).suffix
            counter = 2
            while target.exists() or target.is_symlink():
                candidate = (root / f"{stem}-{counter}{suffix}").resolve()
                if candidate == root or root not in candidate.parents:
                    raise VaultError(f"unsafe collision target in vault: {entry['name']!r}")
                target = candidate
                counter += 1
            target.write_bytes(plain)
            written.append(target)
        return written

    def extract_all(self, out_dir: Path) -> List[Path]:
        return self.extract(self.names(), out_dir)

    def remove(self, names: Iterable[str]) -> int:
        targets = set(names)
        before = len(self.entries())
        self._entries = [entry for entry in self.entries() if entry["name"] not in targets]
        removed = before - len(self._entries)
        if removed:
            self._flush()
        return removed

    def change_password(self, new_password: str) -> None:
        """Re-key the vault: every payload is decrypted and re-encrypted.

        The container is rewritten in the current (v2) format, so changing the
        password also upgrades an old vault.
        """
        if self._entries is None:
            self.unlock()
        AESGCM = _aesgcm()
        old_key, old_format = self._key, self.format
        salt = os.urandom(SALT_LEN)
        new_key = derive_key_scrypt(new_password, salt, self.log_n, self.r, self.p)
        rebuilt: List[Dict[str, Any]] = []
        for entry in self.entries():
            old_aad = V1_AAD if old_format == FORMAT_V1 else b""
            try:
                if old_format == FORMAT_V1:
                    plain = AESGCM(old_key).decrypt(base64.b64decode(entry["nonce"]),
                                                    base64.b64decode(entry["data"]), old_aad)
                else:
                    plain = AESGCM(old_key).decrypt(
                        base64.b64decode(entry["nonce"]), base64.b64decode(entry["data"]),
                        b"WinGenius vault v2|entry|" + entry["name"].encode("utf-8"))
            except Exception as exc:
                raise VaultError(f"cannot decrypt {entry['name']}") from exc
            nonce = os.urandom(NONCE_LEN)
            updated = dict(entry)
            updated["nonce"] = base64.b64encode(nonce).decode()
            updated["data"] = base64.b64encode(AESGCM(new_key).encrypt(
                nonce, plain, b"WinGenius vault v2|entry|" + entry["name"].encode("utf-8"))).decode()
            rebuilt.append(updated)
        self._entries = rebuilt
        self.password = new_password
        self._salt, self._key = salt, new_key
        self.format = FORMAT_V2
        self._flush()

    def verify(self) -> Dict[str, Any]:
        """Decrypt everything to prove the vault is healthy."""
        AESGCM = _aesgcm()
        checked = 0
        problems: List[str] = []
        for entry in self.entries():
            try:
                plain = AESGCM(self._key).decrypt(
                    base64.b64decode(entry["nonce"]), base64.b64decode(entry["data"]),
                    self._entry_aad(entry["name"]))
                if len(plain) != int(entry.get("size", len(plain))):
                    problems.append(f"{entry['name']}: size mismatch")
                checked += 1
            except Exception as exc:                      # noqa: BLE001
                problems.append(f"{entry['name']}: {exc}")
        return {"checked": checked, "problems": problems}


def export_backup(vault_path: Path, target: Path) -> Path:
    """Copy the vault file (already encrypted) for off-site backup."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_path, target)
    return target
