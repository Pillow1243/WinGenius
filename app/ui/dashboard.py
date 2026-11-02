"""Dashboard sheet: today's activity, most used tools, machine health, shortcuts."""
from __future__ import annotations

import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..core.history import history
from ..core.registry import get as get_tool
from ..core.theme import Palette
from ..core.utils import human_size
from .overlay import OverlaySheet
from .widgets import Card, divider, label

TILE_STYLES = """
QFrame#StatTile {{
    background: {bg};
    border: 1px solid {border};
    border-radius: 14px;
}}
"""


class BarChart(QWidget):
    """Minimal animated bar chart — no external plotting dependency."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: List[Tuple[str, str, int]] = []   # (label fa/en text, color, value)
        self.palette_ref: Optional[Palette] = None
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._progress = 1.0

    def set_data(self, items: List[Tuple[str, str, int]], palette: Palette) -> None:
        self.items = items
        self.palette_ref = palette
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.items or self.palette_ref is None:
            painter.setPen(QColor(self.palette_ref.text_dim if self.palette_ref else "#8899aa"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—")
            return
        palette = self.palette_ref
        width, height = self.width(), self.height()
        label_width = min(180, max(112, int(width * 0.30)))
        value_width = 30
        chart_width = max(40, width - label_width - value_width - 6)
        rows = len(self.items)
        row_height = min(30.0, (height - 6) / max(1, rows))
        biggest = max(value for _a, _b, value in self.items) or 1
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, min(9.5, row_height / 3.4)))
        painter.setFont(font)
        for index, (label_text, color, value) in enumerate(self.items):
            top = index * row_height + 3
            painter.setPen(QColor(palette.text_dim))
            painter.drawText(QRectF(0, top, label_width - 8, row_height),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             label_text if len(label_text) <= 17 else label_text[:16] + "…")
            track = QRectF(label_width, top + row_height * 0.3, chart_width, row_height * 0.38)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.border))
            painter.drawRoundedRect(track, 4, 4)
            bar = QRectF(label_width, top + row_height * 0.3,
                         max(8.0, chart_width * (value / biggest)), row_height * 0.38)
            start, end = QColor(palette.accent2), QColor(palette.accent)
            blend = 0.0 if rows < 2 else index / float(rows - 1)
            fill = QColor(int(start.red() + (end.red() - start.red()) * blend),
                          int(start.green() + (end.green() - start.green()) * blend),
                          int(start.blue() + (end.blue() - start.blue()) * blend))
            painter.setBrush(fill)
            painter.drawRoundedRect(bar, 4, 4)
            painter.setPen(QColor(palette.text))
            painter.drawText(QRectF(width - value_width, top, value_width, row_height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(value))


class MeterRow(QWidget):
    """A labelled horizontal meter (CPU / RAM / disk)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.entries: List[Tuple[str, float, str]] = []
        self.palette_ref: Optional[Palette] = None

    def set_data(self, entries: List[Tuple[str, float, str]], palette: Palette) -> None:
        self.entries = entries
        self.palette_ref = palette
        # one 24px lane per metric; a fixed 34px widget used to smear three rows together
        self.setMinimumHeight(max(34, 24 * max(1, len(entries))))
        self.updateGeometry()
        self.update()

    def paintEvent(self, _event) -> None:
        if self.palette_ref is None:
            return
        palette = self.palette_ref
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # keep unit strings such as "19.4 GB" in logical order inside the RTL shell
        painter.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        painter.setFont(font)
        rows = max(1, len(self.entries))
        row_height = self.height() / rows
        name_width = 54
        value_width = 92
        track_left = name_width + 8
        track_width = max(30, self.width() - track_left - value_width - 10)
        for index, (name, value, suffix) in enumerate(self.entries):
            top = index * row_height
            painter.setPen(QColor(palette.text_dim))
            painter.drawText(QRectF(0, top, name_width, row_height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name[:9])
            track = QRectF(track_left, top + row_height / 2 - 3, track_width, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.border))
            painter.drawRoundedRect(track, 3, 3)
            ratio = max(0.0, min(1.0, value / 100.0))
            colour = QColor(palette.accent if ratio < 0.7 else
                            (palette.warning if ratio < 0.88 else palette.danger))
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(track_left, top + row_height / 2 - 3,
                                           max(4.0, track_width * ratio), 6), 3, 3)
            painter.setPen(QColor(palette.text))
            painter.drawText(QRectF(track_left + track_width + 8, top, value_width - 8, row_height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"{value:.0f}% · {suffix}"[:18])


class Dashboard(OverlaySheet):
    """The assistant's home: what happened, what is heavy, what to do next."""

    actionRequested = Signal(str)
    undoRequested = Signal()
    card_width = 660
    body_max_height = 620

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.retranslate()
        self._tiles: List[QWidget] = []
        self._chart: Optional[BarChart] = None
        self._meters: Optional[MeterRow] = None
        self._footer_left = 0
        self._shortcut_buttons: List[QPushButton] = []
        self._build_footer()

    # ------------------------------------------------------------------ build
    def _build_footer(self) -> None:
        self._refresh_button = self.add_footer_button("تازه‌سازی", self._refresh, primary=False)
        self._footer.addStretch(1)
        self._close_footer = self.add_footer_button("بستن", self.close_panel, primary=True)

    def _stat_tile(self, title: str, value: str, hint: str, color: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("StatTile")
        tile.setStyleSheet(TILE_STYLES.format(bg=self.palette_ref.panel_alt,
                                              border=self.palette_ref.border))
        tile.setMinimumHeight(74)
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        value_label = label(value, "H1")
        value_label.setStyleSheet(f"color:{color};font-size:22px;font-weight:700;")
        layout.addWidget(value_label)
        title_label = label(title, "RowTitle")
        title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(title_label)
        if hint:
            hint_label = label(hint, "Hint")
            hint_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(hint_label)
        return tile

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        self.clear_body()
        self._shortcut_buttons = []
        palette = self.palette_ref
        entries = history.entries(limit=250)
        now = time.time()
        today = [entry for entry in entries if now - entry.when < 86400]
        done = [entry for entry in today if entry.status == "done"]
        failed = [entry for entry in today if entry.status == "failed"]
        success_rate = (len(done) * 100 // max(1, len(done) + len(failed)))

        # ------------------------------------------------------------- tiles
        grid = QGridLayout()
        grid.setSpacing(10)
        tiles = [
            ("اجراهای ۲۴ ساعت" if self.lang != "en" else "Runs (24h)", str(len(today)),
             ("ابزارهای انجام‌شده" if self.lang != "en" else "completed tools"), palette.accent),
            ("نرخ موفقیت" if self.lang != "en" else "Success rate", f"{success_rate}%",
             (f"{len(failed)} خطا" if self.lang != "en" else f"{len(failed)} failed"),
             palette.success if success_rate >= 80 else palette.warning),
            ("ابزارهای استفاده‌شده" if self.lang != "en" else "Tools used",
             str(len({entry.tool_id for entry in entries})),
             ("در تاریخچه" if self.lang != "en" else "in history"), "#A78BFA"),
            ("میانگین زمان اجرا" if self.lang != "en" else "Avg run",
             f"{(sum(entry.duration for entry in done) / max(1, len(done))):.1f}s",
             ("برای اجراهای موفق" if self.lang != "en" else "for successful runs"), "#4ECDC4"),
        ]
        for index, (title, value, hint, color) in enumerate(tiles):
            grid.addWidget(self._stat_tile(title, value, hint, color), index // 2, index % 2)
        holder = QWidget()
        holder.setLayout(grid)
        self.body_layout.addWidget(holder)

        # ------------------------------------------------------------- chart
        self.body_layout.addWidget(label("پرمصرف‌ترین ابزارها" if self.lang != "en" else "Most used tools",
                                         "SectionTitle"))
        counts = Counter(entry.tool_id for entry in entries)
        items: List[Tuple[str, str, int]] = []
        for tool_id, count in counts.most_common(6):
            spec = get_tool(tool_id)
            name = spec.name_for(self.lang) if spec else tool_id
            colour = palette.accent
            if spec is not None:
                from ..core.registry import category as get_category

                colour = get_category(spec.category).color
            items.append((name, colour, count))
        if items:
            chart = BarChart()
            chart.set_data(items, palette)
            self._chart = chart
            self.body_layout.addWidget(chart)
        else:
            self.body_layout.addWidget(label(
                "هنوز کاری اجرا نشده است؛ از دایره‌ی خانه یا دستیار شروع کنید."
                if self.lang != "en" else
                "Nothing has run yet — start from the radial menu or the assistant.", "Hint", wrap=True))

        # ------------------------------------------------------------ meters
        self.body_layout.addWidget(divider())
        self.body_layout.addWidget(label("وضعیت لحظه‌ای سیستم" if self.lang != "en" else "Live system health",
                                         "SectionTitle"))
        meters = MeterRow()
        meters.set_data(self._system_entries(), palette)
        self._meters = meters
        self.body_layout.addWidget(meters)

        # ------------------------------------------------------------- undo log
        self.body_layout.addWidget(divider())
        self.body_layout.addWidget(label("آخرین عملیات (Ctrl+Z)" if self.lang != "en"
                                         else "Recent operations (Ctrl+Z)", "SectionTitle"))
        self._undo_rows = []
        try:
            from ..core.journal import journal

            operations = journal().entries(limit=4)
        except Exception:
            operations = []
        if not operations:
            self.body_layout.addWidget(label(
                "هنوز عملیاتی ثبت نشده است؛ تغییر نام، دسته‌بندی و حذف امن همه در ژورنال ثبت می‌شوند."
                if self.lang != "en" else
                "No operations yet — renames, organising and safe deletes all land in the journal.",
                "Hint", wrap=True))
        else:
            for operation in operations:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(8)
                stamp = time.strftime("%H:%M", time.localtime(operation.ts))
                title = label(f"{operation.title(self.lang)} · {stamp}", "RowTitle")
                title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row_layout.addWidget(title, 1)
                state = label("✓" if operation.undoable else "·", "Hint")
                state.setStyleSheet("color:%s;" % (palette.accent if operation.undoable else palette.text_dim))
                row_layout.addWidget(state)
                self.body_layout.addWidget(row)
                self._undo_rows.append(row)
            undo_button = QPushButton("بازگردانی آخرین عملیات" if self.lang != "en" else "Undo last operation")
            undo_button.setObjectName("Ghost")
            undo_button.setMinimumHeight(36)
            undo_button.clicked.connect(lambda: self.undoRequested.emit())
            self.body_layout.addWidget(undo_button)

        # --------------------------------------------------------- shortcuts
        self.body_layout.addWidget(divider())
        self.body_layout.addWidget(label("میان‌برهای هوشمند" if self.lang != "en" else "Smart shortcuts",
                                         "SectionTitle"))
        shortcuts = [
            ("plan", "دستیار برنامه‌ریز" if self.lang != "en" else "AI planner",
             "یک جمله بنویسید، یک زنجیره‌ی ابزار بگیرید"),
            ("workflows", "گردش‌کارها" if self.lang != "en" else "Workflows",
             "چند ابزار را پشت سر هم اجرا کنید"),
            ("automation", "خودکارسازی پوشه" if self.lang != "en" else "Folder automation",
             "فایل تازه آمد؟ خودش اجرا شود"),
            ("jobs", "صف کارها" if self.lang != "en" else "Job queue",
             "اجراهای هم‌زمان و پس‌زمینه"),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        for position, (key, title, hint) in enumerate(shortcuts):
            button = QPushButton(title)
            button.setObjectName("Ghost")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(hint)
            button.setMinimumHeight(38)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, name=key: self.actionRequested.emit(name))
            grid.addWidget(button, position // 2, position % 2)
            self._shortcut_buttons.append(button)
        grid_holder = QWidget()
        grid_holder.setLayout(grid)
        self.body_layout.addWidget(grid_holder)
        self.body_layout.addStretch(1)
        self.fit_body_height()

    # -------------------------------------------------------------- system data
    def _system_entries(self) -> List[Tuple[str, float, str]]:
        entries: List[Tuple[str, float, str]] = []
        try:
            import psutil

            entries.append(("CPU", float(psutil.cpu_percent(interval=0.05)), f"{psutil.cpu_count()} core"))
            memory = psutil.virtual_memory()
            entries.append(("RAM", float(memory.percent), human_size(memory.available)))
            try:
                disk = psutil.disk_usage(str(psutil.disk_partitions()[0].mountpoint))
                entries.append(("Disk", float(disk.percent), human_size(disk.free)))
            except Exception:
                pass
            battery = getattr(psutil, "sensors_battery", lambda: None)()
            if battery is not None:
                entries.append(("Battery", float(battery.percent), "plugged" if battery.power_plugged else "on battery"))
        except Exception:
            pass
        if not entries:
            entries = [("System", 0.0, "psutil missing")]
        return entries

    # ----------------------------------------------------------------- events
    def retranslate(self) -> None:
        self._title.setText("داشبورد" if self.lang != "en" else "Dashboard")
        self._subtitle.setText(
            "خلاصه‌ی کارها، وضعیت سیستم و میان‌برهای پیشنهادی." if self.lang != "en"
            else "Your recent work, machine health and one-click shortcuts.")
        for button, text_fa, text_en in (
                (getattr(self, "_refresh_button", None), "تازه‌سازی", "Refresh"),
                (getattr(self, "_close_footer", None), "بستن", "Close")):
            if button is not None:
                button.setText(text_fa if self.lang != "en" else text_en)

    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette
        self.setStyleSheet("")
        self._refresh()

    def keyPressEvent(self, event) -> None:
        super().keyPressEvent(event)
