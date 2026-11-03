"""Colour palettes, fonts and the global stylesheet."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from PySide6.QtGui import QColor, QFont, QFontDatabase


def _mix(c1: str, c2: str, t: float) -> str:
    a, b = QColor(c1), QColor(c2)
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


def alpha(color: str, a: float) -> str:
    """Return an #AARRGGBB string — understood by both QSS and QColor()."""
    c = QColor(color)
    if not c.isValid():
        c = QColor("#5B8CFF")
    a = max(0.0, min(1.0, float(a)))
    return f"#{int(a * 255):02X}{c.red():02X}{c.green():02X}{c.blue():02X}"


def qcolor(color: str, a: float = 1.0) -> QColor:
    """QColor with float alpha (helper used by the custom painters)."""
    c = QColor(color)
    if not c.isValid():
        c = QColor("#5B8CFF")
    c.setAlphaF(max(0.0, min(1.0, float(a))))
    return c


@dataclass
class Palette:
    key: str
    name: str
    name_en: str
    bg0: str
    bg1: str
    bg2: str
    panel: str
    panel_alt: str
    text: str
    text_dim: str
    border: str
    dark: bool = True
    accent: str = "#5B8CFF"
    accent2: str = "#8B5CFF"
    danger: str = "#FF5A6E"
    success: str = "#2BD9A0"
    warning: str = "#FFC24B"

    def c(self, name: str) -> QColor:
        return QColor(getattr(self, name))


PALETTES: Dict[str, Palette] = {
    "aurora": Palette(
        key="aurora",
        name="شفق",
        name_en="Aurora",
        bg0="#111C28",
        bg1="#0B141E",
        bg2="#080F17",
        panel="#152431",
        panel_alt="#203341",
        text="#EAF0FF",
        text_dim="#A6BAC5",
        border="#344C59",
        dark=True,
    ),
    "midnight": Palette(
        key="midnight",
        name="نیمه‌شب",
        name_en="Midnight",
        bg0="#120B22",
        bg1="#0B0616",
        bg2="#05030B",
        panel="#1A1030",
        panel_alt="#241640",
        text="#F1E9FF",
        text_dim="#9C8FC4",
        border="#33245A",
        dark=True,
        accent2="#FF5CE1",
    ),
    "daylight": Palette(
        key="daylight",
        name="روشن",
        name_en="Daylight",
        bg0="#EEF2FB",
        bg1="#E2E8F6",
        bg2="#D8E0F2",
        panel="#FFFFFF",
        panel_alt="#F2F5FC",
        text="#131A2B",
        text_dim="#5C6A8A",
        border="#D3DBEC",
        dark=False,
    ),
}


def build_palette(theme_key: str, accent: str) -> Palette:
    base = PALETTES.get(theme_key, PALETTES["aurora"])
    pal = Palette(**{**base.__dict__})
    pal.accent = accent
    pal.accent2 = _mix(accent, "#FF4FD8", 0.45) if pal.dark else _mix(accent, "#7A2CFF", 0.35)
    return pal


# ---------------------------------------------------------------------- fonts
def ui_font_family() -> str:
    """Pick the best available UI font (Persian aware)."""
    available = set(QFontDatabase.families())
    for candidate in (
        "Vazirmatn", "Vazir", "IRANSansX", "IRANSans", "Noto Sans Arabic",
        "Segoe UI Variable Text", "Segoe UI", "Tahoma", "DejaVu Sans",
    ):
        if candidate in available:
            return candidate
    return QFont().family()


def make_font(size: float = 10, weight: int = 400, family: str | None = None, letter_spacing: float = 0) -> QFont:
    font = QFont(family or ui_font_family())
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight(weight))
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return font


