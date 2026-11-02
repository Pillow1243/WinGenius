"""Assistant / command palette: natural-language search over every tool."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

from ..core import nlsearch, theme
from ..core.i18n import t
from ..core.registry import category, get
from ..core.settings import settings
from ..core.theme import Palette
from .widgets import label, pixmap_icon


class QuickSearch(QWidget):
    """Floating command palette with live natural-language matching."""

    toolChosen = Signal(str)
    commandChosen = Signal(str)
    closed = Signal()
    recentChosen = Signal(str, dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette_ref: Palette = theme.build_palette("aurora", "#5B8CFF")
        self.lang = "fa"
        self._results = []
        self.setVisible(False)

        self._card = QFrame(self)
        self._card.setObjectName("Card")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = label(t("as.title"), "SectionTitle")
        header.addWidget(self._title)
        header.addStretch(1)
        self._hint = label(t("as.esc"), "FieldHelp")
        header.addWidget(self._hint)
        card_layout.addLayout(header)

        self._input = QLineEdit()
        self._input.setPlaceholderText(t("as.placeholder"))
        self._input.setMinimumHeight(42)
        self._input.textChanged.connect(self._on_text)
        self._input.returnPressed.connect(self.activate_current)
        card_layout.addWidget(self._input)

        self._suggestion = label("", "Hint", wrap=True)
        card_layout.addWidget(self._suggestion)

        self._list = QListWidget()
        self._list.setObjectName("Results")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setMaximumHeight(330)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.itemClicked.connect(self._on_item_activated)
        card_layout.addWidget(self._list)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(90)
        self._debounce.timeout.connect(self._refresh)

    # ------------------------------------------------------------------ config
    def apply_palette(self, palette: Palette) -> None:
        self.palette_ref = palette

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self._title.setText(t("as.title"))
        self._hint.setText(t("as.esc"))
        self._input.setPlaceholderText(t("as.placeholder"))
        self._refresh()

    # ------------------------------------------------------------------- show
    def open_palette(self, prefill: str = "") -> None:
        self.setVisible(True)
        self.raise_()
        self._input.blockSignals(True)
        self._input.setText(prefill)
        self._input.blockSignals(False)
        self._refresh()
        self._input.setFocus()
        self._input.selectAll()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def close_palette(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    # ----------------------------------------------------------------- search
    def _on_text(self, _text: str) -> None:
        self._debounce.start()

    # -------------------------------------------------------------- commands
    def _commands(self):
        """(key, icon, title) triples available from the palette."""
        return [
            ("dashboard", "chart", t("cmd.dashboard"), "Ctrl+1"),
            ("plan", "sparkles", t("cmd.plan"), "Ctrl+2"),
            ("workflows", "layers", t("cmd.workflows"), "Ctrl+3"),
            ("automation", "refresh", t("cmd.automation"), "Ctrl+4"),
            ("scheduler", "clock", t("cmd.scheduler"), "Ctrl+5"),
            ("undo", "undo", t("cmd.undo"), "Ctrl+Z"),
            ("vault", "shield", t("cmd.vault"), ""),
            ("api", "terminal", t("cmd.api"), ""),
            ("jobs", "list", t("cmd.jobs"), "Ctrl+J"),
            ("doctor", "heart", t("cmd.doctor"), "Ctrl+D"),
            ("plugins", "puzzle", t("cmd.plugins"), ""),
            ("settings", "sliders", t("tb.settings"), "Ctrl+,"),
            ("cli", "terminal", t("cmd.cli"), ""),
        ]

    def _add_commands(self, query: str) -> int:
        added = 0
        commands = self._commands()
        matches = [item for item in commands if not query or query.lower() in item[2].lower()
                   or query.lower() in item[0]]
        if query:
            # keep the list focused while typing, but never hide commands completely
            matches = matches[:3]
        if not matches:
            return 0
        header = QListWidgetItem(t("cmd.section"))
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        header.setSizeHint(QSize(0, 26))
        self._list.addItem(header)
        for key, icon, title, hint in matches:
            text = f"{title}      ·  {hint}" if hint else title
            item = QListWidgetItem(text)
            item.setIcon(pixmap_icon(icon, QColor(settings.accent), 24))
            item.setData(Qt.ItemDataRole.UserRole, f"cmd:{key}")
            item.setSizeHint(QSize(0, 36))
            self._list.addItem(item)
            added += 1
        return added

    def _add_recent(self, query: str) -> int:
        from ..core.history import history

        entries = history.entries(limit=6)
        if not entries:
            return 0
        shown = 0
        header_done = False
        for entry in entries:
            spec = get(entry.tool_id)
            if spec is None:
                continue
            title = spec.name_for(self.lang)
            if query and query.lower() not in title.lower() and query.lower() not in spec.id:
                continue
            if not header_done:
                header = QListWidgetItem(t("cmd.recent"))
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setSizeHint(QSize(0, 26))
                self._list.addItem(header)
                header_done = True
            item = QListWidgetItem(f"↻ {title}      ·  {entry.message[:40]}")
            item.setData(Qt.ItemDataRole.UserRole, f"recent:{spec.id}")
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.values or {})
            item.setIcon(pixmap_icon(spec.icon, QColor(settings.accent), 24))
            item.setSizeHint(QSize(0, 36))
            self._list.addItem(item)
            shown += 1
            if shown >= 4:
                break
        return shown

    def _refresh(self) -> None:
        query = self._input.text().strip()
        self._list.clear()
        self._results = nlsearch.search(query, limit=14) if query else [
            (0.0, get(tool_id)) for tool_id in settings.favorites if get(tool_id)
        ]
        if not query:
            # keep room for the command + recent sections below the tools
            self._results = self._results[:5]
            if not self._results:
                for tool in nlsearch.all_tools()[:5]:
                    self._results.append((0.0, tool))
        if not self._results:
            item = QListWidgetItem(t("as.no_result"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            self._suggestion.setText("")
            self._add_recent(query)
            self._add_commands(query)
            return
        best = self._results[0][1]
        self._suggestion.setText(f"{t('as.best')}:  {best.name_for(self.lang)} — {best.desc_for(self.lang)}")
        for score, spec in self._results:
            cat = category(spec.category)
            item = QListWidgetItem(f"{spec.name_for(self.lang)}      ·  {cat.name_for(self.lang)}")
            item.setIcon(pixmap_icon(spec.icon, QColor(cat.color), 28))
            item.setData(Qt.ItemDataRole.UserRole, spec.id)
            item.setToolTip(spec.desc_for(self.lang))
            item.setSizeHint(QSize(0, 40))
            self._list.addItem(item)
        self._add_recent(query)
        self._add_commands(query)
        self._list.setCurrentRow(0)

    def activate_current(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._on_item_activated(item)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        payload = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not payload:
            return
        values = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        settings.push_assistant_history(self._input.text().strip())
        if payload.startswith("cmd:"):
            self.close_palette()
            self.commandChosen.emit(payload.split(":", 1)[1])
            return
        self.close_palette()
        if payload.startswith("recent:"):
            self.recentChosen.emit(payload.split(":", 1)[1], dict(values))
            return
        self.toolChosen.emit(payload)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_palette()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self._list.currentRow()
            count = self._list.count()
            if count:
                row = (row + (1 if key == Qt.Key.Key_Down else -1)) % count
                self._list.setCurrentRow(row)
            return
        super().keyPressEvent(event)
