"""Live job queue drawer: every running / finished task with progress and cancel."""
from __future__ import annotations

import time
from typing import List, Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import theme
from ..core.i18n import t
from ..core.theme import Palette
from ..core.utils import open_path, reveal_path, ui_number
from .widgets import Card, divider, label

STATUS_COLORS = {"queued": "#8FA3BF", "running": "#18C8F5", "done": "#2BD9A0",
                 "failed": "#FF5A6E", "cancelled": "#FFC24B"}


def _status_text(status: str, lang: str) -> str:
    table = {
        "queued": ("در صف", "Queued"),
        "running": ("در حال اجرا", "Running"),
        "done": ("انجام شد", "Done"),
        "failed": ("خطا", "Failed"),
        "cancelled": ("لغو شد", "Cancelled"),
    }
    fa, en = table.get(status, (status, status))
    return en if lang == "en" else fa


def _ago(when: float, lang: str) -> str:
    if not when:
        return ""
    seconds = max(0, int(time.time() - when))
    if seconds < 60:
        return f"{seconds}s" if lang == "en" else f"{seconds} ثانیه"
    if seconds < 3600:
        return f"{seconds // 60}m" if lang == "en" else f"{seconds // 60} دقیقه"
    return f"{seconds // 3600}h" if lang == "en" else f"{seconds // 3600} ساعت"


JOB_ROW_HEIGHT = 88
JOB_ROW_SPACING = 8


