"""Vector icon set drawn with QPainter (crisp at any DPI, no font needed).

Usage:  draw_icon(painter, "pdf", QRectF(x, y, 24, 24), QColor("#fff"), 1.8)
"""
from __future__ import annotations

import math
from typing import Callable, Dict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

BRUSH_KINDS = {"fill"}


def _pen(p: QPainter, color: QColor, w: float) -> QPen:
    pen = QPen(color, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    return pen


# ------------------------------------------------------------------ primitives
def _page(p: QPainter, r: QRectF) -> QPainterPath:
    fold = r.width() * 0.30
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.10, r.top())
    path.lineTo(r.right() - fold, r.top())
    path.lineTo(r.right() - r.width() * 0.06, r.top() + fold)
    path.lineTo(r.right() - r.width() * 0.06, r.bottom())
    path.lineTo(r.left() + r.width() * 0.10, r.bottom())
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(
        QPointF(r.right() - fold, r.top()),
        QPointF(r.right() - fold, r.top() + fold),
    )
    p.drawLine(
        QPointF(r.right() - fold, r.top() + fold),
        QPointF(r.right() - r.width() * 0.06, r.top() + fold),
    )
    return path


def _lines(p: QPainter, r: QRectF, ratios, inset=0.22) -> None:
    for i, ratio in enumerate(ratios):
        y = r.top() + r.height() * (0.34 + i * 0.16)
        p.drawLine(
            QPointF(r.left() + r.width() * inset, y),
            QPointF(r.left() + r.width() * (inset + ratio), y),
        )


# ---------------------------------------------------------------------- icons
def icon_pdf(p, r, c, w):
    _page(p, r)
    _lines(p, r, (0.5, 0.62, 0.4))
    p.drawLine(QPointF(r.left() + r.width() * 0.3, r.bottom() - r.height() * 0.16),
               QPointF(r.left() + r.width() * 0.7, r.bottom() - r.height() * 0.16))


def icon_doc(p, r, c, w):
    _page(p, r)
    _lines(p, r, (0.6, 0.68, 0.5, 0.44))


def icon_image(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.08, r.height() * 0.16,
                                 -r.width() * 0.08, -r.height() * 0.16),
                      r.width() * 0.12, r.width() * 0.12)
    p.drawEllipse(QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.35), w * 0.9, w * 0.9)
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.16, r.bottom() - r.height() * 0.2)
    path.lineTo(r.left() + r.width() * 0.44, r.top() + r.height() * 0.52)
    path.lineTo(r.left() + r.width() * 0.86, r.bottom() - r.height() * 0.2)
    p.drawPath(path)


def icon_film(p, r, c, w):
    body = r.adjusted(r.width() * 0.06, r.height() * 0.2, -r.width() * 0.06, -r.height() * 0.2)
    p.drawRoundedRect(body, r.width() * 0.1, r.width() * 0.1)
    for i in range(3):
        x = body.left() + body.width() * (0.08 + i * 0.1)
        p.drawPoint(QPointF(x, body.top() + body.height() * 0.22))
        p.drawPoint(QPointF(x, body.bottom() - body.height() * 0.22))
    tri = QPolygonF([
        QPointF(body.left() + body.width() * 0.42, body.top() + body.height() * 0.3),
        QPointF(body.left() + body.width() * 0.42, body.bottom() - body.height() * 0.3),
        QPointF(body.left() + body.width() * 0.78, body.center().y()),
    ])
    p.drawPolygon(tri)


def icon_music(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.62, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.62, r.bottom() - r.height() * 0.32))
    p.drawLine(QPointF(r.left() + r.width() * 0.62, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.86, r.top() + r.height() * 0.14))
    p.drawLine(QPointF(r.left() + r.width() * 0.86, r.top() + r.height() * 0.14),
               QPointF(r.left() + r.width() * 0.86, r.bottom() - r.height() * 0.38))
    p.drawEllipse(QPointF(r.left() + r.width() * 0.46, r.bottom() - r.height() * 0.28),
                  r.width() * 0.17, r.width() * 0.13)


def icon_folder(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.08, r.top() + r.height() * 0.72)
    path.lineTo(r.left() + r.width() * 0.08, r.top() + r.height() * 0.26)
    path.lineTo(r.left() + r.width() * 0.42, r.top() + r.height() * 0.26)
    path.lineTo(r.left() + r.width() * 0.52, r.top() + r.height() * 0.38)
    path.lineTo(r.right() - r.width() * 0.08, r.top() + r.height() * 0.38)
    path.lineTo(r.right() - r.width() * 0.08, r.top() + r.height() * 0.72)
    path.closeSubpath()
    p.drawPath(path)


def icon_zip(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.18, r.height() * 0.14,
                                 -r.width() * 0.18, -r.height() * 0.14),
                      r.width() * 0.08, r.width() * 0.08)
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.14),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.14))
    for i in range(4):
        y = r.top() + r.height() * (0.22 + i * 0.1)
        p.drawLine(QPointF(r.center().x() - r.width() * 0.06, y),
                   QPointF(r.center().x() + r.width() * 0.06, y))


def icon_hash(p, r, c, w):
    for x in (0.36, 0.64):
        p.drawLine(QPointF(r.left() + r.width() * x, r.top() + r.height() * 0.18),
                   QPointF(r.left() + r.width() * (x - 0.06), r.bottom() - r.height() * 0.18))
    for y in (0.36, 0.64):
        p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * y),
                   QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * y))


def icon_shield(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.center().x(), r.top() + r.height() * 0.1)
    path.lineTo(r.right() - r.width() * 0.16, r.top() + r.height() * 0.26)
    path.lineTo(r.right() - r.width() * 0.2, r.top() + r.height() * 0.62)
    path.cubicTo(
        r.right() - r.width() * 0.24, r.bottom() - r.height() * 0.2,
        r.center().x(), r.bottom() - r.height() * 0.06,
        r.center().x(), r.bottom() - r.height() * 0.06,
    )
    path.cubicTo(
        r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.2,
        r.left() + r.width() * 0.2, r.top() + r.height() * 0.62,
        r.left() + r.width() * 0.16, r.top() + r.height() * 0.26,
    )
    path.closeSubpath()
    p.drawPath(path)
    check = QPainterPath()
    check.moveTo(r.center().x() - r.width() * 0.12, r.center().y())
    check.lineTo(r.center().x() - r.width() * 0.02, r.center().y() + r.height() * 0.12)
    check.lineTo(r.center().x() + r.width() * 0.14, r.center().y() - r.height() * 0.12)
    p.drawPath(check)


