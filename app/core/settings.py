"""Persistent user settings (portable ini file inside %APPDATA%\\WinGenius)."""
from __future__ import annotations

from typing import Any, List

from PySide6.QtCore import QSettings

from .paths import default_output_dir, settings_file

_DEFAULTS: dict[str, Any] = {
    "ui/language": "fa",              # fa | en
    "ui/theme": "aurora",             # aurora | midnight | daylight
    "ui/accent": "#5B8CFF",
    "ui/animations": True,
    "ui/glow": True,
    "ui/confirm_danger": True,
    "ui/start_page": "home",
    "files/output_dir": str(default_output_dir()),
    "files/open_after_run": True,
    "index/ocr": True,                # OCR scanned PDFs while indexing
    "app/first_run": True,
    "app/window_pos": "",
    "favorites/list": [],
    "assistant/history": [],
}


class Settings:
    """Thin, typed wrapper around QSettings with sane defaults."""

    def __init__(self) -> None:
        self._s = QSettings(str(settings_file()), QSettings.IniFormat)
        self._s.setFallbacksEnabled(False)

    # ---------------------------------------------------------------- generic
    def get(self, key: str, default: Any = None) -> Any:
        if default is None:
            default = _DEFAULTS.get(key)
        value = self._s.value(key, default)
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        self._s.setValue(key, value)
        self._s.sync()

    def bool(self, key: str) -> bool:
        return bool(self.get(key))

    # ---------------------------------------------------------------- helpers
    @property
    def lang(self) -> str:
        value = str(self.get("ui/language") or "fa")
        return value if value in ("fa", "en") else "fa"

    @lang.setter
    def lang(self, value: str) -> None:
        self.set("ui/language", "en" if value == "en" else "fa")

    @property
    def theme(self) -> str:
        return str(self.get("ui/theme") or "aurora")

    @theme.setter
    def theme(self, value: str) -> None:
        self.set("ui/theme", value)

    @property
    def accent(self) -> str:
        return str(self.get("ui/accent") or "#5B8CFF")

    @accent.setter
    def accent(self, value: str) -> None:
        self.set("ui/accent", value)

    @property
    def output_dir(self) -> str:
        value = str(self.get("files/output_dir") or default_output_dir())
        return value

    @output_dir.setter
    def output_dir(self, value: str) -> None:
        self.set("files/output_dir", value)

    @property
    def animations(self) -> bool:
        return bool(self.get("ui/animations"))

    @property
    def favorites(self) -> List[str]:
        raw = self.get("favorites/list") or []
        if isinstance(raw, str):
            raw = [x for x in raw.split(",") if x]
        return [str(x) for x in raw]

    def toggle_favorite(self, tool_id: str) -> bool:
        favs = self.favorites
        if tool_id in favs:
            favs.remove(tool_id)
            state = False
        else:
            favs.append(tool_id)
            state = True
        self.set("favorites/list", favs)
        return state

    def is_favorite(self, tool_id: str) -> bool:
        return tool_id in self.favorites

    def push_assistant_history(self, text: str) -> None:
        hist = self.get("assistant/history") or []
        if isinstance(hist, str):
            hist = [hist]
        hist = [h for h in hist if h != text]
        hist.insert(0, text)
        self.set("assistant/history", hist[:15])


settings = Settings()
