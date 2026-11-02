"""Smart drop overlay: drop files anywhere and WinGenius proposes the right tools."""
from __future__ import annotations

from pathlib import Path
from typing import List

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..core import suggest, theme
from ..core.i18n import t
from ..core.registry import category as get_category, get as get_tool
from ..core.theme import Palette
from .widgets import Card, label, pixmap_icon


ROW_HEIGHT = 58
ROW_SPACING = 7


class ToolSuggestion(QFrame):
    """Clickable row: icon + tool name + why it matches."""

    picked = Signal(str)

    def __init__(self, spec, palette: Palette, lang: str, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("SuggestionRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"#SuggestionRow{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:12px;}}"
            f"#SuggestionRow:hover{{border:1px solid {palette.accent};}}")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        cat = get_category(spec.category)
        badge = QLabel()
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{cat.color}22;border-radius:10px;")
        badge.setPixmap(pixmap_icon(spec.icon, QColor(cat.color), 28))
        row.addWidget(badge)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title_label = label(spec.name_for(lang), "RowTitle")
        title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(title_label)
        description = spec.desc_for(lang)
        if len(description) > 62:
            description = description[:61] + "…"
        desc_label = label(f"{cat.name_for(lang)} · {description}", "FieldHelp")
        desc_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(desc_label)
        row.addLayout(text_box, 1)
        arrow = QLabel("‹" if lang == "fa" else "›")
        arrow.setStyleSheet(f"color:{palette.text_dim};font-size:20px;")
        row.addWidget(arrow)

    def mousePressEvent(self, _event) -> None:
        self.picked.emit(self.spec.id)


class DropZone(QWidget):
    """Center card shown while files hover over the window."""

    toolChosen = Signal(str, list)
    browseRequested = Signal()
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self._paths: List[str] = []
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        self._card = Card(self)
        self._card.setFixedWidth(520)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(24, 20, 24, 18)
        card.setSpacing(10)

        header = QHBoxLayout()
        self._title = label(t("dz.title"), "H1")
        header.addWidget(self._title)
        header.addStretch(1)
        close_button = QPushButton("✕")
        close_button.setObjectName("Ghost")
        close_button.setFixedWidth(36)
        close_button.clicked.connect(self.close_panel)
        header.addWidget(close_button)
        card.addLayout(header)

        self._subtitle = label("", "FieldHelp", wrap=True)
        card.addWidget(self._subtitle)

        self._rows_holder = QWidget()
        self._rows = QVBoxLayout(self._rows_holder)
        self._rows.setContentsMargins(0, 2, 4, 2)
        self._rows.setSpacing(ROW_SPACING)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(356)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(self._rows_holder)
        self._scroll = scroll
        card.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self._browse = QPushButton(t("dz.browse"))
        self._browse.setObjectName("Ghost")
        self._browse.clicked.connect(self.browseRequested.emit)
        footer.addWidget(self._browse)
        footer.addStretch(1)
        self._all = QPushButton(t("dz.all"))
        self._all.setObjectName("Primary")
        self._all.clicked.connect(lambda: self._search_more())
        footer.addWidget(self._all)
        card.addLayout(footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(150)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------------ state
    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette
        self._title.setText(t("dz.title"))
        self._browse.setText(t("dz.browse"))
        self._all.setText(t("dz.all"))
        if self._paths:
            self.show_files(self._paths)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.apply_palette(self.palette_ref)

    @property
    def paths(self) -> List[str]:
        return list(self._paths)

    # ----------------------------------------------------------------- render
    def show_files(self, paths: List[str]) -> None:
        self._paths = [str(p) for p in paths]
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        names = suggest.describe_files(self._paths)
        count = len(self._paths)
        self._subtitle.setText(
            (f"{count} file(s): {names}" if self.lang == "en" else f"{count} فایل: {names}")
            + "\n" + t("dz.hint"))
        tool_ids = suggest.tools_for_files(self._paths, limit=6)
        added = 0
        for tool_id in tool_ids:
            spec = get_tool(tool_id)
            if spec is None:
                continue
            row = ToolSuggestion(spec, self.palette_ref, self.lang)
            row.picked.connect(lambda chosen: self.toolChosen.emit(chosen, self._paths))
            self._rows.addWidget(row)
            added += 1
        if not added:
            self._rows.addWidget(label(t("dz.none"), "Hint", wrap=True))
        self._rows.addStretch(1)
        visible = min(max(added, 1), 5)
        self._scroll.setFixedHeight(4 + visible * ROW_HEIGHT + (visible - 1) * ROW_SPACING)
        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _search_more(self) -> None:
        self.toolChosen.emit("", self._paths)

    def close_panel(self) -> None:
        self._paths = []
        self.setVisible(False)
        self.closed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)