def icon_key(p, r, c, w):
    p.drawEllipse(QPointF(r.left() + r.width() * 0.3, r.center().y()),
                  r.width() * 0.16, r.width() * 0.16)
    p.drawLine(QPointF(r.left() + r.width() * 0.44, r.center().y()),
               QPointF(r.right() - r.width() * 0.14, r.center().y()))
    p.drawLine(QPointF(r.right() - r.width() * 0.28, r.center().y()),
               QPointF(r.right() - r.width() * 0.28, r.center().y() + r.height() * 0.16))
    p.drawLine(QPointF(r.right() - r.width() * 0.16, r.center().y()),
               QPointF(r.right() - r.width() * 0.16, r.center().y() + r.height() * 0.12))


def icon_lock(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.16, r.height() * 0.42,
                                 -r.width() * 0.16, -r.height() * 0.12),
                      r.width() * 0.1, r.width() * 0.1)
    arc = QRectF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.16,
                 r.width() * 0.48, r.height() * 0.48)
    p.drawArc(arc, 0, 180 * 16)


def icon_globe(p, r, c, w):
    p.drawEllipse(r.adjusted(r.width() * 0.1, r.height() * 0.1,
                             -r.width() * 0.1, -r.height() * 0.1))
    p.drawEllipse(r.adjusted(r.width() * 0.3, r.height() * 0.1,
                             -r.width() * 0.3, -r.height() * 0.1))
    p.drawLine(QPointF(r.left() + r.width() * 0.1, r.center().y()),
               QPointF(r.right() - r.width() * 0.1, r.center().y()))


def icon_wifi(p, r, c, w):
    for i, rad in enumerate((0.42, 0.28, 0.14)):
        rect = QRectF(r.center().x() - r.width() * rad, r.top() + r.height() * 0.18,
                      r.width() * rad * 2, r.height() * rad * 2)
        p.drawArc(rect, 45 * 16, 90 * 16)
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.72), w * 0.7, w * 0.7)


def icon_monitor(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.08, r.height() * 0.16,
                                 -r.width() * 0.08, -r.height() * 0.36),
                      r.width() * 0.08, r.width() * 0.08)
    p.drawLine(QPointF(r.center().x(), r.bottom() - r.height() * 0.36),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.2))
    p.drawLine(QPointF(r.center().x() - r.width() * 0.16, r.bottom() - r.height() * 0.18),
               QPointF(r.center().x() + r.width() * 0.16, r.bottom() - r.height() * 0.18))


def icon_gear(p, r, c, w):
    cx, cy = r.center().x(), r.center().y()
    outer, inner = r.width() * 0.36, r.width() * 0.16
    pts = []
    for i in range(8):
        ang = math.radians(i * 45)
        pts.append(QPointF(cx + math.cos(ang) * outer, cy + math.sin(ang) * outer))
        ang2 = math.radians(i * 45 + 22.5)
        pts.append(QPointF(cx + math.cos(ang2) * (outer * 0.72), cy + math.sin(ang2) * (outer * 0.72)))
    p.drawPolygon(QPolygonF(pts))
    p.drawEllipse(QPointF(cx, cy), inner, inner)


def icon_window(p, r, c, w):
    body = r.adjusted(r.width() * 0.1, r.height() * 0.16, -r.width() * 0.1, -r.height() * 0.16)
    p.drawRoundedRect(body, r.width() * 0.08, r.width() * 0.08)
    p.drawLine(QPointF(body.left(), body.top() + body.height() * 0.32),
               QPointF(body.right(), body.top() + body.height() * 0.32))
    p.drawLine(QPointF(body.center().x(), body.top() + body.height() * 0.32),
               QPointF(body.center().x(), body.bottom()))


def icon_power(p, r, c, w):
    arc = QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.16,
                 r.width() * 0.68, r.height() * 0.68)
    p.drawArc(arc, -60 * 16, 300 * 16)
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.14),
               QPointF(r.center().x(), r.center().y() - r.height() * 0.02))


def icon_qr(p, r, c, w):
    s = r.width() * 0.26
    for dx, dy in ((0.1, 0.1), (0.64, 0.1), (0.1, 0.64)):
        p.drawRect(QRectF(r.left() + r.width() * dx, r.top() + r.height() * dy, s, s))
    for dx, dy in ((0.2, 0.2), (0.74, 0.2), (0.2, 0.74)):
        p.drawPoint(QPointF(r.left() + r.width() * dx, r.top() + r.height() * dy))
    p.drawRect(QRectF(r.left() + r.width() * 0.64, r.top() + r.height() * 0.64, s, s))


def icon_color(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.center().x(), r.top() + r.height() * 0.12)
    path.cubicTo(r.right() - r.width() * 0.12, r.top() + r.height() * 0.5,
                 r.right() - r.width() * 0.24, r.bottom() - r.height() * 0.14,
                 r.center().x(), r.bottom() - r.height() * 0.14)
    path.cubicTo(r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.14,
                 r.left() + r.width() * 0.12, r.top() + r.height() * 0.5,
                 r.center().x(), r.top() + r.height() * 0.12)
    p.drawPath(path)


def icon_clock(p, r, c, w):
    p.drawEllipse(r.adjusted(r.width() * 0.12, r.height() * 0.12,
                             -r.width() * 0.12, -r.height() * 0.12))
    p.drawLine(r.center(), QPointF(r.center().x(), r.center().y() - r.height() * 0.2))
    p.drawLine(r.center(), QPointF(r.center().x() + r.width() * 0.16, r.center().y() + r.height() * 0.08))


def icon_clipboard(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.16, r.height() * 0.18,
                                 -r.width() * 0.16, -r.height() * 0.12),
                      r.width() * 0.08, r.width() * 0.08)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.1,
                             r.width() * 0.32, r.height() * 0.16),
                      r.width() * 0.05, r.width() * 0.05)
    for i in range(3):
        y = r.top() + r.height() * (0.46 + i * 0.14)
        p.drawLine(QPointF(r.left() + r.width() * 0.3, y),
                   QPointF(r.right() - r.width() * 0.3, y))


def icon_convert(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.34),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.34))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.34),
               QPointF(r.right() - r.width() * 0.34, r.top() + r.height() * 0.22))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.34),
               QPointF(r.right() - r.width() * 0.34, r.top() + r.height() * 0.46))
    p.drawLine(QPointF(r.right() - r.width() * 0.16, r.bottom() - r.height() * 0.34),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.34))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.34),
               QPointF(r.left() + r.width() * 0.34, r.bottom() - r.height() * 0.22))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.34),
               QPointF(r.left() + r.width() * 0.34, r.bottom() - r.height() * 0.46))


