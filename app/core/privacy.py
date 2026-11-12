"""Secret-aware JSON persistence. Runtime inputs are never mutated.

This is a persistence boundary, not a DLP engine: arbitrary prose, paths and
user-authored macros may still contain private information. Passwords must be
supplied again when replaying a saved job/recipe; this module does not silently
store credentials or invent a replacement password.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_SENSITIVE = re.compile(
    r"password|passwd|passphrase|secret|token|authorization|credential|api[_-]?key|private[_-]?key|رمز",
    re.IGNORECASE,
)
# Needed before the registry has loaded (startup migrations).
_BUILTIN_PRIVATE = {"text_encrypt": {"text"}, "vault_add": {"note_text"}}
PROFILE_JSON = {"history.json", "workflows.json", "schedules.json", "watch_rules.json"}


def sensitive_keys(tool_id: str = "") -> set[str]:
    keys = {"confirm", "confirmation"} | _BUILTIN_PRIVATE.get(tool_id, set())
    # Never import tool modules or require QApplication to sanitise a profile.
    from .registry import get
    spec = get(tool_id)
    if spec:
        keys.update(f.key for f in spec.fields if f.kind == "password" or f.sensitive)
    return keys


def is_sensitive(key: str, tool_id: str = "") -> bool:
    return key in sensitive_keys(tool_id) or bool(_SENSITIVE.search(key))


def sanitize_values(tool_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """Drop sensitive keys recursively; preserve safe values and placeholders."""
    return {str(k): sanitize_document(v) for k, v in values.items()
            if str(k) not in {"_macro_snapshot", "_flow_snapshot"} and not is_sensitive(str(k), tool_id)}


def secret_strings(tool_id: str, values: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key, value in values.items():
        if is_sensitive(str(key), tool_id):
            if isinstance(value, str) and value:
                found.append(value)
        elif isinstance(value, dict):
            found.extend(secret_strings("", value))
    return found


_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(bearer|x-wingenius-token)\s*[:=]\s*([^\s,;\"']+)"),
    re.compile(r"(?i)\b(password|passwd|passphrase|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*([:=])\s*([^\s,;\"']+)"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\b(AIza[0-9A-Za-z_-]{32,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
)
_USER_HOME_RE = re.compile(
    r"(?:[A-Za-z]:\\Users\\[^\\\s\"']+)|(?:/home/[^/\s\"']+)|(?:/Users/[^/\s\"']+)"
)


def scrub_text(text: str, *, scrub_paths: bool = False) -> str:
    """Redact credential tokens and key=secret pairs; optionally anonymize user home paths."""
    if not text:
        return ""
    out = str(text)
    out = _SECRET_VALUE_PATTERNS[0].sub(r"\1: [REDACTED]", out)
    out = _SECRET_VALUE_PATTERNS[1].sub(r"\1\2[REDACTED]", out)
    for pattern in _SECRET_VALUE_PATTERNS[2:]:
        out = pattern.sub("[REDACTED]", out)
    if scrub_paths:
        out = _USER_HOME_RE.sub("<home>", out)
    return out


def redact_known(text: str, secrets: list[str]) -> str:
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def sanitize_document(value: Any) -> Any:
    """Scrub structured profiles, including schedules and watcher payloads."""
    if isinstance(value, (list, tuple)):
        return [sanitize_document(item) for item in value]
    if not isinstance(value, dict):
        return value
    tool_id = str(value.get("tool_id") or value.get("target_id") or "")
    clean = {}
    secrets = []
    for key, item in value.items():
        if key in {"values", "payload"} and isinstance(item, dict):
            secrets.extend(secret_strings(tool_id, item))
            clean[key] = sanitize_values(tool_id, item)
        elif not is_sensitive(str(key)):
            clean[key] = sanitize_document(item)
    if secrets:
        for key in ("message", "error", "last_status"):
            if isinstance(clean.get(key), str):
                clean[key] = redact_known(clean[key], secrets)
    return clean


def atomic_json_write(path: Path, payload: Any) -> None:
    """Unique, private temp file + replace; failures keep the previous file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic rename prevents torn reads; the sidecar lock also serializes
    # competing cooperating writers so a slow writer cannot interleave its
    # replacement with another profile/database metadata write.
    from .resource_lock import resource_lock

    with resource_lock(path):
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            if os.name != "nt":
                try:
                    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    # Some filesystems do not support directory fsync. The
                    # file itself was already fsynced before the atomic rename.
                    pass
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                # os.replace already consumed the temp path on success.
                pass


def scrub_profile_file(path: Path) -> bool:
    """Rewrite a known JSON profile in place only when sanitisation changes it."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    clean = sanitize_document(raw)
    if clean != raw:
        atomic_json_write(path, clean)
        return True
    return False
