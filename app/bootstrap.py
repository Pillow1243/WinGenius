"""Application bootstrap: DPI, logging, theme, tool registry, main window."""
from __future__ import annotations

import os
import sys
from pathlib import Path
import traceback
from typing import List

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .core import theme
from .core.i18n import set_language
from .core.logger import get_logger, setup_logging
from .core.paths import APP_NAME, APP_VERSION, IS_WINDOWS, resource_path
from .core.registry import load_all, count
from .core.settings import settings
from .core import single_instance, upgrade, winapi

log = get_logger("bootstrap")
_APP: QApplication | None = None


_REPORTING = False


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    global _REPORTING
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.error("uncaught exception:\n%s", text)
    if _REPORTING or issubclass(exc_type, RecursionError):
        print(text, file=sys.stderr)
        return
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" or "--self-test" in sys.argv \
            or os.environ.get("WG_NO_DIALOG"):
        # headless / automated runs: never show a modal that would block forever
        print(text, file=sys.stderr)
        return
    _REPORTING = True
    try:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(f"{APP_NAME} — {APP_VERSION}")
        box.setText("An unexpected error occurred / خطای غیرمنتظره رخ داد")
        box.setDetailedText(text)
        box.exec()
    except Exception:
        print(text, file=sys.stderr)
    finally:
        _REPORTING = False


def create_app(argv: List[str] | None = None) -> QApplication:
    """Create the QApplication with WinGenius look & feel."""
    global _APP
    winapi.dpi_awareness()
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")

    setup_logging(debug="--debug" in sys.argv)
    try:
        report = upgrade.migrate(logger=log)
        if report.get("steps"):
            log.info(upgrade.summary(report, settings.lang))
    except Exception as exc:                      # never block startup on a migration
        log.error("data migration failed: %s", exc)
    set_language(settings.lang)

    icon_png = resource_path("icon.png")
    if icon_png.exists():
        app.setWindowIcon(QIcon(str(icon_png)))

    palette = theme.build_palette(settings.theme, settings.accent)
    app.setFont(theme.make_font(10))
    app.setStyleSheet(theme.stylesheet(palette))
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if settings.lang == "fa" else Qt.LayoutDirection.LeftToRight
    )
    sys.excepthook = _excepthook
    _APP = app
    return app


def main(argv: List[str] | None = None) -> int:
    # ---------------------------------------------------------- CLI mode
    argv = list(argv if argv is not None else sys.argv)
    from .core.cli import run_cli, wants_cli

    if wants_cli(argv) and "--self-test" not in argv:
        return run_cli(argv)

    # ------------------------------------------------- single-instance guard
    # A second copy would fight over settings.ini, the journal and the scheduler.
    guard = None
    if "--allow-multi" not in argv:
        guard = single_instance.InstanceGuard()
        if not guard.acquire():
            if guard.announce("show"):
                log.info("another instance is running — asked it to come forward")
            else:
                log.warning("another instance is running (no listener answered)")
            return 0

    app = create_app(argv)
    failures = load_all() or []
    for name, error in failures:
        log.error("tool module %s failed: %s", name, error)
    try:
        from .core.plugins import load_plugins

        loaded = load_plugins()
        if loaded:
            log.info("loaded %d plugin tool(s)", len(loaded))
    except Exception as exc:  # a broken plugin must never stop the app
        log.warning("plugin loading failed: %s", exc)
    log.info("%s %s starting with %d tools", APP_NAME, APP_VERSION, count())

    from .ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    if guard is not None and guard.primary:
        def _surface() -> None:
            window.showNormal()
            window.raise_()
            window.activateWindow()

        guard.listen(_surface)
        app.aboutToQuit.connect(guard.release)
    if settings.get("app/first_run"):
        settings.set("app/first_run", False)
        window._show_toast(
            "خوش آمدید! یک دسته را انتخاب کنید یا با Ctrl+K بنویسید چه کاری می‌خواهید."
            if settings.lang == "fa" else
            "Welcome! Pick a category, or press Ctrl+K and describe what you need.",
            "info",
        )
    if "--pick" in argv:
        window.open_search()
    if "--sendto" in argv:
        # Launched from the Explorer "Send to" shortcut: every existing path after the
        # flag goes straight into the smart-drop sheet.
        incoming = []
        for item in argv[argv.index("--sendto") + 1:]:
            if item.startswith("--"):
                continue
            candidate = Path(item)
            if candidate.exists():
                incoming.append(str(candidate))
        if incoming:
            log.info("send-to received %d item(s)", len(incoming))
            window._show_drop(incoming)
    if "--self-test" in sys.argv:
        # headless-friendly smoke run used by the build pipeline
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2500, app.quit)
    if IS_WINDOWS and not winapi.is_admin():
        window._show_toast(
            "برای ابزارهای سیستمی، برنامه را Run as administrator اجرا کنید."
            if settings.lang == "fa" else
            "Run as administrator for full system-tool access.",
            "info",
        )
    return app.exec()