def icon_scan(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.1, r.top() + r.height() * 0.5),
               QPointF(r.right() - r.width() * 0.1, r.top() + r.height() * 0.5))
    for dx, dy, sx, sy in (
        (0.1, 0.1, 1, 1), (0.9, 0.1, -1, 1), (0.1, 0.9, 1, -1), (0.9, 0.9, -1, -1)
    ):
        x = r.left() + r.width() * dx
        y = r.top() + r.height() * dy
        p.drawLine(QPointF(x, y), QPointF(x + r.width() * 0.18 * sx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + r.height() * 0.18 * sy))


def icon_star(p, r, c, w):
    cx, cy = r.center().x(), r.center().y()
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rad = r.width() * (0.4 if i % 2 == 0 else 0.17)
        pts.append(QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
    p.drawPolygon(QPolygonF(pts))


def icon_search(p, r, c, w):
    p.drawEllipse(QPointF(r.center().x() - r.width() * 0.04, r.center().y() - r.height() * 0.04),
                  r.width() * 0.24, r.width() * 0.24)
    p.drawLine(QPointF(r.center().x() + r.width() * 0.14, r.center().y() + r.height() * 0.14),
               QPointF(r.right() - r.width() * 0.14, r.bottom() - r.height() * 0.14))


def icon_note(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.4))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.4, r.bottom() - r.height() * 0.2))


def icon_camera(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.08, r.height() * 0.3,
                                 -r.width() * 0.08, -r.height() * 0.18),
                      r.width() * 0.1, r.width() * 0.1)
    p.drawEllipse(r.center(), r.width() * 0.14, r.width() * 0.14)
    p.drawLine(QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.3),
               QPointF(r.left() + r.width() * 0.42, r.top() + r.height() * 0.2))


def icon_trash(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.28),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.28))
    p.drawLine(QPointF(r.left() + r.width() * 0.4, r.top() + r.height() * 0.18),
               QPointF(r.right() - r.width() * 0.4, r.top() + r.height() * 0.18))
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.26, r.top() + r.height() * 0.3)
    path.lineTo(r.left() + r.width() * 0.32, r.bottom() - r.height() * 0.14)
    path.lineTo(r.right() - r.width() * 0.32, r.bottom() - r.height() * 0.14)
    path.lineTo(r.right() - r.width() * 0.26, r.top() + r.height() * 0.3)
    p.drawPath(path)


def icon_ruler(p, r, c, w):
    p.save()
    p.translate(r.center())
    p.rotate(-35)
    p.drawRect(QRectF(-r.width() * 0.4, -r.height() * 0.16, r.width() * 0.8, r.height() * 0.32))
    for i in range(4):
        x = -r.width() * 0.3 + i * r.width() * 0.2
        p.drawLine(QPointF(x, -r.height() * 0.16), QPointF(x, -r.height() * 0.02))
    p.restore()


def icon_battery(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.1, r.height() * 0.28,
                                 -r.width() * 0.2, -r.height() * 0.28),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.center().y() - r.height() * 0.08),
               QPointF(r.right() - r.width() * 0.1, r.center().y() - r.height() * 0.08))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.center().y() + r.height() * 0.08),
               QPointF(r.right() - r.width() * 0.1, r.center().y() + r.height() * 0.08))


def icon_terminal(p, r, c, w):
    p.drawRoundedRect(r.adjusted(r.width() * 0.08, r.height() * 0.16,
                                 -r.width() * 0.08, -r.height() * 0.16),
                      r.width() * 0.1, r.width() * 0.1)
    p.drawLine(QPointF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.4),
               QPointF(r.left() + r.width() * 0.38, r.top() + r.height() * 0.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.38, r.top() + r.height() * 0.5),
               QPointF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.6))
    p.drawLine(QPointF(r.left() + r.width() * 0.46, r.top() + r.height() * 0.62),
               QPointF(r.left() + r.width() * 0.68, r.top() + r.height() * 0.62))


def icon_crop(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.1),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.1, r.bottom() - r.height() * 0.2))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.bottom() - r.height() * 0.1),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.1, r.top() + r.height() * 0.2))


def icon_cloud(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.3)
    path.cubicTo(r.left() + r.width() * 0.06, r.bottom() - r.height() * 0.3,
                 r.left() + r.width() * 0.1, r.top() + r.height() * 0.42,
                 r.left() + r.width() * 0.3, r.top() + r.height() * 0.44)
    path.cubicTo(r.left() + r.width() * 0.38, r.top() + r.height() * 0.16,
                 r.right() - r.width() * 0.24, r.top() + r.height() * 0.2,
                 r.right() - r.width() * 0.22, r.top() + r.height() * 0.46)
    path.cubicTo(r.right() - r.width() * 0.02, r.bottom() - r.height() * 0.48,
                 r.right() - r.width() * 0.06, r.bottom() - r.height() * 0.3,
                 r.right() - r.width() * 0.24, r.bottom() - r.height() * 0.3)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.5),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.14))


def icon_text(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.16, r.top() + r.height() * 0.2))
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.2),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.14))
    p.drawLine(QPointF(r.center().x() - r.width() * 0.16, r.bottom() - r.height() * 0.14),
               QPointF(r.center().x() + r.width() * 0.16, r.bottom() - r.height() * 0.14))


def icon_list(p, r, c, w):
    for i in range(4):
        y = r.top() + r.height() * (0.24 + i * 0.18)
        p.drawPoint(QPointF(r.left() + r.width() * 0.18, y))
        p.drawLine(QPointF(r.left() + r.width() * 0.34, y),
                   QPointF(r.right() - r.width() * 0.16, y))


def icon_speed(p, r, c, w):
    rect = r.adjusted(r.width() * 0.1, r.height() * 0.2, -r.width() * 0.1, -r.height() * 0.16)
    p.drawArc(rect, 0, 180 * 16)
    p.drawLine(rect.center(), QPointF(rect.center().x() + r.width() * 0.2,
                                      rect.center().y() - r.height() * 0.16))


def icon_download(p, r, c, w):
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.16),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.36))
    p.drawLine(QPointF(r.center().x(), r.bottom() - r.height() * 0.36),
               QPointF(r.center().x() - r.width() * 0.16, r.bottom() - r.height() * 0.5))
    p.drawLine(QPointF(r.center().x(), r.bottom() - r.height() * 0.36),
               QPointF(r.center().x() + r.width() * 0.16, r.bottom() - r.height() * 0.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.18, r.bottom() - r.height() * 0.18),
               QPointF(r.right() - r.width() * 0.18, r.bottom() - r.height() * 0.18))




