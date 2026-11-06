"""Colour palettes, fonts and the global stylesheet."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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

    @property
    def on_accent(self) -> str:
        """Readable text colour for accent-coloured fills (WCAG-checked)."""
        return accent_ink(self.accent, self.accent2)


PALETTES: Dict[str, Palette] = {
    "aurora": Palette(
        key="aurora",
        name="شفق",
        name_en="Aurora",
        bg0="#0C1226",
        bg1="#080B18",
        bg2="#05070F",
        panel="#141A2E",
        panel_alt="#1B2340",
        text="#EAF0FF",
        text_dim="#8D9AC0",
        border="#2A3557",
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
FONT_FAMILY = "Vazirmatn"
MONO_FAMILIES = ("Cascadia Code", "Cascadia Mono", "Consolas", "JetBrains Mono", "Fira Code",
                 "DejaVu Sans Mono", "Courier New")
_FONT_FILES = ("Vazirmatn-Regular.ttf", "Vazirmatn-Medium.ttf", "Vazirmatn-SemiBold.ttf",
               "Vazirmatn-Bold.ttf", "Vazirmatn-ExtraBold.ttf")
_FONTS_READY = False

# Typographic scale (pt). One source of truth for QSS and the custom painters.
TYPE_SCALE: Dict[str, float] = {
    "display": 18.0, "h1": 15.0, "h2": 12.5, "title": 11.5, "body": 10.5, "label": 9.5,
    "small": 9.0, "caption": 8.5, "micro": 7.6,
}


def register_fonts() -> str:
    """Load the bundled Vazirmatn family into Qt (idempotent) and return the UI family.

    Vazirmatn ships with the app so Persian text renders with the same metrics on
    every machine — no more Tahoma fallbacks, mismatched digits or clipped ascenders.
    """
    global _FONTS_READY
    if _FONTS_READY:
        return ui_font_family()
    _FONTS_READY = True
    try:
        from .paths import resource_path
        folder = resource_path("fonts")
        for name in _FONT_FILES:
            path = folder / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        QFont.insertSubstitutions(FONT_FAMILY, ["Segoe UI", "Tahoma", "Noto Sans Arabic", "DejaVu Sans"])
    except Exception:
        pass
    return ui_font_family()


def ui_font_family() -> str:
    """Pick the best available UI font (Persian aware; the bundled family wins)."""
    available = set(QFontDatabase.families())
    for candidate in (
        FONT_FAMILY, "Vazir", "IRANSansX", "IRANSans", "Noto Sans Arabic",
        "Segoe UI Variable Text", "Segoe UI", "Tahoma", "DejaVu Sans",
    ):
        if candidate in available:
            return candidate
    return QFont().family()


def mono_font_family() -> str:
    available = set(QFontDatabase.families())
    for candidate in MONO_FAMILIES:
        if candidate in available:
            return candidate
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def make_font(size: float = 10, weight: int = 400, family: str | None = None, letter_spacing: float = 0) -> QFont:
    font = QFont(family or ui_font_family())
    font.setPointSizeF(size)
    font.setWeight(QFont.Weight(weight))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting if size >= 12
                              else QFont.HintingPreference.PreferVerticalHinting)
    if letter_spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    return font


def scaled(token: str) -> float:
    """Size in pt for a TYPE_SCALE token."""
    return TYPE_SCALE.get(token, TYPE_SCALE["body"])


# ----------------------------------------------------------------- stylesheet
def _svg_uri(svg: str) -> str:
    """Write an SVG asset to the cache folder and return a QSS-friendly `url(...)` path.

    Qt style sheets cannot read ``data:`` URIs, so the few vector glyphs the sheet
    needs (check mark, chevron, radio dot) are materialised once per colour.
    """
    import hashlib
    import tempfile

    digest = hashlib.sha1(svg.encode("utf-8")).hexdigest()[:16]
    try:
        from .paths import cache_dir
        folder = cache_dir() / "qss"
    except Exception:
        folder = Path(tempfile.gettempdir()) / "wingenius-qss"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest}.svg"
        if not path.exists():
            path.write_text(svg.strip(), encoding="utf-8")
        return path.as_posix()
    except Exception:
        return ""


def _check_svg(color: str) -> str:
    return _svg_uri(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">'
        f'<path d="M4 9.4l3.1 3.1L14 5.9" fill="none" stroke="{color}" stroke-width="2.2" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def _dot_svg(color: str) -> str:
    return _svg_uri(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">'
        f'<circle cx="9" cy="9" r="3.4" fill="{color}"/></svg>'
    )


def _chevron_svg(color: str) -> str:
    return _svg_uri(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 16 16">'
        f'<path d="M4 6.2l4 4 4-4" fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def _relative_luminance(hex_color: str) -> float:
    c = QColor(hex_color)
    if not c.isValid():
        return 0.2

    def _chan(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * _chan(c.red()) + 0.7152 * _chan(c.green()) + 0.0722 * _chan(c.blue())


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.1 contrast ratio between two colours (1.0 … 21.0)."""
    a = _relative_luminance(foreground)
    b = _relative_luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def accent_ink(*fills: str) -> str:
    """Dark or light ink for text sitting on a coloured fill.

    Picks whichever of the two candidates has the best *worst-case* contrast
    across every fill (the primary button is a gradient, so the label has to stay
    readable at both stops).  The old rule — "dark ink only above 0.62
    luminance" — meant white text on all six shipped accents, which fails WCAG AA
    (2.78:1 … 4.20:1).
    """
    candidates = ("#0B1020", "#FFFFFF")
    usable = [fill for fill in fills if fill]
    if not usable:
        return "#0B1020"
    return max(candidates, key=lambda ink: min(contrast_ratio(ink, fill) for fill in usable))