class JobRow(QFrame):
    """One line of the queue."""

    cancelRequested = Signal(str)
    openRequested = Signal(str)
    rerunRequested = Signal(str)

    def __init__(self, job, lang: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("JobRow")
        self.job = job
        self.lang = lang
        self.setFixedHeight(JOB_ROW_HEIGHT)
        self.setStyleSheet(
            f"#JobRow{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:12px;}}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(8)
        dot = label("●", "")
        dot.setStyleSheet(f"color:{STATUS_COLORS.get(job.status, '#8FA3BF')};font-size:13px;")
        head.addWidget(dot)
        title = label(job.title or job.tool_id, "RowTitle")
        title.setToolTip(job.tool_id)
        head.addWidget(title, 1)
        digits = lambda text: ui_number(text, lang)  # noqa: E731 - display only
        meta = label(f"{_status_text(job.status, lang)}"
                     + (f" · {digits(f'{job.elapsed:.1f}s')}" if job.elapsed else "")
                     + (f" · {digits(_ago(job.created, lang))}" if job.status in ("done", "failed", "cancelled") else "")
                     + (f" · {t('jb.restored')}" if getattr(job, "paused", False) else ""),
                     "Hint")
        head.addWidget(meta)
        layout.addLayout(head)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.setValue(job.percent() if job.status != "running" or job.total else 0)
        if job.status == "running" and not job.total:
            self._bar.setRange(0, 0)
        if job.status == "done" and not job.total:
            self._bar.setVisible(False)
        layout.addWidget(self._bar)

        message = (job.error or job.message or "").strip().splitlines()
        detail = menu = None
        detail = message[0][:110] if message else ""
        if not detail and getattr(job, "needs_secrets", False):
            detail = t("jb.needs_secret")
        menu = QHBoxLayout()
        menu.setSpacing(6)
        self._message = label(detail, "FieldHelp")
        self._message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        menu.addWidget(self._message, 1)
        buttons = menu
        if job.active:
            cancel = QPushButton(t("jb.cancel"))
            cancel.setObjectName("Ghost")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(lambda: self.cancelRequested.emit(job.id))
            buttons.addWidget(cancel)
        else:
            if job.outputs:
                open_button = QPushButton(t("tp.open_output"))
                open_button.setObjectName("Ghost")
                open_button.clicked.connect(lambda: self.openRequested.emit(job.id))
                buttons.addWidget(open_button)
            again = QPushButton(t("jb.again"))
            again.setObjectName("Ghost")
            again.clicked.connect(lambda: self.rerunRequested.emit(job.id))
            buttons.addWidget(again)
        for index in range(menu.count()):
            widget = menu.itemAt(index).widget()
            if isinstance(widget, QPushButton):
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addLayout(menu)

    def update_from(self, job) -> None:
        self.job = job
        if job.status == "running":
            self._bar.setRange(0, 100 if job.total else 0)
            if job.total:
                self._bar.setValue(job.percent())
        elif job.status == "done":
            self._bar.setRange(0, 100)
            self._bar.setValue(100)
        text = (job.error or job.message or "").strip().splitlines()
        self._message.setText(text[0][:110] if text else "")
        if job.status == "done" and not job.total:
            self._bar.setVisible(False)


class JobsDrawer(QWidget):
    """Slide-in panel listing the job queue, newest first."""

    closed = Signal()
    openToolRequested = Signal(str, dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self.manager = None
        self._rows: dict = {}
        self.setVisible(False)

        self._card = Card(self)
        self._card.setFixedWidth(520)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(22, 18, 22, 16)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        self._title = label(t("jb.title"), "H1")
        header.addWidget(self._title)
        header.addStretch(1)
        self._stats = label("", "Hint")
        header.addWidget(self._stats)
        close_button = QPushButton("✕")
        close_button.setObjectName("Ghost")
        close_button.setFixedWidth(36)
        close_button.clicked.connect(self.close_panel)
        header.addWidget(close_button)
        card_layout.addLayout(header)

        self._subtitle = label(t("jb.subtitle"), "FieldHelp", wrap=True)
        card_layout.addWidget(self._subtitle)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 6, 0)
        self._body_layout.setSpacing(JOB_ROW_SPACING)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(430)
        scroll.setWidget(self._body)
        self._scroll = scroll
        card_layout.addWidget(scroll, 1)

        card_layout.addWidget(divider())
        footer = QHBoxLayout()
        # Only visible when the previous session left jobs behind (B8).
        self._resume = QPushButton(t("jb.resume", 0))
        self._resume.setObjectName("Primary")
        self._resume.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resume.clicked.connect(self._resume_queue)
        self._resume.setVisible(False)
        footer.addWidget(self._resume)
        self._clear = QPushButton(t("jb.clear"))
        self._clear.setObjectName("Ghost")
        self._clear.clicked.connect(self._clear_finished)
        footer.addWidget(self._clear)
        footer.addStretch(1)
        done = QPushButton(t("st.close"))
        done.setObjectName("Primary")
        done.clicked.connect(self.close_panel)
        footer.addWidget(done)
        card_layout.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(170)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._tick = QTimer(self)
        self._tick.setInterval(900)
        self._tick.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ setup
    def attach(self, manager, lang: str = "fa") -> None:
        self.manager = manager
        self.lang = lang
        manager.jobAdded.connect(lambda _job: self._schedule_refresh())
        manager.jobUpdated.connect(lambda _job: self._schedule_refresh())
        manager.jobFinished.connect(lambda _job: self._schedule_refresh())

    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette
        self._title.setText(t("jb.title"))
        self._subtitle.setText(t("jb.subtitle"))
        self._clear.setText(t("jb.clear"))
        self.refresh()

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.apply_palette(self.palette_ref)

    def _schedule_refresh(self) -> None:
        if self.isVisible():
            QTimer.singleShot(40, self.refresh)

    # ----------------------------------------------------------------- render
    def refresh(self) -> None:
        if self.manager is None:
            return
        jobs = self.manager.jobs()[:40]
        stats = self.manager.stats()
        num = lambda value: ui_number(value, self.lang)  # noqa: E731 - display only
        self._stats.setText(
            (f"{num(stats['active'])} active · {num(stats['done'])} done · {num(stats['failed'])} failed")
            if self.lang == "en" else
            (f"{num(stats['active'])} فعال · {num(stats['done'])} انجام‌شده · {num(stats['failed'])} خطا"))
        resumable = self.manager.has_resumable()
        self._resume.setVisible(resumable)
        if resumable:
            self._resume.setText(t("jb.resume", len(self.manager.resumable())))
        self._clear.setEnabled(bool(stats["done"] or stats["failed"] or stats["cancelled"]))
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = {}
        if not jobs:
            self._body_layout.addWidget(label(t("jb.empty"), "Hint", wrap=True))
            self._body_layout.addStretch(1)
            self._scroll.setFixedHeight(120)
            return
        for job in jobs:
            row = JobRow(job, self.lang, self.palette_ref)
            row.cancelRequested.connect(self.manager.cancel)
            row.openRequested.connect(self._open_output)
            row.rerunRequested.connect(self._rerun)
            self._body_layout.addWidget(row)
            self._rows[job.id] = row
        self._body_layout.addStretch(1)
        visible = min(len(self._rows), 5)
        self._scroll.setFixedHeight(
            max(120, min(visible * JOB_ROW_HEIGHT + (visible - 1) * JOB_ROW_SPACING + 4, 430)))

    def _open_output(self, job_id: str) -> None:
        job = self.manager.get(job_id) if self.manager else None
        if job is None or not job.outputs:
            return
        first = job.outputs[0]
        if len(job.outputs) == 1:
            open_path(first)
        else:
            reveal_path(first)

    def _rerun(self, job_id: str) -> None:
        job = self.manager.get(job_id) if self.manager else None
        if job is not None:
            self.openToolRequested.emit(job.tool_id, dict(job.values or {}))

    def _resume_queue(self) -> None:
        if self.manager is not None:
            self.manager.resume_pending()
        self.refresh()

    def _clear_finished(self) -> None:
        if self.manager is not None:
            self.manager.clear_finished()
        self.refresh()

    # ------------------------------------------------------------------- show
    def open_panel(self) -> None:
        self.refresh()
        self.setVisible(True)
        self.raise_()
        self._tick.start()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def close_panel(self) -> None:
        self._tick.stop()
        self.setVisible(False)
        self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)