def icon_x(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.26),
               QPointF(r.right() - r.width() * 0.26, r.bottom() - r.height() * 0.26))
    p.drawLine(QPointF(r.right() - r.width() * 0.26, r.top() + r.height() * 0.26),
               QPointF(r.left() + r.width() * 0.26, r.bottom() - r.height() * 0.26))


def icon_back(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.22, r.center().y()),
               QPointF(r.right() - r.width() * 0.2, r.center().y()))
    p.drawLine(QPointF(r.left() + r.width() * 0.22, r.center().y()),
               QPointF(r.left() + r.width() * 0.44, r.center().y() - r.height() * 0.2))
    p.drawLine(QPointF(r.left() + r.width() * 0.22, r.center().y()),
               QPointF(r.left() + r.width() * 0.44, r.center().y() + r.height() * 0.2))


def icon_chevron_right(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.38, r.top() + r.height() * 0.24),
               QPointF(r.right() - r.width() * 0.34, r.center().y()))
    p.drawLine(QPointF(r.right() - r.width() * 0.34, r.center().y()),
               QPointF(r.left() + r.width() * 0.38, r.bottom() - r.height() * 0.24))


def icon_chevron_left(p, r, c, w):
    p.drawLine(QPointF(r.right() - r.width() * 0.38, r.top() + r.height() * 0.24),
               QPointF(r.left() + r.width() * 0.34, r.center().y()))
    p.drawLine(QPointF(r.left() + r.width() * 0.34, r.center().y()),
               QPointF(r.right() - r.width() * 0.38, r.bottom() - r.height() * 0.24))


def icon_sliders(p, r, c, w):
    for i, x in enumerate((0.28, 0.5, 0.72)):
        px = r.left() + r.width() * x
        p.drawLine(QPointF(px, r.top() + r.height() * 0.2), QPointF(px, r.bottom() - r.height() * 0.2))
        py = r.top() + r.height() * (0.36 + i * 0.16)
        p.drawEllipse(QPointF(px, py), w * 1.6, w * 1.6)


def icon_minus(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.26, r.center().y()),
               QPointF(r.right() - r.width() * 0.26, r.center().y()))


def icon_grid(p, r, c, w):
    for row in range(2):
        for col in range(2):
            p.drawRoundedRect(
                QRectF(r.left() + r.width() * (0.14 + col * 0.4), r.top() + r.height() * (0.14 + row * 0.4),
                       r.width() * 0.3, r.height() * 0.3),
                r.width() * 0.06, r.width() * 0.06)


# ------------------------------------------------------- v3 icon additions
def icon_chart(p, r, c, w):
    base = r.bottom() - r.height() * 0.12
    for index, ratio in enumerate((0.45, 0.75, 0.3, 0.62)):
        x = r.left() + r.width() * (0.16 + index * 0.19)
        height = r.height() * ratio
        p.drawLine(QPointF(x, base), QPointF(x, base - height))
    p.drawLine(QPointF(r.left() + r.width() * 0.1, base), QPointF(r.right() - r.width() * 0.08, base))


def icon_layers(p, r, c, w):
    for offset, ratio in ((0.62, 1.0), (0.44, 0.86), (0.26, 0.72)):
        y = r.top() + r.height() * (1 - offset) * 0.8
        path = QPolygonF([
            QPointF(r.left() + r.width() * 0.14, y),
            QPointF(r.center().x(), y - r.height() * ratio * 0.22),
            QPointF(r.right() - r.width() * 0.14, y),
            QPointF(r.center().x(), y + r.height() * ratio * 0.22),
        ])
        p.drawPolygon(path)


def icon_refresh(p, r, c, w):
    rect = QRectF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.18,
                  r.width() * 0.64, r.height() * 0.64)
    p.drawArc(rect, 40 * 16, 280 * 16)
    tip = QPointF(rect.right() - rect.width() * 0.12, rect.top() + rect.height() * 0.1)
    p.drawLine(tip, QPointF(tip.x() - rect.width() * 0.22, tip.y()))
    p.drawLine(tip, QPointF(tip.x(), tip.y() + rect.height() * 0.22))


def icon_sparkles(p, r, c, w):
    cx, cy = r.center().x(), r.center().y()
    for dx, dy, size in ((0.0, -0.12, 0.26), (-0.24, 0.18, 0.15), (0.24, 0.16, 0.15)):
        x = cx + r.width() * dx
        y = cy + r.height() * dy
        arm = r.width() * size
        p.drawLine(QPointF(x - arm, y), QPointF(x + arm, y))
        p.drawLine(QPointF(x, y - arm), QPointF(x, y + arm))


def icon_brush(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.72, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.36, r.top() + r.height() * 0.56))
    p.drawEllipse(QPointF(r.left() + r.width() * 0.28, r.top() + r.height() * 0.72),
                  r.width() * 0.16, r.height() * 0.14)


def icon_sun(p, r, c, w):
    p.drawEllipse(r.center(), r.width() * 0.2, r.height() * 0.2)
    for index in range(8):
        angle = index * math.pi / 4
        x1 = r.center().x() + math.cos(angle) * r.width() * 0.29
        y1 = r.center().y() + math.sin(angle) * r.height() * 0.29
        x2 = r.center().x() + math.cos(angle) * r.width() * 0.4
        y2 = r.center().y() + math.sin(angle) * r.height() * 0.4
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def icon_palette(p, r, c, w):
    p.drawEllipse(QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.16,
                         r.width() * 0.7, r.height() * 0.68))
    for dx, dy in ((0.34, 0.36), (0.56, 0.34), (0.44, 0.6)):
        p.drawEllipse(QPointF(r.left() + r.width() * dx, r.top() + r.height() * dy),
                      r.width() * 0.05, r.height() * 0.05)


def icon_eye(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.12, r.center().y())
    path.quadTo(r.center().x(), r.top() + r.height() * 0.18,
                r.right() - r.width() * 0.12, r.center().y())
    path.quadTo(r.center().x(), r.bottom() - r.height() * 0.18,
                r.left() + r.width() * 0.12, r.center().y())
    p.drawPath(path)
    p.drawEllipse(r.center(), r.width() * 0.12, r.height() * 0.12)


def icon_puzzle(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.16,
                             r.width() * 0.68, r.height() * 0.68),
                      r.width() * 0.1, r.width() * 0.1)
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.16),
                  r.width() * 0.1, r.height() * 0.1)


