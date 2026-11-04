"""In-app settings sheet (language, theme, accent, animations, output folder)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core import theme
from ..core.i18n import t
from ..core.diagnostics import diagnostics_text, doctor_issues
from ..core.paths import APP_VERSION, data_dir
from ..core.settings import settings
from ..core.theme import PALETTES, Palette
from ..core.utils import deps_available, open_path
from .widgets import Card, ColorButton, divider, label

ACCENTS = ["#5B8CFF", "#8B5CFF", "#00C2A8", "#FF6B6B", "#FFA502", "#2ED573", "#FF5CE1"]


class SettingsPanel(QWidget):
    """Centered settings card with live-applied changes."""

    closed = Signal()
    languageChanged = Signal(str)
    themeChanged = Signal()
    openLogs = Signal()
    pluginsChanged = Signal()
    openJobsRequested = Signal()
    jobsChangedRequested = Signal(int)
    checkUpdatesRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self.setVisible(False)

        self._card = Card(self)
        self._card.setFixedWidth(600)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(14)

        header = QHBoxLayout()
        self._title = label(t("st.title"), "H1")
        header.addWidget(self._title)
        header.addStretch(1)
        self._close = QPushButton("✕")
        self._close.setObjectName("Ghost")
        self._close.setFixedWidth(36)
        self._close.clicked.connect(self.close_panel)
        header.addWidget(self._close)
        card_layout.addLayout(header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 6, 0)
        self._body_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(560)
        scroll.setWidget(self._body)
        self._scroll = scroll
        card_layout.addWidget(scroll, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        self._faded_once = False

        self._build()

    # ------------------------------------------------------------------ build
    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette
        self.setStyleSheet("")
        self._build()

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self._title.setText(t("st.title"))
        self._build()

    def _section(self, text: str) -> QLabel:
        widget = label(text, "SectionTitle")
        self._body_layout.addWidget(widget)
        return widget

    def _build(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())

        # --------------------------------------------------------- language
        self._section(t("st.language"))
        lang_row = QHBoxLayout()
        group = QButtonGroup(self)
        for code, text in (("fa", "فارسی"), ("en", "English")):
            button = QPushButton(text)
            button.setObjectName("Pill")
            button.setCheckable(True)
            button.setChecked(settings.lang == code)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, c=code: self._set_language(c))
            group.addButton(button)
            lang_row.addWidget(button)
        lang_row.addStretch(1)
        self._add_row(lang_row)

        # ------------------------------------------------------------ theme
        self._section(t("st.theme"))
        theme_row = QHBoxLayout()
        for key, palette in PALETTES.items():
            button = QPushButton(palette.name_en if self.lang == "en" else palette.name)
            button.setObjectName("Pill")
            button.setCheckable(True)
            button.setChecked(settings.theme == key)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self._set_theme(k))
            theme_row.addWidget(button)
        theme_row.addStretch(1)
        self._add_row(theme_row)

        # ----------------------------------------------------------- accent
        self._section(t("st.accent"))
        accent_row = QHBoxLayout()
        accent_row.setSpacing(8)
        for color in ACCENTS:
            button = QPushButton()
            button.setFixedSize(30, 30)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            border = "3px solid #FFFFFF" if color.lower() == settings.accent.lower() else "1px solid rgba(255,255,255,0.25)"
            button.setStyleSheet(f"background:{color};border:{border};border-radius:15px;")
            button.clicked.connect(lambda _checked=False, c=color: self._set_accent(c))
            accent_row.addWidget(button)
        accent_row.addStretch(1)
        self._add_row(accent_row)

        # ----------------------------------------------------------- toggles
        self._section(t("st.animations"))
        for key, text in (
            ("ui/animations", t("st.animations")),
            ("ui/glow", t("st.glow")),
            ("ui/confirm_danger", t("st.confirm")),
            ("files/open_after_run", t("st.open_result")),
            ("index/ocr", t("st.ocr_index")),
        ):
            box = QCheckBox(text)
            box.setChecked(settings.get(key))
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            box.toggled.connect(lambda state, k=key: self._set_flag(k, state))
            self._add_row(box)

        # ------------------------------------------------- windows integration
        self._section(t("st.quick_actions"))
        sendto_row = QHBoxLayout()
        self._sendto_hint = label("", "Hint")
        sendto_row.addWidget(self._sendto_hint, 1)
        install_btn = QPushButton(t("st.install_sendto"))
        install_btn.setObjectName("Ghost")
        install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        install_btn.clicked.connect(lambda: self._sendto("install"))
        sendto_row.addWidget(install_btn)
        remove_btn = QPushButton(t("st.remove_sendto"))
        remove_btn.setObjectName("Ghost")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self._sendto("remove"))
        sendto_row.addWidget(remove_btn)
        self._add_row(sendto_row)
        self._refresh_sendto()

        # ------------------------------------------------------- output dir
        self._section(t("st.output"))
        out_row = QHBoxLayout()
        self._output_label = label(settings.output_dir, "Hint")
        out_row.addWidget(self._output_label, 1)
        browse = QPushButton(t("tp.browse_dir"))
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._choose_output)
        out_row.addWidget(browse)
        self._add_row(out_row)

        # ------------------------------------------------------ system/diag
        self._add_row(divider())
        self._system_section = self._section(t("st.system"))
        info_row = QHBoxLayout()
        self._diag_view = QPlainTextEdit()
        self._diag_view.setObjectName("Log")
        self._diag_view.setReadOnly(True)
        self._diag_view.setFixedHeight(132)
        self._diag_view.setPlainText(diagnostics_text(full=False))
        info_row.addWidget(self._diag_view, 1)
        self._add_row(info_row)

        issues = doctor_issues()
        if issues:
            issue_text = "\n".join(f"• {item}" for item in issues[:4])
            issues_label = label(issue_text, "FieldHelp", wrap=True)
            issues_label.setStyleSheet("color:#FFC24B;")
            self._add_row(issues_label)

        sys_row = QHBoxLayout()
        copy_button = QPushButton(t("tp.copy"))
        copy_button.setObjectName("Ghost")
        copy_button.clicked.connect(self._copy_diagnostics)
        sys_row.addWidget(copy_button)
        refresh_button = QPushButton(t("st.doctor"))
        refresh_button.setObjectName("Ghost")
        refresh_button.clicked.connect(self._refresh_diagnostics)
        sys_row.addWidget(refresh_button)
        jobs_button = QPushButton(t("cmd.jobs"))
        jobs_button.setObjectName("Ghost")
        jobs_button.clicked.connect(lambda: self.openJobsRequested.emit())
        sys_row.addWidget(jobs_button)
        sys_row.addStretch(1)
        self._add_row(sys_row)

        # parallel jobs
        par_row = QHBoxLayout()
        par_row.addWidget(label(t("st.parallel"), "FieldLabel"))
        self._parallel = QComboBox()
        for count in (1, 2, 3, 4, 6):
            self._parallel.addItem(str(count), count)
        current = int(settings.get("ui/parallel") or 3)
        index = self._parallel.findData(current)
        self._parallel.setCurrentIndex(index if index >= 0 else 1)
        self._parallel.currentIndexChanged.connect(self._set_parallel)
        par_row.addWidget(self._parallel)
        par_row.addStretch(1)
        self._add_row(par_row)

        # ----------------------------------------------------------- plugins
        self._plugins_section = self._section(t("st.plugins"))
        from ..core import plugins as plugins_mod

        found = len(plugins_mod.discover())
        self._add_row(label(t("st.plugins_count", found), "Hint"))
        plugin_row = QHBoxLayout()
        open_button = QPushButton(t("st.plugins_open"))
        open_button.setObjectName("Ghost")
        open_button.clicked.connect(lambda: plugins_mod.open_plugins_folder())
        plugin_row.addWidget(open_button)
        example_button = QPushButton(t("st.plugins_example"))
        example_button.setObjectName("Ghost")
        example_button.clicked.connect(self._create_example_plugin)
        plugin_row.addWidget(example_button)
        reload_button = QPushButton(t("st.plugins_reload"))
        reload_button.setObjectName("Ghost")
        reload_button.clicked.connect(self._reload_plugins)
        plugin_row.addWidget(reload_button)
        plugin_row.addStretch(1)
        self._add_row(plugin_row)

        history_row = QHBoxLayout()
        clear_history = QPushButton(t("st.history_clear"))
        clear_history.setObjectName("Ghost")
        clear_history.clicked.connect(self._clear_history)
        history_row.addWidget(clear_history)
        history_row.addStretch(1)
        self._add_row(history_row)

        # ------------------------------------------------------------- updates
        self._add_row(divider())
        self._section(t("st.updates"))
        auto_upd_chk = QCheckBox(t("st.auto_update"))
        auto_upd_chk.setChecked(bool(settings.get("ui/auto_check_updates")))
        auto_upd_chk.toggled.connect(lambda state: self._set_flag("ui/auto_check_updates", state))
        self._add_row(auto_upd_chk)

        upd_row = QHBoxLayout()
        check_upd_btn = QPushButton(t("st.check_update_btn"))
        check_upd_btn.setObjectName("Primary")
        check_upd_btn.clicked.connect(self.checkUpdatesRequested.emit)
        upd_row.addWidget(check_upd_btn)

        from ..core.updates import get_rollback_state

        rb_state = get_rollback_state()
        if rb_state and (rb_state.get("backup_exe_exists") or rb_state.get("data_backup_exists")):
            rb_btn = QPushButton(t("st.rollback_btn", str(rb_state.get("previous_version") or "?")))
            rb_btn.setObjectName("Ghost")
            rb_btn.clicked.connect(self.checkUpdatesRequested.emit)
            upd_row.addWidget(rb_btn)
        upd_row.addStretch(1)
        self._add_row(upd_row)

        # ------------------------------------------------------------- misc
        self._add_row(divider())
        self._section(t("st.about"))
        about = label(t("st.about_text", APP_VERSION), "Hint", wrap=True)
        self._add_row(about)

        deps = deps_available()
        missing = [name for name, available in deps.items() if not available]
        dep_text = ("همه‌ی کتابخانه‌ها نصب هستند" if self.lang == "fa" else "All libraries available") \
            if not missing else ("نصب نیست: " if self.lang == "fa" else "Missing: ") + ", ".join(missing)
        dep_label = label(dep_text, "FieldHelp", wrap=True)
        dep_label.setStyleSheet("color:#2BD9A0;" if not missing else "color:#FFC24B;")
        self._add_row(dep_label)

        buttons = QHBoxLayout()
        logs_button = QPushButton(t("st.logs"))
        logs_button.setObjectName("Ghost")
        logs_button.clicked.connect(lambda: open_path(data_dir() / "logs"))
        buttons.addWidget(logs_button)
        reset_button = QPushButton(t("st.reset"))
        reset_button.setObjectName("Ghost")
        reset_button.clicked.connect(self._reset)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        close_button = QPushButton(t("st.close"))
        close_button.setObjectName("Primary")
        close_button.clicked.connect(self.close_panel)
        buttons.addWidget(close_button)
        self._add_row(buttons)

        self._body_layout.addStretch(1)

    def _add_row(self, item) -> None:
        if isinstance(item, QWidget):
            self._body_layout.addWidget(item)
        else:
            holder = QWidget()
            holder.setLayout(item)
            self._body_layout.addWidget(holder)

    # --------------------------------------------------------------- actions
    def _set_language(self, code: str) -> None:
        settings.lang = code
        self.lang = code
        self.languageChanged.emit(code)
        self._build()

    def _set_theme(self, key: str) -> None:
        settings.theme = key
        self.themeChanged.emit()

    def _set_accent(self, color: str) -> None:
        settings.accent = color
        self.themeChanged.emit()

    def _sendto_shortcut(self) -> Path:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Microsoft" / "Windows" / "SendTo" / "WinGenius.lnk"

    def _refresh_sendto(self) -> None:
        """Show whether the Explorer shortcut exists (Windows only)."""
        from ..core.paths import IS_WINDOWS

        if not IS_WINDOWS:
            self._sendto_hint.setText(t("st.sendto_missing"))
            return
        shortcut = self._sendto_shortcut()
        try:
            exists = shortcut.exists()
        except OSError:
            exists = False
        if settings.lang == "fa":
            prefix = "نصب شده ✓ — " if exists else "نصب نشده — "
        else:
            prefix = "installed ✓ — " if exists else "not installed — "
        self._sendto_hint.setText(prefix + str(shortcut))

    def _sendto(self, action: str) -> None:
        from ..core.registry import get as get_tool
        from ..core.spec import RunContext

        tool = get_tool("sendto_install")
        if tool is None:
            return
        self._sendto_hint.setText("…")
        try:
            result = tool.run(RunContext(lang=settings.lang), {"action": action})
            self._sendto_hint.setText(result.message)
        except Exception as exc:
            self._sendto_hint.setText(str(exc)[:160])
        self._refresh_sendto()

    def _set_flag(self, key: str, state: bool) -> None:
        settings.set(key, bool(state))
        if key in ("ui/animations", "ui/glow"):
            self.themeChanged.emit()

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(diagnostics_text())
        self._diag_view.setPlainText(t("st.copied"))

    def _refresh_diagnostics(self) -> None:
        self._diag_view.setPlainText(diagnostics_text())
        self._build()

    def _set_parallel(self, _index: int) -> None:
        value = self._parallel.currentData()
        try:
            settings.set("ui/parallel", int(value))
        except Exception:
            settings.set("ui/parallel", 3)
        self.jobsChangedRequested.emit(int(value or 3))

    def _create_example_plugin(self) -> None:
        from ..core.plugins import write_example_plugin

        path = write_example_plugin()
        self.pluginsChanged.emit()
        self._build()
        try:
            from ..core.utils import reveal_path

            reveal_path(str(path))
        except Exception:
            pass

    def _reload_plugins(self) -> None:
        from ..core.registry import load_all

        from PySide6.QtWidgets import QMessageBox
        from ..core import plugins

        paths = plugins.discover()
        if not paths:
            return
        warning = (
            "افزونه‌های Python با دسترسی کامل حساب شما اجرا می‌شوند؛ سندباکس ندارند.\n"
            "فقط اگر فایل‌های زیر را می‌شناسید تأیید کنید:\n"
            if self.lang == "fa" else
            "Python plugins run with your account privileges, without a sandbox.\n"
            "Approve only files you have reviewed:\n"
        )
        answer = QMessageBox.warning(
            self, t("st.plugins"), warning + "\n".join(str(p.resolve()) for p in paths),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for path in paths:
            plugins.trust_plugin(path)
        load_all()
        plugins.load_plugins()
        self.pluginsChanged.emit()
        self._build()

    def _clear_history(self) -> None:
        from ..core.history import history

        history.clear()
        self.pluginsChanged.emit()

    def scroll_to_system(self) -> None:
        try:
            self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())
        except Exception:
            pass

    def _choose_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, t("st.output"), settings.output_dir)
        if chosen:
            settings.output_dir = chosen
            self._output_label.setText(chosen)

    def _reset(self) -> None:
        for key, default in (
            ("ui/theme", "aurora"), ("ui/accent", "#5B8CFF"), ("ui/animations", True),
            ("ui/glow", True), ("ui/confirm_danger", True), ("files/open_after_run", True),
        ):
            settings.set(key, default)
        self.themeChanged.emit()
        self._build()

    # ------------------------------------------------------------------ show
    def open_panel(self) -> None:
        self._build()
        self._ensure_fade()
        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _ensure_fade(self) -> None:
        """(Re)create the fade-in effect; it is dropped when the fade finishes."""
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            self._opacity = effect
            return
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0 if self._faded_once else 1.0)
        self.setGraphicsEffect(effect)
        self._opacity = effect
        self._fade = QPropertyAnimation(effect, b"opacity", self)
        self._fade.setDuration(170)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._drop_fade)

    def _drop_fade(self) -> None:
        """Drop the opacity effect after the fade — it caches a pixmap of the
        panel and leaves ghost text behind whenever the settings list reflows."""
        if isinstance(self.graphicsEffect(), QGraphicsOpacityEffect):
            self.setGraphicsEffect(None)
            self._faded_once = True
            self.update()

    def close_panel(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
