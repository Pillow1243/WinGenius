"""Opt-in release check.

WinGenius never phones home on its own.  This module is only reached when the
user explicitly runs ``--check-update``; everything else stays fully offline.
It asks GitHub for the latest published release and compares it with the local
``APP_VERSION``.  A failed request is reported, never retried or logged with
personal data.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple

from .paths import APP_VERSION

RELEASES_URL = "https://api.github.com/repos/Pillow1243/WinGenius/releases/latest"


def version_key(value: str) -> Tuple[int, ...]:
    """Turn ``v5.0.1`` into ``(5, 0, 1)`` so versions compare numerically."""
    parts: list[int] = []
    for chunk in str(value or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple((parts + [0, 0, 0])[:3])


def check_latest(timeout: float = 8.0, url: str = RELEASES_URL) -> Dict[str, Any]:
    """Return a small, serialisable result.  Never raises on network errors."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"WinGenius/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc), "current": APP_VERSION}

    latest = str(data.get("tag_name") or "").lstrip("vV")
    return {
        "ok": True,
        "current": APP_VERSION,
        "latest": latest,
        "name": str(data.get("name") or ""),
        "url": str(data.get("html_url") or "https://github.com/Pillow1243/WinGenius/releases"),
        "update_available": version_key(latest) > version_key(APP_VERSION),
    }