def icon_code(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.26),
               QPointF(r.left() + r.width() * 0.14, r.center().y()))
    p.drawLine(QPointF(r.left() + r.width() * 0.14, r.center().y()),
               QPointF(r.left() + r.width() * 0.34, r.bottom() - r.height() * 0.26))
    p.drawLine(QPointF(r.right() - r.width() * 0.34, r.top() + r.height() * 0.26),
               QPointF(r.right() - r.width() * 0.14, r.center().y()))
    p.drawLine(QPointF(r.right() - r.width() * 0.14, r.center().y()),
               QPointF(r.right() - r.width() * 0.34, r.bottom() - r.height() * 0.26))
    p.drawLine(QPointF(r.center().x() + r.width() * 0.06, r.top() + r.height() * 0.2),
               QPointF(r.center().x() - r.width() * 0.06, r.bottom() - r.height() * 0.2))


def icon_database(p, r, c, w):
    for ratio in (0.22, 0.5, 0.78):
        y = r.top() + r.height() * ratio
        p.drawEllipse(QRectF(r.left() + r.width() * 0.2, y - r.height() * 0.1,
                             r.width() * 0.6, r.height() * 0.2))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.22),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.22))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.22),
               QPointF(r.right() - r.width() * 0.2, r.bottom() - r.height() * 0.22))


def icon_tag(p, r, c, w):
    path = QPolygonF([
        QPointF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.16),
        QPointF(r.center().x() + r.width() * 0.1, r.top() + r.height() * 0.16),
        QPointF(r.right() - r.width() * 0.14, r.center().y()),
        QPointF(r.center().x(), r.bottom() - r.height() * 0.14),
        QPointF(r.left() + r.width() * 0.16, r.center().y()),
    ])
    p.drawPolygon(path)
    p.drawEllipse(QPointF(r.left() + r.width() * 0.32, r.top() + r.height() * 0.32),
                  r.width() * 0.05, r.height() * 0.05)


def icon_filter(p, r, c, w):
    path = QPolygonF([
        QPointF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.22),
        QPointF(r.right() - r.width() * 0.14, r.top() + r.height() * 0.22),
        QPointF(r.center().x() + r.width() * 0.1, r.center().y() + r.height() * 0.04),
        QPointF(r.center().x() + r.width() * 0.1, r.bottom() - r.height() * 0.16),
        QPointF(r.center().x() - r.width() * 0.1, r.bottom() - r.height() * 0.26),
        QPointF(r.center().x() - r.width() * 0.1, r.center().y() + r.height() * 0.04),
    ])
    p.drawPolygon(path)


def icon_broom(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.7, r.top() + r.height() * 0.18),
               QPointF(r.left() + r.width() * 0.46, r.top() + r.height() * 0.5))
    p.drawPolygon(QPolygonF([
        QPointF(r.left() + r.width() * 0.46, r.top() + r.height() * 0.5),
        QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.16),
        QPointF(r.left() + r.width() * 0.56, r.bottom() - r.height() * 0.3),
    ]))


def icon_link(p, r, c, w):
    p.drawEllipse(QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.34,
                         r.width() * 0.34, r.height() * 0.32))
    p.drawEllipse(QRectF(r.right() - r.width() * 0.48, r.top() + r.height() * 0.34,
                         r.width() * 0.34, r.height() * 0.32))
    p.drawLine(QPointF(r.left() + r.width() * 0.44, r.center().y()),
               QPointF(r.right() - r.width() * 0.44, r.center().y()))


def icon_report(p, r, c, w):
    _page(p, r)
    _lines(p, r, (0.4, 0.52, 0.3))


def icon_diff(p, r, c, w):
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.12),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.12))
    for index in range(3):
        y = r.top() + r.height() * (0.26 + index * 0.24)
        p.drawLine(QPointF(r.left() + r.width() * 0.14, y), QPointF(r.center().x() - r.width() * 0.1, y))
        p.drawLine(QPointF(r.center().x() + r.width() * 0.1, y), QPointF(r.right() - r.width() * 0.14, y))


def icon_print(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.42,
                             r.width() * 0.68, r.height() * 0.36),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawRect(QRectF(r.left() + r.width() * 0.3, r.top() + r.height() * 0.14,
                      r.width() * 0.4, r.height() * 0.28))
    p.drawRect(QRectF(r.left() + r.width() * 0.3, r.bottom() - r.height() * 0.3,
                      r.width() * 0.4, r.height() * 0.16))


def icon_network(p, r, c, w):
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.26),
                  r.width() * 0.1, r.height() * 0.1)
    for dx in (-0.26, 0.26):
        p.drawEllipse(QPointF(r.center().x() + r.width() * dx, r.bottom() - r.height() * 0.24),
                      r.width() * 0.1, r.height() * 0.1)
        p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.36),
                   QPointF(r.center().x() + r.width() * dx, r.bottom() - r.height() * 0.34))


def icon_archive(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.2,
                             r.width() * 0.64, r.height() * 0.62),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawLine(QPointF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.42),
               QPointF(r.right() - r.width() * 0.18, r.top() + r.height() * 0.42))
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.42),
               QPointF(r.center().x(), r.center().y() + r.height() * 0.06))


def icon_copy(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.2,
                             r.width() * 0.46, r.height() * 0.5),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.34,
                             r.width() * 0.46, r.height() * 0.5),
                      r.width() * 0.06, r.width() * 0.06)


def icon_pencil(p, r, c, w):
    p.drawPolygon(QPolygonF([
        QPointF(r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.18),
        QPointF(r.left() + r.width() * 0.74, r.top() + r.height() * 0.2),
        QPointF(r.right() - r.width() * 0.16, r.top() + r.height() * 0.42),
        QPointF(r.left() + r.width() * 0.4, r.bottom() - r.height() * 0.08),
    ]))


def icon_undo(p, r, c, w):
    rect = QRectF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.28,
                  r.width() * 0.6, r.height() * 0.48)
    p.drawArc(rect, 30 * 16, 230 * 16)
    tip = QPointF(rect.left() + rect.width() * 0.06, rect.center().y() + rect.height() * 0.34)
    p.drawPolyline(QPolygonF([
        QPointF(tip.x() - r.width() * 0.10, tip.y() - r.height() * 0.10),
        tip,
        QPointF(tip.x() + r.width() * 0.12, tip.y() + r.height() * 0.02),
    ]))


def icon_play(p, r, c, w):
    p.drawPolygon(QPolygonF([
        QPointF(r.left() + r.width() * 0.32, r.top() + r.height() * 0.2),
        QPointF(r.right() - r.width() * 0.2, r.center().y()),
        QPointF(r.left() + r.width() * 0.32, r.bottom() - r.height() * 0.2),
    ]))


