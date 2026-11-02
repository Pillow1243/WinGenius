"""Shared skeleton for the full-screen sheets (dashboard, plan, workflows, automation).

Every sheet is a centred card with a title row, a scrollable body and a fade-in,
so the whole app keeps one visual language without repeating 60 lines of layout
code in each panel.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core import theme
from ..core.theme import Palette
from .widgets import Card, label


class _EdgeFade(QWidget):
    """Gradient strip that softly fades rows clipped by the scroll viewport.

    Without it a row half-scrolled under the sheet header looks like a rendering
    glitch (two overlapping lines). With it the body reads like it continues
    beyond the edge — the same trick macOS and iOS use for list edges.
    """

    height_hint = 14
    peak_alpha = 205

    def __init__(self, parent: QWidget, top: bool) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._top = top
        self._color = QColor("#141A2E")
        self.setFixedHeight(self.height_hint)
        self.hide()

    def set_color(self, color: str) -> None:
        if QColor(color).isValid():
            self._color = QColor(color)
            self.update()

    def paintEvent(self, event) -> None:      # noqa: N802 (Qt signature)
        painter = QPainter(self)
        rect = self.rect()
        if self._top:
            gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        else:
            gradient = QLinearGradient(0, rect.bottom(), 0, rect.top())
        solid = QColor(self._color)
        solid.setAlpha(self.peak_alpha)
        clear = QColor(self._color)
        clear.setAlpha(0)
        gradient.setColorAt(0.0, solid)
        gradient.setColorAt(1.0, clear)
        painter.fillRect(rect, QBrush(gradient))


class OverlaySheet(QWidget):
    """Base class for the sheet overlays."""

    closed = Signal()

    title_key = ""
    subtitle_key = ""
    fallback_title = ""
    fallback_subtitle = ""
    card_width = 640
    body_max_height = 560

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self._faded_once = False
        self.setVisible(False)

        self._card = Card(self)
        self._card.setFixedWidth(self.card_width)
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setContentsMargins(24, 18, 24, 18)
        self._card_layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._title = label(self._text(self.title_key, self.fallback_title), "H1")
        header.addWidget(self._title)
        header.addStretch(1)
        self._header_extra = QWidget()
        self._header_extra_layout = QHBoxLayout(self._header_extra)
        self._header_extra_layout.setContentsMargins(0, 0, 0, 0)
        self._header_extra_layout.setSpacing(6)
        header.addWidget(self._header_extra)
        self._close_button = QPushButton("✕")
        self._close_button.setObjectName("Ghost")
        self._close_button.setFixedWidth(36)
        self._close_button.clicked.connect(self.close_panel)
        header.addWidget(self._close_button)
        self._card_layout.addLayout(header)

        self._subtitle = label(self._text(self.subtitle_key, self.fallback_subtitle), "FieldHelp", wrap=True)
        self._card_layout.addWidget(self._subtitle)

        self._body = QWidget()
        self.body_layout = QVBoxLayout(self._body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(10)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(self.body_max_height)
        self._scroll.setWidget(self._body)
        self._card_layout.addWidget(self._scroll, 1)
        self._edge_top = _EdgeFade(self._scroll, top=True)
        self._edge_bottom = _EdgeFade(self._scroll, top=False)
        self._scroll.installEventFilter(self)
        bar = self._scroll.verticalScrollBar()
        bar.valueChanged.connect(self._sync_edge_fades)
        bar.rangeChanged.connect(lambda *_: self._sync_edge_fades())

        self._footer = QHBoxLayout()
        self._footer.setSpacing(8)
        self._card_layout.addLayout(self._footer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        self._ensure_fade()

    # ------------------------------------------------------------------ i18n
    def _text(self, key: str, fallback: str) -> str:
        if not key:
            return fallback
        from ..core.i18n import t

        try:
            text = t(key)
        except Exception:
            text = key
        return text if text and text != key else fallback

    def retranslate(self) -> None:
        self._title.setText(self._text(self.title_key, self.fallback_title))
        self._subtitle.setText(self._text(self.subtitle_key, self.fallback_subtitle))

    # -------------------------------------------------------------- lifecycle
    def open_panel(self) -> None:
        self._refresh()
        self._ensure_fade()
        self.setVisible(True)
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._paint_edge_colour()
        self._place_edge_fades()

    # -------------------------------------------------------------------- fade
    def _ensure_fade(self) -> QGraphicsOpacityEffect:
        """Create the fade-in effect if the last fade already dropped it."""
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            self._opacity = effect
            return effect
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0 if self._faded_once else 1.0)
        self.setGraphicsEffect(effect)
        self._opacity = effect
        self._fade = QPropertyAnimation(effect, b"opacity", self)
        self._fade.setDuration(170)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._drop_fade)
        return effect

    def _drop_fade(self) -> None:
        """Remove the opacity effect once the fade is done.

        A QGraphicsOpacityEffect renders the sheet through a cached pixmap, so any
        later reflow (scrolling the body, switching language, resizing) can leave
        ghost text behind at the old position. Dropping the effect when the
        animation ends gives crisp, ordinary painting — and is cheaper too.
        """
        if isinstance(self.graphicsEffect(), QGraphicsOpacityEffect):
            self.setGraphicsEffect(None)
            self._faded_once = True
            self.update()

    def close_panel(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette
        self.setStyleSheet("")
        self._paint_edge_colour()
        self._refresh()
        self._place_edge_fades()

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.retranslate()
        self._refresh()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ to override
    def _refresh(self) -> None:
        """Rebuild the body (subclasses override)."""

    # ----------------------------------------------------------------- helpers
    def clear_body(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                _clear(item.layout())

    def add_footer_button(self, text: str, slot, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Primary" if primary else "Ghost")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        self._footer.addWidget(button)
        return button

    def add_header_button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("Pill")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        self._header_extra_layout.addWidget(button)
        return button

    # -------------------------------------------------------- scroll edges
    def eventFilter(self, watched, event) -> bool:      # noqa: N802 (Qt signature)
        if watched is self._scroll and event.type() == QEvent.Type.Resize:
            self._place_edge_fades()
        return super().eventFilter(watched, event)

    def _edge_geometry(self) -> None:
        width, height = self._scroll.width(), self._scroll.height()
        strip = _EdgeFade.height_hint
        self._edge_top.setGeometry(0, 0, width, strip)
        self._edge_bottom.setGeometry(0, height - strip, width, strip)
        self._edge_top.raise_()
        self._edge_bottom.raise_()

    def _place_edge_fades(self) -> None:
        self._edge_geometry()
        self._sync_edge_fades()

    def _sync_edge_fades(self) -> None:
        """Show a fade only on the side that actually hides content."""
        bar = self._scroll.verticalScrollBar()
        value, maximum = bar.value(), bar.maximum()
        self._edge_geometry()
        self._edge_top.setVisible(maximum > 0 and value > 0)
        self._edge_bottom.setVisible(maximum > 0 and value < maximum)

    def _paint_edge_colour(self) -> None:
        colour = getattr(self.palette_ref, "panel", "#141A2E")
        self._edge_top.set_color(colour)
        self._edge_bottom.set_color(colour)

    def fit_body_height(self, extra: int = 6) -> int:
        """Size the scroll area to whatever the body actually needs (clamped).

        The layout hint is often stale right after widgets are inserted, so the
        measurement is repeated on the next event-loop turn.
        """
        self.body_layout.activate()
        needed = self.body_layout.sizeHint().height() + extra
        self.set_body_height(needed)
        QTimer.singleShot(0, lambda: self._refit_body(extra))
        return self._scroll.height()

    def _refit_body(self, extra: int = 6) -> None:
        try:
            self.body_layout.activate()
            self.set_body_height(self.body_layout.sizeHint().height() + extra)
        except RuntimeError:      # widget already destroyed
            pass

    def set_body_height(self, height: int) -> None:
        self._scroll.setFixedHeight(max(140, min(int(height), self.body_max_height)))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
