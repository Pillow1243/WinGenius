"""Reusable widgets: toasts, path pickers, colour buttons, labeled rows."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..core import theme
from ..core.i18n import t
from ..core.theme import Palette
from .icons import draw_icon


# --------------------------------------------------------------------- toast
class Toast(QWidget):
    """Small self-hiding notification pill."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._text = ""
        self._kind = "info"
        self._opacity = 0.0
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_toast)
        self._palette_ref: Optional[Palette] = None

    def set_palette_ref(self, palette: Palette) -> None:
        self._palette_ref = palette
        self.update()

    def show_toast(self, text: str, kind: str = "info", msec: int = 2600) -> None:
        self._text = text
        self._kind = kind
        self._width_hint = 0
        try:
            from PySide6.QtGui import QFontMetrics

            metrics = QFontMetrics(theme.make_font(10.5, 600))
            self._width_hint = min(600, max(240, metrics.horizontalAdvance(text) + 56))
        except Exception:
            self._width_hint = 320
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(msec)
        self.update()

    def hide_toast(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()
        QTimer.singleShot(260, self._maybe_hide)

    def _maybe_hide(self) -> None:
        if self._effect.opacity() < 0.05:
            self.hide()

    def _reposition(self) -> None:
        if not self.parent():
            return
        parent_rect = self.parent().rect()
        width = min(600, max(220, getattr(self, "_width_hint", 320) or 320))
        self.resize(width, 44)
        # bottom-centre: never covers the title bar or an open sheet's header
        self.move((parent_rect.width() - width) // 2, max(60, parent_rect.height() - 78))

    def sizeHint(self) -> QSize:
        return QSize(320, 44)

    def paintEvent(self, _event) -> None:
        palette = self._palette_ref or theme.build_palette("aurora", "#5B8CFF")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        accent = {
            "info": palette.accent,
            "success": palette.success,
            "error": palette.danger,
            "warning": palette.warning,
        }.get(self._kind, palette.accent)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.fillPath(path, QColor(palette.panel))
        pen = painter.pen()
        pen.setColor(QColor(theme.alpha(accent, 0.75)))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.setPen(QColor(accent))
        painter.setFont(theme.make_font(10.5, 600))
        metrics = painter.fontMetrics()
        text = metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, int(rect.width()) - 28)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# --------------------------------------------------------------- icon button
def icon_button(icon: str, tooltip: str = "", size: int = 34, color: Optional[QColor] = None) -> QPushButton:
    button = QPushButton()
    button.setObjectName("IconButton")
    button.setToolTip(tooltip)
    button.setFixedSize(QSize(size, size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIcon(_make_icon(icon, color or QColor("#C8D2EA"), size))
    button.setIconSize(QSize(int(size * 0.55), int(size * 0.55)))
    return button


def _make_icon(name: str, color: QColor, size: int = 32) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_icon(painter, name, QRectF(2, 2, size - 4, size - 4), color, max(1.2, size / 16))
    painter.end()
    return QIcon(pixmap)


def pixmap_icon(name: str, color: QColor, size: int = 48) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_icon(painter, name, QRectF(size * 0.1, size * 0.1, size * 0.8, size * 0.8), color, max(1.4, size / 18))
    painter.end()
    return pixmap


# ------------------------------------------------------------------ path edit
class PathEdit(QWidget):
    """Line edit + browse button, aware of file / files / dir / save kinds."""

    changed = Signal()

    def __init__(self, kind: str, filt: str = "", multiple: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.filt = filt or "همه فایل‌ها (*.*)"
        self.multiple = multiple
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(lambda *_: self.changed.emit())
        layout.addWidget(self.edit, 1)
        self.button = QPushButton()
        self.button.setObjectName("Ghost")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.button, 0)
        self.retranslate()

    def retranslate(self) -> None:
        if self.kind == "dir":
            self.button.setText(t("tp.browse_dir"))
            self.edit.setPlaceholderText(t("tp.browse_dir"))
        elif self.kind == "save":
            self.button.setText(t("tp.save_as"))
            self.edit.setPlaceholderText(t("tp.save_as"))
        else:
            self.button.setText(t("tp.browse"))
            self.edit.setPlaceholderText(t("tp.browse"))

    def _browse(self) -> None:
        folder = str(Path(self.edit.text()).parent) if self.edit.text().strip() else str(Path.home())
        if self.kind == "dir":
            chosen = QFileDialog.getExistingDirectory(self, t("tp.browse_dir"), folder)
            if chosen:
                self.edit.setText(chosen)
        elif self.kind == "save":
            chosen, _ = QFileDialog.getSaveFileName(self, t("tp.save_as"), self.edit.text() or folder, self.filt)
            if chosen:
                self.edit.setText(chosen)
        elif self.multiple or self.kind == "files":
            chosen, _ = QFileDialog.getOpenFileNames(self, t("tp.browse"), folder, self.filt)
            if chosen:
                current = [p for p in self.edit.text().split("|") if p.strip()]
                merged = current + [p for p in chosen if p not in current]
                self.edit.setText("|".join(merged))
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, t("tp.browse"), folder, self.filt)
            if chosen:
                self.edit.setText(chosen)

    def value(self):
        text = self.edit.text().strip()
        if self.kind == "files":
            return [p for p in text.split("|") if p.strip()]
        return text

    def set_value(self, value) -> None:
        if isinstance(value, (list, tuple)):
            self.edit.setText("|".join(str(v) for v in value))
        else:
            self.edit.setText("" if value is None else str(value))

    def add_paths(self, paths: List[str]) -> None:
        if self.kind in ("files",) or self.multiple:
            current = [p for p in self.edit.text().split("|") if p.strip()]
            merged = current + [p for p in paths if p not in current]
            self.edit.setText("|".join(merged))
        elif self.kind in ("file", "dir", "save") and paths:
            self.edit.setText(paths[0])


# ---------------------------------------------------------------- colour pick
class ColorButton(QPushButton):
    def __init__(self, color: str = "#FFFFFF", parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.clicked.connect(self._pick)
        self._refresh()

    def _refresh(self) -> None:
        self.setText(self._color.name().upper())
        self.setStyleSheet(
            f"background:{self._color.name()};color:{'#000' if self._color.lightness() > 140 else '#fff'};"
            "border:1px solid rgba(255,255,255,0.25);border-radius:10px;font-weight:700;"
        )

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, "Color")
        if chosen.isValid():
            self._color = chosen
            self._refresh()

    def value(self) -> str:
        return self._color.name()

    def set_value(self, value) -> None:
        color = QColor(str(value))
        if color.isValid():
            self._color = color
            self._refresh()


# --------------------------------------------------------------------- misc
class Card(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


PERSIAN_RANGE = range(0x0600, 0x0700)
RLM = "\u200f"


def rtl_text(text: str) -> str:
    """Force a right-to-left paragraph when the string mixes scripts.

    Qt picks the paragraph direction from the first strong character, so a label
    like "PDFهای اسکنشده" lays out left-to-right and lands the Latin run in the
    wrong place. A leading right-to-left mark fixes the paragraph direction
    without changing a single visible glyph.
    """
    if not text or text.startswith(RLM):
        return text
    if any(ord(char) in PERSIAN_RANGE for char in text):
        return RLM + text
    return text


def label(text: str, object_name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(rtl_text(text))
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def elide(text: str, font: QFont, width: int) -> str:
    from PySide6.QtGui import QFontMetrics

    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, width)