def icon_check(p, r, c, w):
    p.drawPolyline(QPolygonF([
        QPointF(r.left() + r.width() * 0.22, r.center().y()),
        QPointF(r.left() + r.width() * 0.42, r.bottom() - r.height() * 0.28),
        QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.3),
    ]))


def icon_calendar(p, r, c, w):
    box = QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.18,
                 r.width() * 0.72, r.height() * 0.66)
    p.drawRoundedRect(box, r.width() * 0.08, r.width() * 0.08)
    p.drawLine(QPointF(box.left(), box.top() + box.height() * 0.28),
               QPointF(box.right(), box.top() + box.height() * 0.28))
    for ratio in (0.35, 0.62):
        p.drawLine(QPointF(box.left() + box.width() * 0.22, box.top() + box.height() * ratio),
                   QPointF(box.left() + box.width() * 0.22, box.top() + box.height() * ratio))
    p.drawLine(QPointF(box.left() + box.width() * 0.28, box.top()),
               QPointF(box.left() + box.width() * 0.28, box.top() - r.height() * 0.10))
    p.drawLine(QPointF(box.right() - box.width() * 0.28, box.top()),
               QPointF(box.right() - box.width() * 0.28, box.top() - r.height() * 0.10))


def icon_heart(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.center().x(), r.bottom() - r.height() * 0.16)
    path.cubicTo(r.left() - r.width() * 0.05, r.center().y() + r.height() * 0.12,
                 r.left() + r.width() * 0.14, r.top() + r.height() * 0.1,
                 r.center().x(), r.top() + r.height() * 0.3)
    path.cubicTo(r.right() - r.width() * 0.14, r.top() + r.height() * 0.1,
                 r.right() + r.width() * 0.05, r.center().y() + r.height() * 0.12,
                 r.center().x(), r.bottom() - r.height() * 0.16)
    p.drawPath(path)


def icon_bell(p, r, c, w):
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.24, r.bottom() - r.height() * 0.3)
    path.cubicTo(r.left() + r.width() * 0.28, r.top() + r.height() * 0.3,
                 r.left() + r.width() * 0.34, r.top() + r.height() * 0.16,
                 r.center().x(), r.top() + r.height() * 0.16)
    path.cubicTo(r.right() - r.width() * 0.34, r.top() + r.height() * 0.16,
                 r.right() - r.width() * 0.28, r.top() + r.height() * 0.3,
                 r.right() - r.width() * 0.24, r.bottom() - r.height() * 0.3)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(r.left() + r.width() * 0.18, r.bottom() - r.height() * 0.3),
               QPointF(r.right() - r.width() * 0.18, r.bottom() - r.height() * 0.3))
    p.drawEllipse(QPointF(r.center().x(), r.bottom() - r.height() * 0.16),
                  r.width() * 0.08, r.height() * 0.07)


def icon_table(p, r, c, w):
    box = QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.2,
                 r.width() * 0.72, r.height() * 0.6)
    p.drawRoundedRect(box, r.width() * 0.07, r.width() * 0.07)
    p.drawLine(QPointF(box.left(), box.top() + box.height() * 0.3),
               QPointF(box.right(), box.top() + box.height() * 0.3))
    p.drawLine(QPointF(box.left() + box.width() * 0.38, box.top() + box.height() * 0.3),
               QPointF(box.left() + box.width() * 0.38, box.bottom()))
    for ratio in (0.52, 0.76):
        p.drawLine(QPointF(box.left(), box.top() + box.height() * ratio),
                   QPointF(box.right(), box.top() + box.height() * ratio))


def icon_sort(p, r, c, w):
    for index, width_ratio in enumerate((0.34, 0.5, 0.22)):
        y = r.top() + r.height() * (0.26 + index * 0.24)
        p.drawLine(QPointF(r.left() + r.width() * 0.16, y),
                   QPointF(r.left() + r.width() * (0.16 + width_ratio), y))
    p.drawPolyline(QPolygonF([
        QPointF(r.right() - r.width() * 0.26, r.top() + r.height() * 0.24),
        QPointF(r.right() - r.width() * 0.16, r.top() + r.height() * 0.36),
        QPointF(r.right() - r.width() * 0.26, r.top() + r.height() * 0.48),
    ]))
    p.drawLine(QPointF(r.right() - r.width() * 0.16, r.top() + r.height() * 0.36),
               QPointF(r.right() - r.width() * 0.16, r.bottom() - r.height() * 0.2))


def icon_file(p, r, c, w):
    _page(p, r)
    _lines(p, r, (0.56, 0.4))


def icon_scissors(p, r, c, w):
    p.drawLine(QPointF(r.left() + r.width() * 0.3, r.top() + r.height() * 0.18),
               QPointF(r.left() + r.width() * 0.64, r.bottom() - r.height() * 0.34))
    p.drawLine(QPointF(r.left() + r.width() * 0.7, r.top() + r.height() * 0.18),
               QPointF(r.left() + r.width() * 0.36, r.bottom() - r.height() * 0.34))
    p.drawEllipse(QPointF(r.left() + r.width() * 0.3, r.bottom() - r.height() * 0.22),
                  r.width() * 0.09, r.height() * 0.09)
    p.drawEllipse(QPointF(r.left() + r.width() * 0.7, r.bottom() - r.height() * 0.22),
                  r.width() * 0.09, r.height() * 0.09)


def icon_compare(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.12, r.top() + r.height() * 0.14,
                             r.width() * 0.5, r.height() * 0.62),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.38, r.top() + r.height() * 0.26,
                             r.width() * 0.5, r.height() * 0.62),
                      r.width() * 0.06, r.width() * 0.06)


def icon_history(p, r, c, w):
    icon_clock(p, r, c, w)
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.center().y() + r.height() * 0.1),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.16))


def icon_sync(p, r, c, w):
    box = QRectF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.22,
                 r.width() * 0.64, r.height() * 0.56)
    p.drawArc(box, 40 * 16, 150 * 16)
    p.drawArc(box, 220 * 16, 150 * 16)
    p.drawPolyline(QPolygonF([
        QPointF(box.right() - r.width() * 0.06, box.top()),
        QPointF(box.right() - r.width() * 0.02, box.top() + r.height() * 0.14),
        QPointF(box.right() - r.width() * 0.16, box.top() + r.height() * 0.17),
    ]))
    p.drawPolyline(QPolygonF([
        QPointF(box.left() + r.width() * 0.06, box.bottom()),
        QPointF(box.left() + r.width() * 0.02, box.bottom() - r.height() * 0.14),
        QPointF(box.left() + r.width() * 0.16, box.bottom() - r.height() * 0.17),
    ]))


