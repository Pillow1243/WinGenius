"""System-tray presence so the assistant is always one click away."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.paths import APP_NAME
from ..core.logger import get_logger

log = get_logger("tray")


def tray_available() -> bool:
    try:
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:  # pragma: no cover - offscreen / headless
        return False


class Tray(QObject):
    """Thin wrapper: the window connects signals, the tray never owns logic."""

    openRequested = Signal()
    paletteRequested = Signal()
    planRequested = Signal()
    jobsRequested = Signal()
    automationRequested = Signal()
    quitRequested = Signal()

    def __init__(self, icon_path: Optional[Path] = None, parent=None) -> None:
        super().__init__(parent)
        self.available = tray_available()
        self._icon: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None
        self.lang = "fa"
        if not self.available:
            return
        icon = QIcon(str(icon_path)) if icon_path and Path(icon_path).exists() else QIcon()
        self._icon = QSystemTrayIcon(icon, self)
        self._menu = QMenu()
        self._rebuild_menu()
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._icon.show()

    # ------------------------------------------------------------------ menu
    def _rebuild_menu(self) -> None:
        if self._menu is None:
            return
        fa = self.lang != "en"
        self._menu.clear()
        actions = [
            (("باز کردن پنجره" if fa else "Open window"), self.openRequested),
            (("دستیار / جست‌وجو" if fa else "Search palette"), self.paletteRequested),
            (("دستیار برنامه‌ریز" if fa else "Planner"), self.planRequested),
            (("صف کارها" if fa else "Job queue"), self.jobsRequested),
            (("خودکارسازی پوشه" if fa else "Folder automation"), self.automationRequested),
        ]
        for text, signal in actions:
            action = self._menu.addAction(text)
            action.triggered.connect(lambda _checked=False, sig=signal: sig.emit())
        self._menu.addSeparator()
        quit_action = self._menu.addAction("خروج" if fa else "Quit")
        quit_action.triggered.connect(lambda: self.quitRequested.emit())

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self._rebuild_menu()

    # ----------------------------------------------------------------- events
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.openRequested.emit()

    def notify(self, title: str, message: str) -> None:
        if self._icon is not None:
            try:
                self._icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)
            except Exception:  # pragma: no cover
                pass

    def hide(self) -> None:
        if self._icon is not None:
            self._icon.hide()

    @property
    def icon(self):
        return self._icon
