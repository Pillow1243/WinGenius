"""Auto-generated tool form, execution, progress and result panel."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core import theme
from ..core.i18n import t
from ..core.logger import get_logger
from ..core.registry import ToolSpec
from ..core.settings import settings
from ..core.spec import (
    K_BOOL, K_CHOICE, K_COLOR, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_MULTILINE,
    K_PASSWORD, K_SAVE, K_TEXT, Field, RunResult,
)
from ..core.utils import human_size, open_path, reveal_path
from ..core.worker import ToolRunner
from .widgets import Card, ColorButton, PathEdit, divider, icon_button, label, pixmap_icon

log = get_logger("ui.tool_panel")


class ToolPanel(QWidget):
    """Sliding panel that renders a ToolSpec and runs it."""

    closed = Signal()
    favoriteToggled = Signal(str, bool)
    toastRequested = Signal(str, str)
    outputDirChanged = Signal(str)
    queueRequested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ToolPanelRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.palette_ref = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self.spec: Optional[ToolSpec] = None
        self._widgets: Dict[str, QWidget] = {}
        self._rows: Dict[str, QWidget] = {}
        self._helpers: Dict[str, QWidget] = {}
        self._runner: Optional[ToolRunner] = None
        self._result: Optional[RunResult] = None
        self._jobs = None

        self._card = QFrame(self)
        self._card.setObjectName("Sheet")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        root = QVBoxLayout(self._card)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        # ------------------------------------------------------------- header
        header = QHBoxLayout()
        header.setSpacing(12)
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(48, 48)
        header.addWidget(self._icon_label, 0, Qt.AlignmentFlag.AlignTop)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self._title = label("", "H1")
        self._subtitle = label("", "Hint", wrap=True)
        title_box.addWidget(self._title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)

        self._fav_button = icon_button("star", t("tp.favorite"), 32)
        self._fav_button.clicked.connect(self._toggle_favorite)
        header.addWidget(self._fav_button, 0, Qt.AlignmentFlag.AlignTop)
        self._close_button = icon_button("x", t("tp.close"), 32)
        self._close_button.clicked.connect(self._request_close)
        header.addWidget(self._close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        root.addWidget(divider())

        # --------------------------------------------------------------- form
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._form_host = QWidget()
        self._form = QVBoxLayout(self._form_host)
        self._form.setContentsMargins(0, 4, 6, 8)
        self._form.setSpacing(14)
        self._form.addStretch(1)
        self._scroll.setWidget(self._form_host)
        root.addWidget(self._scroll, 1)

        # ------------------------------------------------------------- result
        self._result_card = Card()
        result_layout = QVBoxLayout(self._result_card)
        result_layout.setContentsMargins(14, 12, 14, 12)
        result_layout.setSpacing(8)
        self._result_title = label("", "SectionTitle")
        result_layout.addWidget(self._result_title)
        self._result_message = label("", "", wrap=True)
        result_layout.addWidget(self._result_message)
        self._result_details = QPlainTextEdit()
        self._result_details.setReadOnly(True)
        self._result_details.setObjectName("Log")
        self._result_details.setMaximumHeight(170)
        self._result_details.setVisible(False)
        result_layout.addWidget(self._result_details)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._open_button = QPushButton(t("tp.open_output"))
        self._open_button.clicked.connect(self._open_result)
        self._reveal_button = QPushButton(t("tp.show_in_folder"))
        self._reveal_button.setObjectName("Ghost")
        self._reveal_button.clicked.connect(self._reveal_result)
        self._copy_button = QPushButton(t("tp.copy"))
        self._copy_button.setObjectName("Ghost")
        self._copy_button.clicked.connect(self._copy_result)
        actions.addWidget(self._open_button)
        actions.addWidget(self._reveal_button)
        actions.addWidget(self._copy_button)
        actions.addStretch(1)
        result_layout.addLayout(actions)
        self._result_card.setVisible(False)
        root.addWidget(self._result_card)

        # ------------------------------------------------------------- footer
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._status = label("", "Hint")
        footer.addWidget(self._status, 1)

        self._outdir_button = QPushButton()
        self._outdir_button.setObjectName("Pill")
        self._outdir_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._outdir_button.clicked.connect(self._choose_output_dir)
        footer.addWidget(self._outdir_button, 0)

        self._log_button = QPushButton(t("tp.log"))
        self._log_button.setObjectName("Pill")
        self._log_button.setCheckable(True)
        self._log_button.clicked.connect(self._toggle_log)
        footer.addWidget(self._log_button, 0)

        self._cancel_button = QPushButton(t("tp.cancel"))
        self._cancel_button.setObjectName("Ghost")
        self._cancel_button.clicked.connect(self._cancel_or_close)
        footer.addWidget(self._cancel_button, 0)

        self._queue_button = QPushButton(t("tp.queue"))
        self._queue_button.setObjectName("Ghost")
        self._queue_button.setToolTip(t("tp.queued"))
        self._queue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._queue_button.clicked.connect(self._run_in_background)
        footer.addWidget(self._queue_button, 0)

        self._run_button = QPushButton(t("tp.run"))
        self._run_button.setObjectName("Primary")
        self._run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_button.setMinimumWidth(120)
        self._run_button.clicked.connect(self._run)
        footer.addWidget(self._run_button, 0)
        root.addLayout(footer)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("Log")
        self._log_view.setReadOnly(True)
        self._log_view.setFixedHeight(110)
        self._log_view.setVisible(False)
        root.addWidget(self._log_view)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide = QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._run)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------ theme
    def apply_palette(self, palette) -> None:
        self.palette_ref = palette
        from .widgets import _make_icon

        accent = QColor(palette.accent)
        icon = self.spec.icon if self.spec else "gear"
        self._icon_label.setPixmap(pixmap_icon(icon, QColor(accent), 44))
        self._close_button.setIcon(_make_icon("x", QColor(palette.text_dim), 32))
        self._update_favorite_icon()

    def attach_jobs(self, manager) -> None:
        """Give the panel a job manager so runs can go to the background queue."""
        self._jobs = manager

    # ------------------------------------------------------------------- load
    def load(self, spec: ToolSpec, lang: str, output_dir: str,
             values: Optional[Dict[str, Any]] = None) -> None:
        self.spec = spec
        self.lang = lang
        self._result = None
        self._result_card.setVisible(False)
        self._log_view.clear()
        self._log_view.setVisible(self._log_button.isChecked())
        self._progress.setVisible(False)
        self._status.setText(t("tp.ready"))

        self._title.setText(spec.name_for(lang))
        self._subtitle.setText(spec.desc_for(lang))
        self._icon_label.setPixmap(pixmap_icon(spec.icon, QColor(self._accent_color()), 44))
        self._update_favorite_icon()

        # clear old form
        while self._form.count() > 1:
            item = self._form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._widgets.clear()
        self._rows.clear()
        self._helpers.clear()

        if spec.danger:
            banner = label("⚠  " + ("این ابزار تغییرات غیرقابل بازگشت انجام می‌دهد." if lang == "fa"
                                     else "This tool makes irreversible changes."), "Hint")
            banner.setStyleSheet("color:#FFC24B;")
            self._form.insertWidget(0, banner)
        if spec.admin and os.name == "nt":
            note = label("🔒  " + ("برای عملکرد کامل، برنامه را به‌عنوان مدیر اجرا کنید."
                                   if lang == "fa" else "Run the app as administrator for full access."),
                         "Hint")
            self._form.insertWidget(1, note)

        for field in spec.fields:
            self._add_field(field)

        defaults = spec.defaults()
        for key, value in defaults.items():
            widget = self._widgets.get(key)
            if widget is not None and value not in (None, ""):
                self._set_widget_value(widget, value, field=spec.field(key))
        if values:
            self._prefill(values)
        self._refresh_visibility()
        self._update_outdir_button(output_dir)
        self._run_button.setEnabled(True)
        self._scroll.verticalScrollBar().setValue(0)

    # ----------------------------------------------------------------- helpers
    def _prefill(self, values: Dict[str, Any]) -> None:
        """Fill fields from dropped files / a previous run."""
        from ..core.spec import K_FILES as _K_FILES

        filled = False
        pending = dict(values or {})
        # a list of paths should land in the first file/multi-file field
        path_values = [v for v in pending.values() if isinstance(v, (list, tuple)) and v]
        file_fields = [f for f in (self.spec.fields if self.spec else []) if f.kind in (K_FILE, K_FILES)]
        if path_values and file_fields and all(
                isinstance(item, str) and Path(item).exists() for item in path_values[0]):
            target = file_fields[0]
            items = list(path_values[0])
            widget = self._widgets.get(target.key)
            if widget is not None:
                self._set_widget_value(widget, items if target.kind == _K_FILES else items[0], target)
                filled = True
                pending.pop(target.key, None)
                pending.pop("__paths__", None)
        for key, value in pending.items():
            if key.startswith("__"):
                continue
            field = self.spec.field(key) if self.spec else None
            widget = self._widgets.get(key)
            if widget is None or value in (None, ""):
                continue
            if field is not None and field.kind in (K_FILE, K_DIR, K_SAVE):
                if isinstance(value, (list, tuple)):
                    continue
                if not Path(str(value)).exists():
                    continue
            self._set_widget_value(widget, value, field)
            filled = True
        if filled:
            self._status.setText(t("tp.prefilled"))

    def cli_command(self) -> str:
        """A ready-to-paste command line that reproduces the current form."""
        if not self.spec:
            return ""
        values = self._collect()
        parts = [f'WinGenius.exe --run {self.spec.id}']
        for key, value in values.items():
            if value in (None, "", [], False):
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    parts.append(f'--file "{item}"')
                continue
            if isinstance(value, bool):
                parts.append(f"--set {key}=true")
            else:
                parts.append(f'--set {key}="{value}"')
        return " ".join(parts)

    def _run_in_background(self) -> None:
        if not self.spec:
            return
        values = self._collect()
        problem = self._validate(values)
        if problem:
            self.toastRequested.emit(problem, "error")
            self._status.setText(problem)
            return
        if self._jobs is None:
            self._run()
            return
        job = self._jobs.submit(self.spec, values, self.lang, settings.output_dir)
        self.toastRequested.emit(t("tp.queued"), "success")
        if job is not None:
            self.queueRequested.emit(job.id)

    def _accent_color(self) -> str:
        try:
            from ..core.registry import category

            return category(self.spec.category).color if self.spec else self.palette_ref.accent
        except Exception:
            return self.palette_ref.accent

    def _update_favorite_icon(self) -> None:
        if not self.spec:
            return
        is_fav = settings.is_favorite(self.spec.id)
        color = QColor(self.palette_ref.warning if is_fav else self.palette_ref.text_dim)
        from .widgets import _make_icon

        self._fav_button.setIcon(_make_icon("star", color, 32))
        self._fav_button.setToolTip(t("tp.unfavorite") if is_fav else t("tp.favorite"))

    def _toggle_favorite(self) -> None:
        if not self.spec:
            return
        state = settings.toggle_favorite(self.spec.id)
        self._update_favorite_icon()
        self.favoriteToggled.emit(self.spec.id, state)
        self.toastRequested.emit(t("toast.saved"), "success")

    # ------------------------------------------------------------------- form
    def _add_field(self, field: Field) -> None:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        label_row = QHBoxLayout()
        label_row.setSpacing(6)
        text = field.label_for(self.lang)
        if field.required:
            text += " *"
        field_label = label(text, "FieldLabel")
        label_row.addWidget(field_label)
        label_row.addStretch(1)
        help_text = field.help_for(self.lang)
        if help_text:
            hint = label("؟", "FieldHelp")
            hint.setToolTip(help_text)
            hint.setFixedWidth(14)
            label_row.addWidget(hint)
        layout.addLayout(label_row)

        widget = self._create_widget(field)
        if widget is not None:
            # Screen readers announce the field by this name; without it every
            # auto-generated input is an anonymous "edit" box.
            widget.setAccessibleName(text.replace(" *", ""))
            if help_text:
                widget.setAccessibleDescription(help_text)
            # keep compact inputs compact; file pickers get the full row
            if field.kind in (K_INT, K_FLOAT):
                widget.setMaximumWidth(240)
            elif field.kind == K_CHOICE:
                widget.setMaximumWidth(420)
            elif field.kind == K_COLOR:
                widget.setMaximumWidth(180)
            layout.addWidget(widget)
            self._widgets[field.key] = widget
        if help_text:
            helper = label(help_text, "FieldHelp", wrap=True)
            layout.addWidget(helper)
            self._helpers[field.key] = helper
        self._rows[field.key] = row
        self._form.insertWidget(self._form.count() - 1, row)

    def _create_widget(self, field: Field) -> Optional[QWidget]:
        kind = field.kind
        if kind in (K_FILE, K_FILES, K_DIR, K_SAVE):
            widget = PathEdit(kind, field.filt, multiple=field.multiple)
            if field.placeholder:
                widget.edit.setPlaceholderText(field.placeholder)
            widget.changed.connect(self._refresh_visibility)
            return widget
        if kind == K_BOOL:
            widget = QCheckBox(field.label_for(self.lang))
            widget.setChecked(bool(field.default))
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            return widget
        if kind == K_CHOICE:
            widget = QComboBox()
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            widget.setMinimumWidth(220)
            for value, label_fa, label_en in field.choices:
                text = label_en if self.lang == "en" else label_fa
                widget.addItem(text, value)
            widget.setEditable = getattr(widget, "setEditable", None)
            widget.currentIndexChanged.connect(self._refresh_visibility)
            return widget
        if kind == K_INT:
            widget = QSpinBox()
            widget.setRange(int(field.minimum), int(field.maximum))
            widget.setSingleStep(max(1, int(field.step)))
            widget.setValue(int(field.default or field.minimum))
            if field.suffix:
                widget.setSuffix(f" {field.suffix}")
            return widget
        if kind == K_FLOAT:
            widget = QDoubleSpinBox()
            widget.setDecimals(max(1, field.decimals))
            widget.setRange(float(field.minimum), float(field.maximum))
            widget.setSingleStep(float(field.step))
            widget.setValue(float(field.default or field.minimum))
            if field.suffix:
                widget.setSuffix(f" {field.suffix}")
            return widget
        if kind == K_COLOR:
            return ColorButton(str(field.default or "#FFFFFF"))
        if kind == K_MULTILINE:
            widget = QPlainTextEdit()
            widget.setPlaceholderText(field.placeholder or "")
            widget.setFixedHeight(110)
            return widget
        if kind in (K_TEXT, K_PASSWORD):
            widget = QLineEdit()
            widget.setClearButtonEnabled(True)
            if kind == K_PASSWORD:
                widget.setEchoMode(QLineEdit.EchoMode.Password)
            widget.setPlaceholderText(field.placeholder if self.lang != "en" else (field.placeholder_en or field.placeholder))
            return widget
        return QLineEdit()

    def _set_widget_value(self, widget: QWidget, value: Any, field: Optional[Field] = None) -> None:
        if isinstance(widget, PathEdit):
            widget.set_value(value)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
        elif isinstance(widget, QSpinBox):
            try:
                widget.setValue(int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(widget, QDoubleSpinBox):
            try:
                widget.setValue(float(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(widget, ColorButton):
            widget.set_value(value)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText(str(value))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))

    def _widget_value(self, widget: QWidget) -> Any:
        if isinstance(widget, PathEdit):
            return widget.value()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        if isinstance(widget, ColorButton):
            return widget.value()
        if isinstance(widget, QPlainTextEdit):
            return widget.toPlainText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return None

    def _is_visible(self, field: Field) -> bool:
        condition = field.visible_when
        if not condition:
            return True
        other_key, expected = condition
        other = self._widgets.get(other_key)
        if other is None:
            return True
        value = self._widget_value(other)
        if isinstance(expected, (list, tuple, set)):
            return value in expected
        return value == expected

    def _refresh_visibility(self) -> None:
        if not self.spec:
            return
        for field in self.spec.fields:
            visible = self._is_visible(field)
            row = self._rows.get(field.key)
            if row is not None:
                row.setVisible(visible)
            helper = self._helpers.get(field.key)
            if helper is not None:
                helper.setVisible(visible and bool(field.help_for(self.lang)))

    # -------------------------------------------------------------------- run
    def _collect(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        assert self.spec is not None
        for field in self.spec.fields:
            if not self._is_visible(field):
                continue
            widget = self._widgets.get(field.key)
            if widget is None:
                continue
            values[field.key] = self._widget_value(widget)
        return values

    def _validate(self, values: Dict[str, Any]) -> Optional[str]:
        assert self.spec is not None
        for field in self.spec.fields:
            if not self._is_visible(field):
                continue
            value = values.get(field.key)
            empty = value in (None, "", [], 0.0) and field.kind not in (K_INT, K_FLOAT, K_BOOL)
            if field.required and empty:
                return f"{field.label_for(self.lang)}: {t('tp.required')}"
            if field.kind in (K_FILE, K_DIR) and value:
                path = Path(str(value))
                if not path.exists():
                    return t("tp.not_found", str(path))
            if field.kind == K_FILES and value:
                for item in (value if isinstance(value, (list, tuple)) else [value]):
                    if not Path(str(item)).exists():
                        return t("tp.not_found", str(item))
        return None

    def _run(self) -> None:
        if not self.spec or self._runner is not None:
            return
        values = self._collect()
        problem = self._validate(values)
        if problem:
            self.toastRequested.emit(problem, "error")
            self._status.setText(problem)
            return
        if self.spec.danger and settings.get("ui/confirm_danger"):
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(self, t("err.title"), t("tp.confirm_danger"),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._result_card.setVisible(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status.setText(t("tp.running"))
        self._run_button.setEnabled(False)
        self._cancel_button.setText(t("tp.cancel"))
        self._log_view.appendPlainText(f"▶ {self.spec.name_for(self.lang)}")

        self._runner = ToolRunner(self.spec, values, self.lang, settings.output_dir, self)
        self._runner.progressed.connect(self._on_progress)
        self._runner.messaged.connect(self._on_message)
        self._runner.succeeded.connect(self._on_success)
        self._runner.failed.connect(self._on_error)
        self._runner.cancelled.connect(self._on_cancelled)
        self._runner.finished.connect(self._cleanup_runner)
        self._runner.start()

    def _cleanup_runner(self) -> None:
        if self._runner is not None:
            self._runner.deleteLater()
        self._runner = None
        self._run_button.setEnabled(True)
        self._cancel_button.setText(t("tp.cancel"))
        self._progress.setVisible(False)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if total <= 0:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, total)
            self._progress.setValue(min(current, total))
        if message:
            self._status.setText(message)

    def _on_message(self, message: str) -> None:
        self._log_view.appendPlainText(message)
        if not self._log_view.isVisible() and self._log_button.isChecked():
            self._log_view.setVisible(True)

    def _on_success(self, result: RunResult) -> None:
        self._result = result
        self._status.setText(t("tp.done"))
        self._show_result(result, error=False)
        self.toastRequested.emit(result.message.split("\n")[0][:120] or t("tp.done"), "success")
        if settings.get("files/open_after_run") and result.open_target:
            try:
                reveal_path(result.open_target)
            except Exception as exc:
                log.debug("cannot reveal %s: %s", result.open_target, exc)

    def _on_error(self, message: str, trace: str) -> None:
        self._status.setText(t("tp.failed"))
        self._result = RunResult(message=message, details=trace)
        self._show_result(self._result, error=True)
        self.toastRequested.emit(message.split("\n")[0][:120], "error")

    def _on_cancelled(self) -> None:
        self._status.setText(t("tp.ready"))
        self.toastRequested.emit(t("tp.cancel"), "warning")

    def _show_result(self, result: RunResult, error: bool = False) -> None:
        self._result_card.setVisible(True)
        self._result_title.setText(t("tp.failed") if error else t("tp.done"))
        self._result_title.setStyleSheet(f"color:{self.palette_ref.danger if error else self.palette_ref.success};")
        self._result_message.setText(result.message or "")
        details = result.details or ""
        self._result_details.setPlainText(details)
        self._result_details.setVisible(bool(details))
        self._open_button.setVisible(bool(result.outputs or result.open_target))
        self._reveal_button.setVisible(bool(result.outputs))
        self._copy_button.setVisible(bool(details or result.message))

    def _open_result(self) -> None:
        if not self._result:
            return
        target = self._result.open_target or (self._result.outputs[0] if self._result.outputs else "")
        if not target:
            self.toastRequested.emit(t("toast.no_output"), "warning")
            return
        if Path(target).is_dir():
            open_path(target)
        else:
            reveal_path(target)

    def _reveal_result(self) -> None:
        if not self._result or not self._result.outputs:
            self.toastRequested.emit(t("toast.no_output"), "warning")
            return
        reveal_path(self._result.outputs[0])

    def _copy_result(self) -> None:
        if not self._result:
            return
        text = self._result.details or self._result.message
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        self.toastRequested.emit(t("toast.output_copied"), "success")

    # ----------------------------------------------------------------- footer
    def _choose_output_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(self, t("st.output"), settings.output_dir)
        if chosen:
            settings.output_dir = chosen
            self._update_outdir_button(chosen)
            self.outputDirChanged.emit(chosen)

    def _update_outdir_button(self, path: str) -> None:
        name = Path(path).name or path
        self._outdir_button.setText(f"{t('tp.output_dir')}: {name}")
        self._outdir_button.setToolTip(f"{t('tp.output_dir')}: {path}")

    def _toggle_log(self) -> None:
        self._log_view.setVisible(self._log_button.isChecked())

    def _cancel_or_close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._status.setText(t("tp.cancel"))
        else:
            self._request_close()

    def _request_close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
        self.closed.emit()

    # ---------------------------------------------------------- drag and drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths or not self.spec:
            return
        for field in self.spec.fields:
            widget = self._widgets.get(field.key)
            if isinstance(widget, PathEdit) and field.kind in (K_FILE, K_FILES, K_DIR, K_SAVE):
                widget.add_paths(paths)
                self.toastRequested.emit(
                    ("%d فایل اضافه شد" % len(paths)) if self.lang == "fa" else (f"{len(paths)} file(s) attached"),
                    "info",
                )
                break
        event.acceptProposedAction()

    # ---------------------------------------------------------------- animate
    def slide_in(self, width: int, height: int) -> None:
        self._hide_timer.stop()
        self.setFixedSize(width, height)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._slide.stop()
        self._slide.setStartValue(self.geometry().adjusted(width + 40, 0, width + 40, 0))
        self._slide.setEndValue(self.geometry())
        self._slide.start()

    def slide_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.setDuration(140)
        self._fade.start()
        self._hide_timer.start(170)

    def dismiss(self) -> None:
        """Close the sheet from outside (cancel a running tool first)."""
        if self._runner is not None:
            self._runner.cancel()
        self.slide_out()

    def close_panel(self) -> None:
        self.hide()

    def shutdown(self, timeout_ms: int = 4000) -> None:
        """Cancel a running tool and wait for its thread (called on app close)."""
        runner = self._runner
        if runner is None:
            return
        runner.cancel()
        try:
            runner.wait(timeout_ms)
        except Exception:
            pass
        self._runner = None
