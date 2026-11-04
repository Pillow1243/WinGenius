"""Render the WinGenius application icon (PNG + multi-size ICO) from vector code.

    QT_QPA_PLATFORM=offscreen python scripts/make_icon.py [--out app/assets]

The icon is drawn with QPainter so it is reproducible, resolution independent and
matches the in-app vector icon set: an aurora gradient tile, the signature radial
ring of the launcher, and a bold Vazirmatn "W" monogram with a genius spark.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QImage,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)

FONTS = ROOT / "app" / "assets" / "fonts"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _font_family() -> str:
    for name in ("Vazirmatn-ExtraBold.ttf", "Vazirmatn-Bold.ttf"):
        path = FONTS / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    return "Vazirmatn" if "Vazirmatn" in QFontDatabase.families() else QFont().family()


def render(size: int, *, family: str, margin_ratio: float = 0.0) -> QImage:
    """Draw the icon into a square `size` px image (supersampled ×2 for smooth edges)."""
    scale = 2
    s = size * scale
    img = QImage(s, s, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    pad = s * margin_ratio
    tile = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
    radius = tile.width() * 0.225

    # ---- tile: aurora gradient + soft top-left light + bottom vignette
    base = QLinearGradient(tile.topLeft(), tile.bottomRight())
    base.setColorAt(0.0, QColor("#1B2A6B"))
    base.setColorAt(0.45, QColor("#141A3E"))
    base.setColorAt(1.0, QColor("#2A135A"))
    tile_path = QPainterPath()
    tile_path.addRoundedRect(tile, radius, radius)
    p.fillPath(tile_path, base)

    glow = QRadialGradient(QPointF(tile.left() + tile.width() * 0.25, tile.top() + tile.height() * 0.12),
                           tile.width() * 0.9)
    glow.setColorAt(0.0, QColor(91, 140, 255, 120))
    glow.setColorAt(0.55, QColor(91, 140, 255, 18))
    glow.setColorAt(1.0, QColor(91, 140, 255, 0))
    p.fillPath(tile_path, glow)

    violet = QRadialGradient(QPointF(tile.right() - tile.width() * 0.1, tile.bottom()), tile.width() * 0.75)
    violet.setColorAt(0.0, QColor(255, 79, 216, 70))
    violet.setColorAt(1.0, QColor(255, 79, 216, 0))
    p.fillPath(tile_path, violet)

    # inner rim (gives the tile depth on light and dark backgrounds)
    p.setPen(QPen(QColor(255, 255, 255, 34), s * 0.006))
    p.setBrush(Qt.BrushStyle.NoBrush)
    inset = s * 0.004
    p.drawRoundedRect(tile.adjusted(inset, inset, -inset, -inset), radius - inset, radius - inset)

    # ---- the radial ring of the launcher
    cx, cy = tile.center().x(), tile.center().y() + tile.height() * 0.015
    ring_r = tile.width() * 0.335
    ring_pen = QPen(QColor(255, 255, 255, 46), s * 0.012)
    ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(ring_pen)
    p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)
    # eight nodes, alternating accent / white like the category bubbles
    accents = ("#5B8CFF", "#2BD9A0", "#FFC24B", "#FF5A6E", "#8B5CFF", "#22C9E6", "#FF8A3D", "#F15BB5")
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        x, y = cx + ring_r * math.cos(ang), cy + ring_r * math.sin(ang)
        node_r = tile.width() * (0.042 if i % 2 == 0 else 0.03)
        halo = QRadialGradient(QPointF(x, y), node_r * 2.6)
        c = QColor(accents[i])
        halo.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 110))
        halo.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(x, y), node_r * 2.6, node_r * 2.6)
        p.setBrush(QColor(accents[i]) if i % 2 == 0 else QColor("#EAF0FF"))
        p.drawEllipse(QPointF(x, y), node_r, node_r)

    # ---- centre disc
    disc_r = tile.width() * 0.235
    disc = QRadialGradient(QPointF(cx - disc_r * 0.3, cy - disc_r * 0.4), disc_r * 1.6)
    disc.setColorAt(0.0, QColor("#1E2A58"))
    disc.setColorAt(1.0, QColor("#0A0E24"))
    p.setBrush(disc)
    p.setPen(QPen(QColor(91, 140, 255, 190), s * 0.009))
    p.drawEllipse(QPointF(cx, cy), disc_r, disc_r)

    # ---- the "W" monogram with gradient fill and glow
    font = QFont(family)
    font.setPixelSize(int(disc_r * 1.32))
    font.setWeight(QFont.Weight.ExtraBold)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 96)
    path = QPainterPath()
    text = "W"
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(font)
    tw = fm.horizontalAdvance(text)
    cap = fm.capHeight()
    path.addText(QPointF(cx - tw / 2, cy + cap / 2), font, text)
    # glow
    p.setPen(Qt.PenStyle.NoPen)
    for spread, a in ((s * 0.028, 26), (s * 0.014, 60)):
        stroker_pen = QPen(QColor(120, 160, 255, a), spread)
        stroker_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(stroker_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    fill = QLinearGradient(QPointF(cx, cy - cap / 2), QPointF(cx + tw * 0.4, cy + cap / 2))
    fill.setColorAt(0.0, QColor("#FFFFFF"))
    fill.setColorAt(0.55, QColor("#DDE8FF"))
    fill.setColorAt(1.0, QColor("#9DB8FF"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawPath(path)

    # ---- genius spark (four-point star) at the top-right of the monogram
    sx, sy = cx + tw * 0.56, cy - cap * 0.78
    spark_r = disc_r * 0.26
    spark = QPainterPath()
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        rr = spark_r if i % 2 == 0 else spark_r * 0.36
        pt = QPointF(sx + rr * math.cos(ang), sy + rr * math.sin(ang))
        if i == 0:
            spark.moveTo(pt)
        else:
            spark.lineTo(pt)
    spark.closeSubpath()
    sg = QRadialGradient(QPointF(sx, sy), spark_r * 2.2)
    sg.setColorAt(0.0, QColor(255, 194, 75, 150))
    sg.setColorAt(1.0, QColor(255, 194, 75, 0))
    p.setBrush(sg)
    p.drawEllipse(QPointF(sx, sy), spark_r * 2.2, spark_r * 2.2)
    sf = QLinearGradient(QPointF(sx, sy - spark_r), QPointF(sx, sy + spark_r))
    sf.setColorAt(0.0, QColor("#FFF3CC"))
    sf.setColorAt(1.0, QColor("#FFC24B"))
    p.setBrush(sf)
    p.drawPath(spark)
    p.end()

    return img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "app" / "assets"))
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    family = _font_family()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    master = render(args.size, family=family)
    png = out / "icon.png"
    master.save(str(png))

    try:
        from PIL import Image
    except ImportError:                                   # pragma: no cover - Pillow is a hard dependency
        print("Pillow missing: icon.ico not written")
        return 1
    frames = []
    for size in SIZES:
        # tiny sizes get a hair of extra padding so the tile does not touch the cell edge
        img = render(size, family=family, margin_ratio=0.02 if size <= 32 else 0.0)
        buf = img.constBits().tobytes() if hasattr(img.constBits(), "tobytes") else bytes(img.constBits())
        frame = Image.frombuffer("RGBA", (size, size), buf, "raw", "BGRA", img.bytesPerLine(), 1).copy()
        frames.append(frame)
    ico = out / "icon.ico"
    frames[-1].save(str(ico), format="ICO", sizes=[(f.width, f.height) for f in frames],
                    append_images=frames[:-1])
    print(f"wrote {png} ({args.size}px) and {ico} ({', '.join(str(s) for s in SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