def icon_maximize(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.2,
                             r.width() * 0.6, r.height() * 0.6),
                      r.width() * 0.07, r.width() * 0.07)


def icon_restore(p, r, c, w):
    """Two offset frames: the standard "restore down" glyph."""
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.3,
                             r.width() * 0.52, r.height() * 0.52),
                      r.width() * 0.06, r.width() * 0.06)
    p.drawLine(QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.3),
               QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.16))
    p.drawLine(QPointF(r.left() + r.width() * 0.34, r.top() + r.height() * 0.16),
               QPointF(r.left() + r.width() * 0.84, r.top() + r.height() * 0.16))
    p.drawLine(QPointF(r.left() + r.width() * 0.84, r.top() + r.height() * 0.16),
               QPointF(r.left() + r.width() * 0.84, r.top() + r.height() * 0.66))


def icon_square(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.18,
                             r.width() * 0.64, r.height() * 0.64),
                      r.width() * 0.08, r.width() * 0.08)


def icon_circle(p, r, c, w):
    p.drawEllipse(QPointF(r.center()), r.width() * 0.32, r.height() * 0.32)


def icon_info(p, r, c, w):
    p.drawEllipse(QPointF(r.center()), r.width() * 0.33, r.height() * 0.33)
    p.drawLine(QPointF(r.center().x(), r.center().y() + r.height() * 0.02),
               QPointF(r.center().x(), r.center().y() + r.height() * 0.18))
    p.drawPoint(QPointF(r.center().x(), r.center().y() - r.height() * 0.13))


def icon_fast(p, r, c, w):
    for offset in (0.0, 0.22):
        p.drawPolyline(QPolygonF([
            QPointF(r.left() + r.width() * (0.22 + offset), r.top() + r.height() * 0.22),
            QPointF(r.left() + r.width() * (0.44 + offset), r.center().y()),
            QPointF(r.left() + r.width() * (0.22 + offset), r.bottom() - r.height() * 0.22),
        ]))


def icon_stamp(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.16, r.bottom() - r.height() * 0.24,
                             r.width() * 0.68, r.height() * 0.14),
                      r.width() * 0.04, r.width() * 0.04)
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.34, r.bottom() - r.height() * 0.28)
    path.lineTo(r.left() + r.width() * 0.36, r.top() + r.height() * 0.34)
    path.cubicTo(r.left() + r.width() * 0.3, r.top() + r.height() * 0.16,
                 r.right() - r.width() * 0.3, r.top() + r.height() * 0.16,
                 r.right() - r.width() * 0.36, r.top() + r.height() * 0.34)
    path.lineTo(r.right() - r.width() * 0.34, r.bottom() - r.height() * 0.28)
    p.drawPath(path)


def icon_radar(p, r, c, w):
    centre = r.center()
    for scale in (0.32, 0.2):
        p.drawEllipse(centre, r.width() * scale, r.height() * scale)
    p.drawLine(centre, QPointF(centre.x() + r.width() * 0.24,
                               centre.y() - r.height() * 0.24))
    p.drawPoint(QPointF(centre.x() + r.width() * 0.12, centre.y() - r.height() * 0.22))


def icon_plug(p, r, c, w):
    body = QRectF(r.left() + r.width() * 0.3, r.top() + r.height() * 0.4,
                  r.width() * 0.4, r.height() * 0.3)
    p.drawRoundedRect(body, r.width() * 0.05, r.width() * 0.05)
    for offset in (0.42, 0.58):
        p.drawLine(QPointF(r.left() + r.width() * offset, r.top() + r.height() * 0.2),
                   QPointF(r.left() + r.width() * offset, body.top()))
    p.drawLine(QPointF(r.center().x(), body.bottom()),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.14))


def icon_disk(p, r, c, w):
    box = QRectF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.2,
                 r.width() * 0.6, r.height() * 0.58)
    p.drawEllipse(QRectF(box.left(), box.top(), box.width(), box.height() * 0.3))
    p.drawLine(QPointF(box.left(), box.top() + box.height() * 0.15),
               QPointF(box.left(), box.bottom() - box.height() * 0.15))
    p.drawLine(QPointF(box.right(), box.top() + box.height() * 0.15),
               QPointF(box.right(), box.bottom() - box.height() * 0.15))
    p.drawArc(QRectF(box.left(), box.bottom() - box.height() * 0.3, box.width(),
                     box.height() * 0.3), 180 * 16, 180 * 16)


def icon_alert(p, r, c, w):
    p.drawPolygon(QPolygonF([
        QPointF(r.center().x(), r.top() + r.height() * 0.16),
        QPointF(r.right() - r.width() * 0.12, r.bottom() - r.height() * 0.18),
        QPointF(r.left() + r.width() * 0.12, r.bottom() - r.height() * 0.18),
    ]))
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.42),
               QPointF(r.center().x(), r.top() + r.height() * 0.58))
    p.drawPoint(QPointF(r.center().x(), r.top() + r.height() * 0.68))


def icon_chip(p, r, c, w):
    body = QRectF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.26,
                  r.width() * 0.48, r.height() * 0.48)
    p.drawRoundedRect(body, r.width() * 0.06, r.width() * 0.06)
    p.drawRect(QRectF(body.left() + body.width() * 0.28, body.top() + body.height() * 0.28,
                      body.width() * 0.44, body.height() * 0.44))
    for offset in (0.38, 0.62):
        p.drawLine(QPointF(r.left() + r.width() * offset, r.top() + r.height() * 0.1),
                   QPointF(r.left() + r.width() * offset, body.top()))
        p.drawLine(QPointF(r.left() + r.width() * offset, body.bottom()),
                   QPointF(r.left() + r.width() * offset, r.bottom() - r.height() * 0.1))


def icon_bug(p, r, c, w):
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.32, r.top() + r.height() * 0.28,
                             r.width() * 0.36, r.height() * 0.46),
                      r.width() * 0.18, r.width() * 0.18)
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.28),
               QPointF(r.center().x(), r.top() + r.height() * 0.16))
    for ratio in (0.4, 0.56, 0.72):
        p.drawLine(QPointF(r.left() + r.width() * 0.14, r.top() + r.height() * ratio),
                   QPointF(r.left() + r.width() * 0.3, r.top() + r.height() * ratio))
        p.drawLine(QPointF(r.right() - r.width() * 0.3, r.top() + r.height() * ratio),
                   QPointF(r.right() - r.width() * 0.14, r.top() + r.height() * ratio))