# ----------------------------------------------------------------- stylesheet
def stylesheet(p: Palette) -> str:
    dim = p.text_dim
    return f"""
* {{
    font-family: "{ui_font_family()}";
    outline: 0;
}}
QWidget {{
    color: {p.text};
    font-size: 10.5pt;
}}
QWidget#Root {{
    background: transparent;
}}
QWidget#TitleBar {{
    background: transparent;
}}
QLabel#AppTitle {{
    font-size: 12pt;
    font-weight: 700;
    color: {p.text};
}}
QLabel#AppSubtitle {{
    font-size: 8.5pt;
    color: {dim};
}}
QLabel#Hint {{
    color: {dim};
    font-size: 9.5pt;
}}
QLabel#SectionTitle {{
    color: {dim};
    font-size: 8.5pt;
    font-weight: 700;
}}
QLabel#H1 {{
    font-size: 15pt;
    font-weight: 700;
}}
QLabel#FieldLabel {{
    color: {dim};
    font-size: 9pt;
}}
QLabel#FieldHelp {{
    color: {alpha(dim, 0.85)};
    font-size: 8pt;
}}

/* ------------------------------------------------------------- inputs */
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QListWidget {{
    background: {alpha(p.panel_alt, 0.92)};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: #FFFFFF;
    color: {p.text};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {alpha(p.accent, 0.55)};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QListWidget:focus {{
    border: 1px solid {p.accent};
    background: {alpha(p.panel_alt, 1.0)};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {alpha(dim, 0.6)};
    border-color: {alpha(p.border, 0.5)};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.panel};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {alpha(p.accent, 0.85)};
    color: {p.text};
    outline: 0;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {alpha(p.border, 0.6)};
    width: 16px;
    border-radius: 4px;
    margin: 1px 3px;
}}

/* ------------------------------------------------------------ buttons */
QPushButton {{
    background: {alpha(p.panel_alt, 0.95)};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 8px 16px;
    color: {p.text};
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {alpha(p.accent, 0.8)};
    background: {alpha(p.accent, 0.16)};
}}
QPushButton:pressed {{
    background: {alpha(p.accent, 0.28)};
}}
QPushButton:disabled {{
    color: {alpha(dim, 0.55)};
    border-color: {alpha(p.border, 0.5)};
    background: {alpha(p.panel_alt, 0.4)};
}}
QPushButton#Primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.accent}, stop:1 {p.accent2});
    border: none;
    color: #FFFFFF;
    padding: 10px 22px;
    border-radius: 12px;
    font-size: 10.5pt;
    font-weight: 700;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_mix(p.accent, "#FFFFFF", 0.14)}, stop:1 {_mix(p.accent2, "#FFFFFF", 0.14)});
}}
QPushButton#Primary:disabled {{
    background: {alpha(p.accent, 0.35)};
    color: {alpha("#FFFFFF", 0.7)};
}}
QPushButton#Danger {{
    background: {alpha(p.danger, 0.16)};
    border: 1px solid {alpha(p.danger, 0.6)};
    color: {p.danger};
}}
QPushButton#Danger:hover {{
    background: {alpha(p.danger, 0.3)};
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {alpha(p.border, 0.9)};
    color: {dim};
    padding: 7px 12px;
}}
QPushButton#Ghost:hover {{
    color: {p.text};
    border-color: {alpha(p.accent, 0.7)};
    background: {alpha(p.accent, 0.12)};
}}
QPushButton#IconButton {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 4px;
    font-size: 11pt;
    color: {dim};
}}
QPushButton#IconButton:hover {{
    background: {alpha(p.accent, 0.16)};
    color: {p.text};
}}
QPushButton#Pill {{
    background: {alpha(p.panel_alt, 0.8)};
    border: 1px solid {p.border};
    border-radius: 11px;
    padding: 4px 12px;
    font-size: 8.8pt;
    color: {dim};
    font-weight: 600;
}}
QPushButton#Pill:hover {{
    color: {p.text};
    border-color: {alpha(p.accent, 0.6)};
}}
QPushButton#Pill:checked {{
    color: {p.text};
    border-color: {p.accent};
    background: {alpha(p.accent, 0.22)};
}}

/* ----------------------------------------------------------- checkboxes */
QCheckBox {{
    spacing: 8px;
    color: {p.text};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid {p.border};
    background: {alpha(p.panel_alt, 0.9)};
}}
QCheckBox::indicator:hover {{
    border-color: {alpha(p.accent, 0.8)};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: none;
}}

/* -------------------------------------------------------------- progress */
QProgressBar {{
    background: {alpha(p.border, 0.45)};
    border: none;
    border-radius: 6px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p.accent}, stop:1 {p.accent2});
}}

/* ------------------------------------------------------------ scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {alpha(dim, 0.45)};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {alpha(p.accent, 0.75)};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {alpha(dim, 0.45)};
    border-radius: 5px;
    min-width: 32px;
}}

/* ---------------------------------------------------------------- misc */
QToolTip {{
    background: {p.panel};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 9pt;
}}
QFrame#Sheet {{
    background: {alpha(p.panel, 0.985)};
    border: 1px solid {alpha(p.border, 0.95)};
    border-radius: 18px;
}}
QFrame#Card {{
    background: {p.panel};
    border: 1px solid {p.border};
    border-radius: 16px;
}}
QFrame#Divider {{
    background: {alpha(p.border, 0.8)};
    max-height: 1px;
    border: none;
}}
QTextEdit#Log {{
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 8.5pt;
    color: {dim};
    background: {alpha(p.bg2, 0.75)};
}}
"""
