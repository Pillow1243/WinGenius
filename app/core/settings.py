"""Persistent user settings (portable ini file inside %APPDATA%\\WinGenius)."""
from __future__ import annotations

import configparser
from typing import Any, List

from .paths import default_output_dir, settings_file

try:
    from PySide6.QtCore import QSettings as _QSettings
except ImportError:  # pragma: no cover - headless fallback when PySide6 is absent
    _QSettings = None


class _IniFallback:
    """Minimal QSettings(IniFormat)-compatible backend for pure-Python CLI environments."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._cfg = configparser.RawConfigParser()
        try:
            self._cfg.read(path, encoding="utf-8")
        except OSError:
            pass

    def setFallbacksEnabled(self, _enabled: bool) -> None:
        return

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        if "/" in key:
            sec, opt = key.split("/", 1)
            return sec or "General", opt
        return "General", key

    def value(self, key: str, default: Any = None) -> Any:
        sec, opt = self._split(key)
        if not self._cfg.has_option(sec, opt):
            return default
        return self._cfg.get(sec, opt)

    def setValue(self, key: str, value: Any) -> None:
        sec, opt = self._split(key)
        if not self._cfg.has_section(sec):
            self._cfg.add_section(sec)
        if isinstance(value, (list, tuple)):
            self._cfg.set(sec, opt, ",".join(str(x) for x in value))
        else:
            self._cfg.set(sec, opt, str(value))

    def sync(self) -> None:
        from pathlib import Path

        target = Path(self._path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as fh:
                self._cfg.write(fh)
        except OSError:
            pass

_DEFAULTS: dict[str, Any] = {
    "ui/language": "fa",              # fa | en
    "ui/theme": "aurora",             # aurora | midnight | daylight
    "ui/accent": "#5B8CFF",
    "ui/animations": True,
    "ui/glow": True,
    "ui/confirm_danger": True,
    "ui/auto_check_updates": False,   # opt-in startup release check
    "ui/start_page": "home",
    "files/output_dir": str(default_output_dir()),
    "files/open_after_run": True,
    "index/ocr": True,                # OCR scanned PDFs while indexing
    "index/exclude_folders": "",      # newline/semicolon separated paths never indexed
    "app/first_run": True,
    "app/window_pos": "",
    "favorites/list": [],
    "assistant/history": [],
}


class Settings:
    """Thin, typed wrapper around QSettings with sane defaults."""

    def __init__(self) -> None:
        if _QSettings is not None:
            self._s = _QSettings(str(settings_file()), _QSettings.IniFormat)
            self._s.setFallbacksEnabled(False)
        else:
            self._s = _IniFallback(str(settings_file()))

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