def icon_persian(p, r, c, w):
    """Right-to-left script mark: a «ب»-style bowl with its dot and an RTL arrow above."""
    bowl = QPainterPath()
    x0, x1 = r.left() + r.width() * 0.14, r.right() - r.width() * 0.14
    top_y, base_y = r.top() + r.height() * 0.5, r.bottom() - r.height() * 0.3
    bowl.moveTo(x1, top_y)
    bowl.lineTo(x1, base_y - r.height() * 0.08)
    bowl.cubicTo(x1, base_y + r.height() * 0.06, x1 - r.width() * 0.08, base_y + r.height() * 0.08,
                 x1 - r.width() * 0.16, base_y + r.height() * 0.08)
    bowl.lineTo(x0 + r.width() * 0.16, base_y + r.height() * 0.08)
    bowl.cubicTo(x0 + r.width() * 0.06, base_y + r.height() * 0.08, x0, base_y + r.height() * 0.02, x0, base_y - r.height() * 0.08)
    bowl.lineTo(x0, base_y - r.height() * 0.18)
    p.drawPath(bowl)
    p.setBrush(c)
    p.drawEllipse(QPointF(r.center().x(), r.bottom() - r.height() * 0.08), w * 0.7, w * 0.7)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # direction arrow (right → left), the essence of RTL text handling
    y = r.top() + r.height() * 0.24
    p.drawLine(QPointF(x1 - r.width() * 0.06, y), QPointF(x0 + r.width() * 0.1, y))
    p.drawLine(QPointF(x0 + r.width() * 0.1, y), QPointF(x0 + r.width() * 0.26, y - r.height() * 0.14))
    p.drawLine(QPointF(x0 + r.width() * 0.1, y), QPointF(x0 + r.width() * 0.26, y + r.height() * 0.14))


def icon_wrench(p, r, c, w):
    """Repair: open-end wrench, head top-right, handle to bottom-left."""
    p.save()
    p.translate(r.center())
    p.rotate(45)
    s = r.width()
    head = QRectF(-s * 0.21, -s * 0.46, s * 0.42, s * 0.42)
    path = QPainterPath()
    path.arcMoveTo(head, 125)
    path.arcTo(head, 125, 290)                     # ring with the jaw opening on top
    jaw_y = head.top() + s * 0.1
    path.lineTo(s * 0.08, jaw_y)
    path.lineTo(s * 0.08, head.top() + s * 0.2)
    path.lineTo(-s * 0.08, head.top() + s * 0.2)
    path.lineTo(-s * 0.08, jaw_y)
    path.closeSubpath()
    p.drawPath(path)
    handle = QRectF(-s * 0.085, -s * 0.1, s * 0.17, s * 0.54)
    p.drawRoundedRect(handle, s * 0.085, s * 0.085)
    p.restore()


def icon_font(p, r, c, w):
    """Typography: a large A with a small a — the text_to_pdf / font tools."""
    left, right = r.left() + r.width() * 0.12, r.left() + r.width() * 0.56
    top, bottom = r.top() + r.height() * 0.16, r.bottom() - r.height() * 0.14
    apex = QPointF((left + right) / 2, top)
    p.drawLine(QPointF(left, bottom), apex)
    p.drawLine(apex, QPointF(right, bottom))
    p.drawLine(QPointF(left + (right - left) * 0.22, top + (bottom - top) * 0.64),
               QPointF(right - (right - left) * 0.22, top + (bottom - top) * 0.64))
    small = QRectF(r.right() - r.width() * 0.4, bottom - r.height() * 0.36, r.width() * 0.28, r.height() * 0.3)
    p.drawEllipse(small.adjusted(0, small.height() * 0.1, 0, 0))
    p.drawLine(QPointF(small.right(), small.top() + small.height() * 0.1), QPointF(small.right(), small.bottom()))



ICONS: Dict[str, Callable] = {
    "persian": icon_persian, "wrench": icon_wrench, "font": icon_font,
    "pdf": icon_pdf, "doc": icon_doc, "image": icon_image, "film": icon_film,
    "music": icon_music, "folder": icon_folder, "zip": icon_zip, "hash": icon_hash,
    "shield": icon_shield, "key": icon_key, "lock": icon_lock, "globe": icon_globe,
    "wifi": icon_wifi, "monitor": icon_monitor, "gear": icon_gear, "window": icon_window,
    "power": icon_power, "qr": icon_qr, "color": icon_color, "clock": icon_clock,
    "clipboard": icon_clipboard, "convert": icon_convert, "scan": icon_scan,
    "star": icon_star, "search": icon_search, "note": icon_note, "camera": icon_camera,
    "trash": icon_trash, "ruler": icon_ruler, "battery": icon_battery,
    "terminal": icon_terminal, "crop": icon_crop, "cloud": icon_cloud, "text": icon_text,
    "list": icon_list, "speed": icon_speed, "download": icon_download,
    "x": icon_x, "back": icon_back, "chevron_right": icon_chevron_right,
    "chevron_left": icon_chevron_left, "sliders": icon_sliders, "minus": icon_minus,
    "maximize": icon_maximize, "restore": icon_restore,
    "grid": icon_grid,
    "chart": icon_chart, "layers": icon_layers, "refresh": icon_refresh, "sparkles": icon_sparkles,
    "brush": icon_brush, "sun": icon_sun, "palette": icon_palette, "eye": icon_eye,
    "puzzle": icon_puzzle, "code": icon_code, "database": icon_database, "tag": icon_tag,
    "filter": icon_filter, "broom": icon_broom, "link": icon_link, "report": icon_report,
    "diff": icon_diff, "print": icon_print, "network": icon_network, "archive": icon_archive,
    "copy": icon_copy, "pencil": icon_pencil,
    "undo": icon_undo, "play": icon_play, "check": icon_check,
    "calendar": icon_calendar, "heart": icon_heart, "bell": icon_bell,
    "table": icon_table, "sort": icon_sort, "file": icon_file, "scissors": icon_scissors,
    "compare": icon_compare, "history": icon_history, "sync": icon_sync,
    "square": icon_square, "circle": icon_circle, "info": icon_info, "fast": icon_fast,
    "stamp": icon_stamp, "radar": icon_radar, "plug": icon_plug, "disk": icon_disk,
    "alert": icon_alert, "chip": icon_chip, "bug": icon_bug,
}


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.8) -> None:
    """Draw one of the built-in icons scaled into `rect`."""
    func = ICONS.get(name) or ICONS.get("gear")
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _pen(painter, color, width)
    try:
        func(painter, rect, color, width)
    finally:
        painter.restore()


def icon_names() -> list[str]:
    return sorted(ICONS)
