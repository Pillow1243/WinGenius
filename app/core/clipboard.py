"""Clipboard intelligence: look at what was just copied and offer the right tool."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

URL_RE = re.compile(r"^https?://\S+$", re.I)
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$", re.I)
JWT_RE = re.compile(r"^ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$")


def suggestion_for_text(text: str) -> Optional[Tuple[str, str]]:
    """(tool_id, short kind) for a piece of clipboard text."""
    body = (text or "").strip()
    if not body or len(body) > 200_000:
        return None
    first = body.splitlines()[0]
    if JWT_RE.match(first[:2000]):
        return "jwt_decode", "JWT"
    if URL_RE.match(first):
        return "url_tool", "URL"
    if EMAIL_RE.match(first):
        return "text_extract", "email"
    if HEX_RE.match(first):
        return "color_convert", "color"
    if IP_RE.match(first):
        return "dns_lookup", "IP"
    if body.startswith("{") or body.startswith("["):
        return "json_tool", "JSON"
    if BASE64_RE.match(first[:240]) and " " not in first:
        return "base64_tool", "Base64"
    if path_like(body):
        paths = [line.strip().strip('"') for line in body.splitlines() if line.strip()]
        return suggestion_for_paths(paths) or ("file_hash", "path")
    if len(body) > 180 and " " in body:
        return "text_summarize", "text"
    return None


def path_like(text: str) -> bool:
    lines = [line.strip().strip('"') for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 50:
        return False
    return all(Path(line.replace("\\", "/")).suffix for line in lines)


def suggestion_for_paths(paths: List[str]) -> Optional[Tuple[str, str]]:
    from .suggest import tools_for_files

    if not paths:
        return None
    ranked = tools_for_files(paths, limit=1)
    if not ranked:
        return None
    return ranked[0], "files"


class ClipboardWatcher(QObject):
    """Emits (tool_id, label, payload) whenever something useful is copied."""

    suggested = Signal(str, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._clipboard = None
        self._last = ""
        self._enabled = True

    # ------------------------------------------------------------------ setup
    def attach(self, clipboard) -> None:
        self._clipboard = clipboard
        try:
            clipboard.dataChanged.connect(self._on_changed)
        except Exception:  # pragma: no cover - offscreen platforms
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)
        if not self._enabled:
            self._last = ""

    # ----------------------------------------------------------------- events
    def _on_changed(self) -> None:
        if not self._enabled or self._clipboard is None:
            return
        try:
            mime = self._clipboard.mimeData()
        except Exception:
            return
        try:
            if mime.hasImage():
                payload = "__image__"
                if payload != self._last:
                    self._last = payload
                    self.suggested.emit("image_ocr", "image", payload)
                return
            if mime.hasUrls():
                paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
                hint = suggestion_for_paths(paths)
                if hint:
                    key = "|".join(paths[:3])
                    if key != self._last:
                        self._last = key
                        tool_id, label = hint
                        self.suggested.emit(tool_id, label, "\n".join(paths))
                return
            text = self._clipboard.text()
        except Exception:
            return
        if not text or text == self._last:
            return
        self._last = text
        hint = suggestion_for_text(text)
        if hint:
            tool_id, label = hint
            if not label:
                return
            self.suggested.emit(tool_id, label, text[:4000])
