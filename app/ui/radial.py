"""The animated radial (circular) menu — the heart of the WinGenius UI.

Design goals
    * everything reachable in one or two clicks from the centre
    * smooth, GPU-cheap animations (a single QTimer drives all easing)
    * crisp custom painting, no font/emoji dependency (vector icons)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QToolTip, QWidget

from ..core import theme
from ..core.i18n import t
from ..core.theme import Palette
from .icons import draw_icon


@dataclass
class Node:
    """One clickable bubble on the ring."""

    id: str
    label: str
    icon: str = "gear"
    color: str = "#5B8CFF"
    sub: str = ""
    badge: str = ""
    kind: str = "tool"           # category | tool | nav
    appear: float = 0.0          # 0..1 animation state
    hover: float = 0.0
    press: float = 0.0
    delay_ms: int = 0


class RadialMenu(QWidget):
    """Circular node menu with page transitions, hover glow and a pulsing core."""

    nodeClicked = Signal(str)
    centerClicked = Signal()
    pageChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Radial tool menu")
        self.setMinimumSize(520, 520)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self._nodes: List[Node] = []
        self._pending: Optional[List[Node]] = None
        self._title = t("home.hint")
        self._subtitle = ""
        self._center_icon = "star"
        self._center_color = "#8EA2FF"
        self._allow_back = False
        self._animations = True
        self._glow = True
        self._hovered: Optional[int] = None
        self._pressed: Optional[int] = None
        self._rotation = 0.0
        self._pulse = 0.0
        self._page = 0
        self._pages = 1
        self._transition_start = 0.0
        self._fade = 1.0
        self._mouse = QPointF(-1000, -1000)
        self._last_tick = time.time()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(16)

    # ------------------------------------------------------------------ setup
    def apply_palette(self, palette: Palette, animations: bool = True, glow: bool = True) -> None:
        self.palette_ref = palette
        self._animations = animations
        self._glow = glow
        if animations:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def set_page_state(self, page: int, pages: int) -> None:
        """Set both the active page and total page count (no signal emitted)."""
        self._page = max(0, min(page, max(0, pages - 1)))
        self._pages = max(1, pages)
        self.update()

    def set_page_info(self, page: int, pages: int) -> None:
        self._page = max(0, page)
        self._pages = max(1, pages)
        self.update()

    # ------------------------------------------------------------------ nodes
    def set_nodes(
        self,
        nodes: List[Node],
        title: str = "",
        subtitle: str = "",
        center_icon: str = "star",
        center_color: str = "#8EA2FF",
        allow_back: bool = False,
        animate: bool = True,
    ) -> None:
        for index, node in enumerate(nodes):
            node.delay_ms = index * 42
            node.appear = 0.0
            node.hover = 0.0
        self._title = title or self._title
        self._subtitle = subtitle
        self._center_icon = center_icon
        self._center_color = center_color
        self._allow_back = allow_back
        self._hovered = None
        if animate and self._animations and self._nodes:
            self._pending = nodes
            self._transition_start = time.time()
            self._fade = 1.0
        else:
            self._nodes = nodes
            self._pending = None
            self._fade = 1.0
        if not self._timer.isActive() and self._animations:
            self._timer.start()
        self.update()

    # -------------------------------------------------------------- animation
    def _tick(self) -> None:
        now = time.time()
        dt = min(0.06, now - self._last_tick)
        self._last_tick = now
        busy = False

        if self._pending is not None:
            # fade out current nodes quickly, then swap in the new page
            self._fade -= dt / 0.16
            if self._fade <= 0.0:
                self._nodes = self._pending
                self._pending = None
                self._fade = 0.0
                self._transition_start = now
                busy = True
        elif self._fade < 1.0:
            self._fade = min(1.0, self._fade + dt / 0.22)
            busy = True

        elapsed_ms = (now - self._transition_start) * 1000.0
        for node in self._nodes:
            target = 1.0 if elapsed_ms >= node.delay_ms else 0.0
            if abs(node.appear - target) > 0.001:
                node.appear += (target - node.appear) * min(1.0, dt * 14.0)
                if abs(node.appear - target) < 0.004:
                    node.appear = target
                busy = True
            hover_target = 1.0 if node is self._hovered_node() else 0.0
            if abs(node.hover - hover_target) > 0.002:
                node.hover += (hover_target - node.hover) * min(1.0, dt * 12.0)
                if abs(node.hover - hover_target) < 0.004:
                    node.hover = hover_target
                busy = True

        if self._animations:
            self._rotation = (self._rotation + dt * 14.0) % 360.0
            self._pulse = (self._pulse + dt) % 3.2
            busy = busy or self.isVisible()

        if busy or self._animations:
            self.update()

    def _hovered_node(self) -> Optional[Node]:
        if self._hovered is None or self._hovered >= len(self._nodes):
            return None
        return self._nodes[self._hovered]

    # --------------------------------------------------------------- geometry
    def _center(self) -> QPointF:
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def _ring_radius(self) -> float:
        return min(self.width(), self.height()) * 0.315

    def _node_radius(self) -> float:
        return max(26.0, min(self.width(), self.height()) * 0.062)

    def _node_position(self, index: int, total: int) -> QPointF:
        center = self._center()
        radius = self._ring_radius()
        angle = -math.pi / 2 + (2 * math.pi * index / max(1, total))
        return QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p = self.palette_ref
        rect = QRectF(self.rect())
        center = self._center()

        # ---------------------------------------------------------- backdrop
        gradient = QRadialGradient(center, rect.width() * 0.72)
        gradient.setColorAt(0.0, QColor(theme.alpha(p.accent, 0.13 if p.dark else 0.08)))
        gradient.setColorAt(0.45, QColor(theme.alpha(p.accent2, 0.05)))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, gradient)

        # orbital decoration
        ring = self._ring_radius()
        if self._glow:
            self._draw_orbits(painter, center, ring)
        self._draw_core(painter, center)

        # ------------------------------------------------------------- nodes
        total = len(self._nodes)
        fade = max(0.0, min(1.0, self._fade))
        node_radius = self._node_radius()
        for index, node in enumerate(self._nodes):
            appear = max(0.0, min(1.0, node.appear))
            if appear <= 0.01:
                continue
            position = self._node_position(index, total)
            self._draw_node(painter, node, position, node_radius, appear * fade)

        if self._pages > 1:
            self._draw_pager(painter, center)

        # hint text
        if self._subtitle:
            painter.setPen(QColor(theme.alpha(p.text_dim, 0.95)))
            painter.setFont(theme.make_font(8.8, 500))
            hint_rect = QRectF(rect.left(), rect.bottom() - 28, rect.width(), 22)
            painter.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, self._subtitle)

    def _draw_orbits(self, painter: QPainter, center: QPointF, radius: float) -> None:
        p = self.palette_ref
        # static dotted circle
        pen = QPen(QColor(theme.alpha(p.text_dim, 0.16 if p.dark else 0.22)))
        pen.setWidthF(1.0)
        pen.setDashPattern([1.0, 7.0])
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)
        painter.drawEllipse(center, radius * 0.55, radius * 0.55)

        # rotating conic sweep
        sweep = QConicalGradient(center, -self._rotation)
        sweep.setColorAt(0.0, QColor(theme.alpha(p.accent, 0.55)))
        sweep.setColorAt(0.12, QColor(theme.alpha(p.accent, 0.0)))
        sweep.setColorAt(0.55, QColor(theme.alpha(p.accent2, 0.35)))
        sweep.setColorAt(0.68, QColor(theme.alpha(p.accent2, 0.0)))
        sweep.setColorAt(1.0, QColor(theme.alpha(p.accent, 0.55)))
        pen = QPen(sweep, 1.6)
        painter.setPen(pen)
        painter.drawEllipse(center, radius * 1.135, radius * 1.135)

        # outer ticks
        pen = QPen(QColor(theme.alpha(p.text_dim, 0.20)))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        for tick in range(72):
            angle = math.radians(tick * 5 + self._rotation * 0.4)
            inner = radius * 1.185
            outer = radius * (1.205 if tick % 6 else 1.225)
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * inner, center.y() + math.sin(angle) * inner),
                QPointF(center.x() + math.cos(angle) * outer, center.y() + math.sin(angle) * outer),
            )

    def _draw_core(self, painter: QPainter, center: QPointF) -> None:
        p = self.palette_ref
        core_radius = min(self.width(), self.height()) * 0.145
        accent = QColor(self._center_color)

        # pulse rings
        if self._animations and self._glow:
            for phase in (0.0, 1.6):
                progress = ((self._pulse + phase) % 3.2) / 3.2
                radius = core_radius * (1.0 + progress * 1.35)
                alpha = int(90 * (1.0 - progress) ** 1.6)
                if alpha <= 0:
                    continue
                pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha))
                pen.setWidthF(1.6)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(center, radius, radius)

        # glow
        glow = QRadialGradient(center, core_radius * 2.1)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(),
                                    120 if self._glow else 40))
        glow.setColorAt(0.55, QColor(accent.red(), accent.green(), accent.blue(), 26))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, core_radius * 2.1, core_radius * 2.1)

        # disc
        disc = QRadialGradient(QPointF(center.x(), center.y() - core_radius * 0.4), core_radius * 1.6)
        disc.setColorAt(0.0, QColor(theme.alpha(p.panel_alt, 1.0 if p.dark else 0.95)))
        disc.setColorAt(1.0, QColor(theme.alpha(p.bg1, 0.98)))
        painter.setBrush(disc)
        painter.setPen(QPen(QColor(theme.alpha(accent.name(), 0.55)), 1.4))
        painter.drawEllipse(center, core_radius, core_radius)

        # icon
        icon_size = core_radius * 0.78
        draw_icon(
            painter, self._center_icon,
            QRectF(center.x() - icon_size / 2, center.y() - icon_size * 0.86, icon_size, icon_size),
            QColor(accent), max(1.6, core_radius / 22),
        )

        # title
        painter.setPen(QColor(p.text))
        font = theme.make_font(10.5 if len(self._title) < 22 else 9.0, 700)
        painter.setFont(font)
        title_rect = QRectF(center.x() - core_radius * 1.55, center.y() + core_radius * 0.16,
                            core_radius * 3.1, 20)
        metrics = painter.fontMetrics()
        text = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, int(title_rect.width()))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, text)

        # subtitle / back hint
        painter.setFont(theme.make_font(8.0, 500))
        painter.setPen(QColor(theme.alpha(p.text_dim, 0.9)))
        sub_rect = QRectF(title_rect.left(), title_rect.bottom() - 1, title_rect.width(), 16)
        hint = t("center.back") if self._allow_back else ""
        if hint:
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, hint)

    def _draw_node(self, painter: QPainter, node: Node, position: QPointF, radius: float, appear: float) -> None:
        p = self.palette_ref
        accent = QColor(node.color)
        scale = 0.55 + 0.45 * appear
        hover = node.hover
        r = radius * scale * (1.0 + 0.12 * hover)
        alpha = int(255 * appear)
        center = position

        # glow
        if hover > 0.02 or self._glow:
            strength = (0.22 + 0.55 * hover) if self._glow else 0.0
            glow = QRadialGradient(center, r * 2.3)
            glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(),
                                        int(150 * strength * appear)))
            glow.setColorAt(0.6, QColor(accent.red(), accent.green(), accent.blue(),
                                        int(42 * strength * appear)))
            glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, r * 2.3, r * 2.3)

        # body
        body = QRadialGradient(QPointF(center.x(), center.y() - r * 0.5), r * 1.9)
        base = QColor(p.panel_alt if p.dark else p.panel)
        tint = 0.34 + 0.30 * hover
        body.setColorAt(0.0, QColor(
            int(base.red() * (1 - tint) + accent.red() * tint),
            int(base.green() * (1 - tint) + accent.green() * tint),
            int(base.blue() * (1 - tint) + accent.blue() * tint),
            alpha,
        ))
        body.setColorAt(1.0, QColor(
            int(base.red() * 0.85), int(base.green() * 0.85), int(base.blue() * 0.95), alpha))
        painter.setBrush(body)
        border_alpha = 0.35 + 0.55 * hover
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(),
                                   int(255 * border_alpha * appear)), 1.4))
        painter.drawEllipse(center, r, r)

        # icon
        icon_size = r * 1.02
        draw_icon(
            painter, node.icon,
            QRectF(center.x() - icon_size / 2, center.y() - icon_size / 2, icon_size, icon_size),
            QColor(accent.red(), accent.green(), accent.blue(), alpha),
            max(1.3, r / 17),
        )

        # label
        painter.setFont(theme.make_font(9.6 if node.kind == "category" else 9.0,
                                        700 if node.kind == "category" else 600))
        painter.setPen(QColor(theme.alpha(p.text, 1.0 if hover > 0.3 else 0.86)))
        label_rect = QRectF(center.x() - 78, center.y() + r + 6, 156, 30)
        metrics = painter.fontMetrics()
        text = metrics.elidedText(node.label, Qt.TextElideMode.ElideRight, 150)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)

        if node.badge:
            painter.setFont(theme.make_font(7.4, 600))
            badge_rect = QRectF(center.x() + r - 10, center.y() - r - 6, 34, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.alpha(accent.name(), 0.9)))
            painter.drawRoundedRect(badge_rect, 8, 8)
            painter.setPen(QColor("#0B1020"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, node.badge)

    def _pager_geometry(self):
        """Return (dots_rect, left_button_rect, right_button_rect)."""
        center = self._center()
        y = self.height() - 52
        dots = QRectF(center.x() - 60, y - 6, 120, 22)
        left = QRectF(center.x() - 108, y - 14, 32, 32)
        right = QRectF(center.x() + 76, y - 14, 32, 32)
        return dots, left, right

    def _draw_pager(self, painter: QPainter, center: QPointF) -> None:
        p = self.palette_ref
        dots, left_rect, right_rect = self._pager_geometry()
        for rect, direction, enabled in ((left_rect, -1, self._page > 0),
                                          (right_rect, 1, self._page < self._pages - 1)):
            painter.setPen(QPen(QColor(theme.alpha(p.accent if enabled else p.text_dim,
                                                   0.75 if enabled else 0.25)), 1.2))
            painter.setBrush(QColor(theme.alpha(p.panel, 0.85)))
            painter.drawRoundedRect(rect, 10, 10)
            draw_icon(painter, "chevron_left" if direction < 0 else "chevron_right",
                      rect.adjusted(8, 8, -8, -8),
                      QColor(theme.alpha(p.text if enabled else p.text_dim, 1.0 if enabled else 0.4)), 1.6)
        count = self._pages
        width = count * 14
        start = dots.center().x() - width / 2
        for index in range(count):
            color = QColor(p.accent) if index == self._page else QColor(theme.alpha(p.text_dim, 0.45))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            radius = 3.4 if index == self._page else 2.4
            painter.drawEllipse(QPointF(start + index * 14 + 5, dots.center().y()), radius, radius)

    # ----------------------------------------------------------------- mouse
    def _node_at(self, position: QPointF) -> Optional[int]:
        node_radius = self._node_radius()
        best: Optional[int] = None
        best_distance = node_radius * 1.5
        total = len(self._nodes)
        for index, node in enumerate(self._nodes):
            if node.appear < 0.4:
                continue
            center = self._node_position(index, total)
            distance = math.hypot(position.x() - center.x(), position.y() - center.y())
            if distance < best_distance:
                best, best_distance = index, distance
        return best

    def mouseMoveEvent(self, event) -> None:
        self._mouse = event.position()
        hovered = self._node_at(self._mouse)
        if hovered != self._hovered:
            self._hovered = hovered
            node = self._hovered_node()
            if node and node.sub:
                QToolTip.showText(event.globalPosition().toPoint(), f"{node.label}\n{node.sub}", self)
            else:
                QToolTip.hideText()
            self.setCursor(Qt.CursorShape.PointingHandCursor if hovered is not None else Qt.CursorShape.ArrowCursor)
            if not self._timer.isActive():
                self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._pages > 1:
                _dots, left_rect, right_rect = self._pager_geometry()
                if left_rect.contains(event.position()):
                    self.navigate_page(-1)
                    return
                if right_rect.contains(event.position()):
                    self.navigate_page(1)
                    return
            index = self._node_at(event.position())
            self._pressed = index
            if index is None and self._is_in_core(event.position()):
                self.centerClicked.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._node_at(event.position())
            if index is not None and index == self._pressed:
                node = self._nodes[index]
                self.nodeClicked.emit(node.id)
            self._pressed = None
        super().mouseReleaseEvent(event)

    def _is_in_core(self, position: QPointF) -> bool:
        center = self._center()
        core_radius = min(self.width(), self.height()) * 0.145
        return math.hypot(position.x() - center.x(), position.y() - center.y()) <= core_radius

    def wheelEvent(self, event) -> None:
        if self._pages <= 1:
            return
        delta = 1 if event.angleDelta().y() < 0 else -1
        self.navigate_page(delta)

    def navigate_page(self, direction: int) -> None:
        page = self._page + direction
        if 0 <= page < self._pages:
            self._page = page
            self.pageChanged.emit(self._page, self._pages)
            self.update()

    def showEvent(self, event) -> None:
        if self._animations and (self._pending is not None or self._nodes) and not self._timer.isActive():
            self._last_tick = time.time()
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_PageUp,):
            self.navigate_page(-1)
        elif key in (Qt.Key.Key_PageDown,):
            self.navigate_page(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            if self._nodes:
                self._hovered = ((self._hovered - 1) if self._hovered is not None else len(self._nodes) - 1) % len(self._nodes)
                self.update()
            else:
                self.navigate_page(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            if self._nodes:
                self._hovered = ((self._hovered + 1) if self._hovered is not None else 0) % len(self._nodes)
                self.update()
            else:
                self.navigate_page(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            node = self._hovered_node()
            if node is not None:
                self.nodeClicked.emit(node.id)
            else:
                self.centerClicked.emit()
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace) and self._allow_back:
            self.centerClicked.emit()
        else:
            super().keyPressEvent(event)

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(760, 720)