def stylesheet(p: Palette, direction: str = "rtl") -> str:
    dim = p.text_dim
    family = ui_font_family()
    mono = mono_font_family()
    t = TYPE_SCALE
    on_accent = accent_ink(p.accent, p.accent2)
    is_rtl = str(direction or "rtl").lower() != "ltr"
    combo_pad_side = "padding-left" if is_rtl else "padding-right"
    combo_drop_pos = "center left" if is_rtl else "center right"
    menu_item_pad = "7px 28px 7px 14px" if is_rtl else "7px 14px 7px 28px"
    menu_icon_margin = "margin-right: 6px" if is_rtl else "margin-left: 6px"
    return f"""
* {{
    font-family: "{family}";
    outline: 0;
}}
QWidget {{
    color: {p.text};
    font-size: {t["body"]}pt;
}}
QWidget#Root {{
    background: transparent;
}}
QWidget#TitleBar {{
    background: transparent;
}}
QLabel#AppTitle {{
    font-size: {t["title"]}pt;
    font-weight: 700;
    color: {p.text};
    letter-spacing: 0.2px;
}}
QLabel#AppSubtitle {{
    font-size: {t["caption"]}pt;
    color: {dim};
}}
QLabel#Hint {{
    color: {dim};
    font-size: {t["label"]}pt;
}}
QLabel#SectionTitle {{
    color: {dim};
    font-size: {t["caption"]}pt;
    font-weight: 700;
    letter-spacing: 0.6px;
}}
QLabel#H1 {{
    font-size: {t["h1"]}pt;
    font-weight: 700;
}}
QLabel#H2 {{
    font-size: {t["h2"]}pt;
    font-weight: 600;
}}
QLabel#FieldLabel {{
    color: {dim};
    font-size: {t["label"]}pt;
    font-weight: 500;
}}
QLabel#FieldHelp {{
    color: {alpha(dim, 0.85)};
    font-size: {t["caption"]}pt;
}}
QLabel#Badge {{
    background: {alpha(p.accent, 0.18)};
    color: {p.accent};
    border-radius: 9px;
    padding: 1px 7px;
    font-size: {t["micro"]}pt;
    font-weight: 700;
}}

/* ------------------------------------------------------------- inputs */
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {alpha(p.panel_alt, 0.92)};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: {on_accent};
    color: {p.text};
    placeholder-text-color: {alpha(dim, 0.75)};
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
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {alpha(dim, 0.6)};
    border-color: {alpha(p.border, 0.5)};
    background: {alpha(p.panel_alt, 0.45)};
}}
QComboBox {{
    {combo_pad_side}: 28px;
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
    subcontrol-origin: padding;
    subcontrol-position: {combo_drop_pos};
}}
QComboBox::down-arrow {{
    image: url("{_chevron_svg(dim)}");
    width: 14px;
    height: 14px;
}}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: url("{_chevron_svg(p.accent)}");
}}
QComboBox QAbstractItemView {{
    background: {p.panel};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {alpha(p.accent, 0.85)};
    selection-color: {on_accent};
    color: {p.text};
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 2px 8px;
    border-radius: 6px;
}}
QSpinBox, QDoubleSpinBox {{
    padding-left: 20px;
    padding-right: 20px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: {alpha(p.border, 0.6)};
    width: 16px;
    border-radius: 4px;
    margin: 1px 3px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {alpha(p.accent, 0.4)};
}}
/* A styled button without a styled arrow draws an empty grey square: the two
   little triangles are CSS borders, so they work without shipping an image. */
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {alpha(p.text, 0.75)};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {alpha(p.text, 0.75)};
}}
QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled,
QDoubleSpinBox::up-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    border-bottom-color: {alpha(p.text_dim, 0.35)};
    border-top-color: {alpha(p.text_dim, 0.35)};
}}
QListWidget::item, QTreeWidget::item {{
    padding: 5px 8px;
    border-radius: 7px;
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {alpha(p.accent, 0.10)};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {alpha(p.accent, 0.26)};
    color: {p.text};
}}
QHeaderView::section {{
    background: {alpha(p.panel_alt, 0.9)};
    color: {dim};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 8px;
    font-size: {t["caption"]}pt;
    font-weight: 700;
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
QPushButton:focus {{
    border-color: {alpha(p.accent, 0.9)};
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
    color: {on_accent};
    padding: 10px 22px;
    border-radius: 12px;
    font-size: {t["body"]}pt;
    font-weight: 700;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_mix(p.accent, "#FFFFFF", 0.14)}, stop:1 {_mix(p.accent2, "#FFFFFF", 0.14)});
}}
QPushButton#Primary:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_mix(p.accent, "#000000", 0.12)}, stop:1 {_mix(p.accent2, "#000000", 0.12)});
}}
QPushButton#Primary:disabled {{
    background: {alpha(p.accent, 0.35)};
    color: {alpha(on_accent, 0.7)};
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
    font-size: {t["title"]}pt;
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
    font-size: {t["small"]}pt;
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
QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 5px;
    color: {dim};
}}
QToolButton:hover {{
    background: {alpha(p.accent, 0.14)};
    color: {p.text};
}}

/* ------------------------------------------------- checkboxes & radios */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {p.text};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid {p.border};
    background: {alpha(p.panel_alt, 0.9)};
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {alpha(p.accent, 0.8)};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{_check_svg(on_accent)}");
}}
QCheckBox::indicator:checked:disabled {{
    background: {alpha(p.accent, 0.45)};
    border-color: transparent;
}}
QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{_dot_svg(on_accent)}");
}}

/* ------------------------------------------------------------- sliders */
QSlider::groove:horizontal {{
    height: 6px;
    border-radius: 3px;
    background: {alpha(p.border, 0.7)};
}}
QSlider::sub-page:horizontal {{
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p.accent}, stop:1 {p.accent2});
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
    background: {on_accent if p.dark else p.panel};
    border: 2px solid {p.accent};
}}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {dim};
    padding: 7px 14px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {p.text};
}}
QTabBar::tab:selected {{
    color: {p.text};
    border-bottom: 2px solid {p.accent};
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
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {alpha(dim, 0.45)};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {alpha(p.accent, 0.75)};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
    width: 0;
}}

/* ---------------------------------------------------------------- menus */
QMenu {{
    background: {alpha(p.panel, 0.985)};
    border: 1px solid {p.border};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    padding: {menu_item_pad};
    border-radius: 8px;
    color: {p.text};
}}
QMenu::item:selected {{
    background: {alpha(p.accent, 0.2)};
}}
QMenu::item:disabled {{
    color: {alpha(dim, 0.55)};
}}
QMenu::separator {{
    height: 1px;
    background: {alpha(p.border, 0.9)};
    margin: 5px 10px;
}}
QMenu::icon {{
    {menu_icon_margin};
}}

/* ---------------------------------------------------------------- misc */
QToolTip {{
    background: {p.panel};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: {t["small"]}pt;
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
QFrame#Card:hover {{
    border-color: {alpha(p.accent, 0.45)};
}}
QFrame#Divider {{
    background: {alpha(p.border, 0.8)};
    max-height: 1px;
    border: none;
}}
QTextEdit#Log, QPlainTextEdit#Log, QPlainTextEdit#Code, QLineEdit#Code {{
    font-family: "{mono}";
    font-size: {t["caption"]}pt;
    color: {dim};
    background: {alpha(p.bg2, 0.75)};
}}
QMessageBox, QDialog {{
    background: {p.panel};
}}
QMessageBox QLabel {{
    color: {p.text};
}}
"""
