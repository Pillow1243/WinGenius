"""The frameless WinGenius shell: title bar, radial menu and overlays."""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPixmap,
    QRadialGradient, QRegion, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import i18n, nlsearch, registry, suggest, theme
from ..core.i18n import set_language, t
from ..core.paths import APP_NAME, APP_TAGLINE, APP_TAGLINE_EN, APP_VERSION
from ..core.registry import CATEGORIES, Category, category as get_category, get as get_tool
from ..core.settings import settings
from ..core.history import history
from ..core.jobs import JobManager
from ..core.theme import Palette
from ..core.clipboard import ClipboardWatcher
from ..core.watcher import Watcher, mark_run
from ..core.scheduler import Scheduler, store as schedule_store
from ..core.workflows import WorkflowRunner, store as workflow_store
from ..core import macro as macro_engine
from ..core.journal import journal
from .automation_panel import AutomationPanel
from .scheduler_panel import SchedulerPanel
from .dashboard import Dashboard
from .plan_panel import PlanPanel
from .tray import Tray, tray_available
from .workflow_panel import WorkflowPanel
from .drop_zone import DropZone
from .jobs_drawer import JobsDrawer
from .quick_search import QuickSearch
from .radial import Node, RadialMenu
from .settings_panel import SettingsPanel
from .tool_panel import ToolPanel
from .widgets import Toast, _make_icon, icon_button, label, pixmap_icon

TOOLS_PER_PAGE = 10


class DimOverlay(QWidget):
    """Semi-transparent layer that closes the active overlay when clicked."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.apply_palette(theme.build_palette("aurora", "#5B8CFF"))

    def apply_palette(self, palette: Palette) -> None:
        color = "rgba(4, 8, 18, 0.74)" if palette.dark else "rgba(20, 30, 55, 0.34)"
        self.setStyleSheet(f"background: {color}; border-radius: 24px;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class TitleBar(QWidget):
    """Draggable title bar."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._drag_offset: Optional[QPoint] = None
        self.setFixedHeight(74)
        self.setObjectName("TitleBar")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        window = self._window
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        settings.set("app/window_pos", f"{self._window.x()},{self._window.y()}")
        event.accept()


class MainWindow(QWidget):
    """Root window — owns navigation state and every overlay."""

    def __init__(self) -> None:
        super().__init__()
        set_language(settings.lang)
        self.lang = settings.lang
        self.palette_ref: Palette = theme.build_palette(settings.theme, settings.accent)
        self._category_key = "home"
        self._pages: List[List[Node]] = []
        self._overlay: Optional[str] = None

        self.jobs = JobManager(max_parallel=int(settings.get("ui/parallel") or 3))
        self.jobs.configure(self.lang, settings.output_dir)

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(QSize(680, 600))
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._resizing_edge = None
        self._resize_origin = None
        self._resize_start = None
        self._maximized = False
        self.resize(self._preferred_size())
        icon_path = Path(__file__).resolve().parents[1] / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ------------------------------------------------------------ layout
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 10, 18, 12)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)
        self._build_titlebar()

        from .update_dialog import UpdateBanner

        self.update_banner = UpdateBanner(self.lang, self)
        self.update_banner.openRequested.connect(self.open_update_dialog)
        root.addWidget(self.update_banner)
        self._last_update_check: Optional[dict] = None
        self._bg_update_worker = None

        self.radial = RadialMenu(self)
        self.radial.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.radial, 1)

        # ---------------------------------------------------------- overlays
        self._dim = DimOverlay(self)
        self._dim.clicked.connect(self._close_overlay)
        self._dim.hide()

        self.tool_panel = ToolPanel(self)
        self.tool_panel.hide()
        self.settings_panel = SettingsPanel(self)
        self.settings_panel.hide()
        self.quick_search = QuickSearch(self)
        self.quick_search.hide()
        self.jobs_drawer = JobsDrawer(self)
        self.jobs_drawer.hide()
        self.drop_zone = DropZone(self)
        self.drop_zone.hide()
        self.dashboard = Dashboard(self)
        self.dashboard.hide()
        self.plan_panel = PlanPanel(self)
        self.plan_panel.hide()
        self.workflow_panel = WorkflowPanel(self)
        self.workflow_panel.hide()
        self.automation_panel = AutomationPanel(self)
        self.automation_panel.hide()
        self.scheduler_panel = SchedulerPanel(self)
        self.scheduler_panel.hide()
        self.toast = Toast(self)

        # ------------------------------------------------------------ services
        self._last_paths: List[str] = []
        self._clip_hint: Optional[tuple] = None
        self.watcher = Watcher(int(settings.get("ui/watch_interval") or 4000), self)
        self.automation_panel.attach(self.watcher)
        self.scheduler = Scheduler(interval_ms=15_000, parent=self)
        self.clipboard_watcher = ClipboardWatcher(self)
        self.clipboard_watcher.attach(QApplication.clipboard())
        self._nav_buttons: List[QPushButton] = []
        self.tray: Optional[Tray] = None

        # ---------------------------------------------------------- signals
        self.radial.nodeClicked.connect(self._on_node_clicked)
        self.radial.centerClicked.connect(self._on_center_clicked)
        self.radial.pageChanged.connect(self._on_page_changed)
        self.tool_panel.closed.connect(self._close_overlay)
        self.tool_panel.toastRequested.connect(self._show_toast)
        self.tool_panel.favoriteToggled.connect(lambda *_: self._rebuild_page())
        self.settings_panel.closed.connect(self._close_overlay)
        self.settings_panel.languageChanged.connect(self._on_language_changed)
        self.settings_panel.themeChanged.connect(self._apply_theme)
        self.settings_panel.openJobsRequested.connect(self.open_jobs)
        self.settings_panel.jobsChangedRequested.connect(self._on_parallel_changed)
        self.settings_panel.pluginsChanged.connect(self._on_plugins_changed)
        self.settings_panel.checkUpdatesRequested.connect(lambda: self.open_update_dialog())
        self.quick_search.toolChosen.connect(self._open_tool_by_id)
        self.quick_search.commandChosen.connect(self._on_command)
        self.quick_search.recentChosen.connect(self._open_tool_by_id)
        self.quick_search.closed.connect(self._on_search_closed)
        self.tool_panel.attach_jobs(self.jobs)
        self.tool_panel.queueRequested.connect(self._on_job_queued)
        self.jobs_drawer.attach(self.jobs, self.lang)
        self.jobs_drawer.closed.connect(self._close_overlay)
        self.jobs_drawer.openToolRequested.connect(self._rerun_job)
        self.drop_zone.toolChosen.connect(self._on_drop_choice)
        self.drop_zone.browseRequested.connect(self._browse_for_drop)
        self.drop_zone.closed.connect(self._close_overlay)
        self.jobs.changed.connect(self._update_jobs_badge)
        self.jobs.jobFinished.connect(lambda _job: self._update_jobs_badge())
        # ---- v3 sheets
        self.dashboard.closed.connect(self._close_overlay)
        self.dashboard.actionRequested.connect(self._on_dashboard_action)
        self.plan_panel.closed.connect(self._close_overlay)
        self.plan_panel.toastRequested.connect(self._show_toast)
        self.plan_panel.runFinished.connect(self._on_workflow_done)
        self.workflow_panel.closed.connect(self._close_overlay)
        self.workflow_panel.toastRequested.connect(self._show_toast)
        self.workflow_panel.runFinished.connect(self._on_workflow_done)
        self.automation_panel.closed.connect(self._close_overlay)
        self.automation_panel.toastRequested.connect(self._show_toast)
        self.automation_panel.ruleTriggered.connect(self._run_watch_rule)
        self.watcher.triggered.connect(self._on_watch_triggered)
        self.clipboard_watcher.suggested.connect(self._on_clipboard_suggestion)
        # ---- v4: scheduler + undo
        self.scheduler_panel.closed.connect(self._close_overlay)
        self.scheduler_panel.toastRequested.connect(self._show_toast)
        self.scheduler_panel.scheduleTriggered.connect(self._on_schedule_triggered)
        self.scheduler_panel.macroRunRequested.connect(self._on_macro_run)
        self.scheduler.triggered.connect(lambda item: self._on_schedule_triggered(item, "auto"))
        self.plan_panel.undoRequested.connect(self.undo_last) if hasattr(self.plan_panel, "undoRequested") else None
        self.dashboard.undoRequested.connect(self.undo_last) if hasattr(self.dashboard, "undoRequested") else None
        self._build_nav_rail()
        self._start_services()

        self._setup_shortcuts()
        self._apply_theme(rebuild=False)
        self._restore_position()
        self.show_home(animate=False)
        self.radial.setFocus()

    # ---------------------------------------------------------------- window
    def _preferred_size(self) -> QSize:
        """Default size, clamped so the window always fits the screen."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QSize(960, 900)
        available = screen.availableGeometry()
        width = max(680, min(980, available.width() - 90))
        height = max(600, min(920, available.height() - 90))
        return QSize(width, height)

    def _edge_at(self, position) -> str:
        """Return the resize handle under the given local position ('' if none)."""
        if self.isMaximized():
            return ""
        band = 12
        width, height = self.width(), self.height()
        x, y = position.x(), position.y()
        left, right = x <= band, x >= width - band
        top, bottom = y <= band, y >= height - band
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return ""

    _CURSORS = {
        "left": Qt.CursorShape.SizeHorCursor, "right": Qt.CursorShape.SizeHorCursor,
        "top": Qt.CursorShape.SizeVerCursor, "bottom": Qt.CursorShape.SizeVerCursor,
        "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    }

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position())
            if edge:
                self._resizing_edge = edge
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_start = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing_edge and self._resize_start is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            rect = QRect(self._resize_start)
            minimum = self.minimumSize()
            if "left" in self._resizing_edge or self._resizing_edge in ("tl", "bl"):
                new_left = min(rect.left() + delta.x(), rect.right() - minimum.width())
                rect.setLeft(new_left)
            if "right" in self._resizing_edge or self._resizing_edge in ("tr", "br"):
                rect.setRight(max(rect.left() + minimum.width(), rect.right() + delta.x()))
            if "top" in self._resizing_edge or self._resizing_edge in ("tl", "tr"):
                new_top = min(rect.top() + delta.y(), rect.bottom() - minimum.height())
                rect.setTop(new_top)
            if "bottom" in self._resizing_edge or self._resizing_edge in ("bl", "br"):
                rect.setBottom(max(rect.top() + minimum.height(), rect.bottom() + delta.y()))
            self.setGeometry(rect)
            event.accept()
            return
        edge = self._edge_at(event.position())
        self.setCursor(self._CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing_edge:
            self._resizing_edge = None
            self._resize_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            settings.set("app/window_pos", f"{self.x()},{self.y()}")
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._maximized = self.isMaximized()
            self._sync_max_button()
            self.update()

    # ------------------------------------------------------------- window state
    def toggle_maximized(self) -> None:
        """Maximize / restore, keeping the custom shadow-margin in sync."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_max_button()

    def _sync_max_button(self) -> None:
        button = getattr(self, "_max_button", None)
        if button is None:
            return
        maximized = self.isMaximized()
        key = "tb.restore" if maximized else "tb.maximize"
        color = QColor(self.palette_ref.text_dim if hasattr(self, "palette_ref") else "#C9D4E6")
        button.setIcon(_make_icon("restore" if maximized else "maximize", color, 34))
        button.setToolTip(t(key))
        button.setAccessibleName(t(key))

    # ---------------------------------------------------------------- helpers
    def _build_titlebar(self) -> None:
        self.title_bar.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout = QHBoxLayout(self.title_bar)
        layout.setContentsMargins(6, 8, 2, 8)
        layout.setSpacing(10)

        self._back_button = icon_button("back", t("center.back"), 34)
        self._back_button.clicked.connect(lambda: self.show_home())
        self._back_button.hide()
        layout.addWidget(self._back_button)

        self._logo = QLabel()
        self._logo.setFixedSize(40, 40)
        layout.addWidget(self._logo)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self._title_label = label(APP_NAME, "AppTitle")
        self._tagline_label = label(APP_TAGLINE, "AppSubtitle")
        text_box.addWidget(self._title_label)
        text_box.addWidget(self._tagline_label)
        layout.addLayout(text_box)

        layout.addStretch(1)

        self._search_button = QPushButton()
        self._search_button.setObjectName("Pill")
        self._search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_button.setMinimumHeight(32)
        self._search_button.clicked.connect(self.open_search)
        layout.addWidget(self._search_button)

        self._lang_button = QPushButton()
        self._lang_button.setObjectName("Pill")
        self._lang_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lang_button.setToolTip("Language")
        self._lang_button.clicked.connect(self._toggle_language)
        layout.addWidget(self._lang_button)

        self._jobs_button = QPushButton()
        self._jobs_button.setObjectName("Pill")
        self._jobs_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._jobs_button.setMinimumHeight(32)
        self._jobs_button.setToolTip(t("cmd.jobs"))
        self._jobs_button.clicked.connect(self.open_jobs)
        layout.addWidget(self._jobs_button)

        self._settings_button = icon_button("sliders", t("tb.settings"), 34)
        self._settings_button.clicked.connect(self.open_settings)
        layout.addWidget(self._settings_button)

        self._minimize_button = icon_button("minus", t("tb.minimize"), 34)
        self._minimize_button.clicked.connect(self.showMinimized)
        layout.addWidget(self._minimize_button)

        # A frameless window has no OS chrome, so without this button the only way
        # to fill the screen was a double click on the title bar.
        self._max_button = icon_button("maximize", t("tb.maximize"), 34)
        self._max_button.setObjectName("MaxButton")
        self._max_button.clicked.connect(self.toggle_maximized)
        layout.addWidget(self._max_button)

        self._close_button = icon_button("x", t("tb.close"), 34)
        self._close_button.clicked.connect(self.close)
        layout.addWidget(self._close_button)

    def _setup_shortcuts(self) -> None:
        for sequence in ("Ctrl+K", "Ctrl+F", "Ctrl+Space"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.open_search)
        max_shortcut = QShortcut(QKeySequence("F11"), self)
        max_shortcut.activated.connect(self.toggle_maximized)
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self.open_settings)
        home_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        home_shortcut.activated.connect(lambda: self.show_home())
        jobs_shortcut = QShortcut(QKeySequence("Ctrl+J"), self)
        jobs_shortcut.activated.connect(self.open_jobs)
        doctor_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        doctor_shortcut.activated.connect(self.run_doctor)
        for sequence, slot in (("Ctrl+1", self.open_dashboard), ("Ctrl+2", self.open_plan),
                               ("Ctrl+3", self.open_workflows), ("Ctrl+4", self.open_automation),
                               ("Ctrl+5", self.open_scheduler), ("Ctrl+Z", self.undo_last),
                               ("Ctrl+Shift+V", self.run_clipboard_suggestion)):
            QShortcut(QKeySequence(sequence), self).activated.connect(slot)

    # --------------------------------------------------------------- painting
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p = self.palette_ref
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 0 if self.isMaximized() else 24
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0.0, QColor(p.bg0))
        background.setColorAt(0.55, QColor(p.bg1))
        background.setColorAt(1.0, QColor(p.bg2))
        painter.fillPath(path, background)

        # accent bloom in the top-left / bottom-right
        bloom = QRadialGradient(rect.topLeft() + QPoint(int(rect.width() * 0.16), int(rect.height() * 0.1)),
                                rect.width() * 0.55)
        bloom.setColorAt(0.0, QColor(theme.alpha(p.accent, 0.16 if p.dark else 0.10)))
        bloom.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setClipPath(path)
        painter.fillRect(rect, bloom)

        bloom2 = QRadialGradient(rect.bottomRight() - QPoint(int(rect.width() * 0.2), int(rect.height() * 0.16)),
                                 rect.width() * 0.5)
        bloom2.setColorAt(0.0, QColor(theme.alpha(p.accent2, 0.12 if p.dark else 0.07)))
        bloom2.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, bloom2)

        # glass sheen
        sheen = QLinearGradient(rect.topLeft(), QPoint(rect.left(), rect.top() + rect.height() * 0.4))
        sheen.setColorAt(0.0, QColor(255, 255, 255, 22 if p.dark else 60))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, sheen)

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor(theme.alpha(p.border, 0.95)))
        painter.drawPath(path)

        if p.dark:
            inner = QPainterPath()
            inner.addRoundedRect(rect.adjusted(1, 1, -1, -1), max(0, radius - 1), max(0, radius - 1))
            painter.setPen(QColor(255, 255, 255, 14))
            painter.drawPath(inner)

        # bottom accent line
        line = QLinearGradient(rect.left(), 0, rect.right(), 0)
        line.setColorAt(0.0, QColor(theme.alpha(p.accent, 0.0)))
        line.setColorAt(0.5, QColor(theme.alpha(p.accent, 0.55)))
        line.setColorAt(1.0, QColor(theme.alpha(p.accent, 0.0)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(line)
        painter.drawRoundedRect(QRectF(rect.left() + rect.width() * 0.3, rect.bottom() - 2,
                                       rect.width() * 0.4, 2), 1, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_overlays()
        self._maximized = self.isMaximized()

    def _layout_overlays(self) -> None:
        rect = self.rect()
        margin = 18
        self._dim.setGeometry(rect)
        panel_width = min(680, max(520, rect.width() - 260))
        self.tool_panel.setFixedWidth(panel_width)
        self.tool_panel.setGeometry(rect.width() - panel_width - margin, margin + 60,
                                    panel_width, rect.height() - 2 * margin - 60)
        self.settings_panel.setGeometry(rect)
        self.jobs_drawer.setGeometry(rect)
        self.drop_zone.setGeometry(rect)
        self.dashboard.setGeometry(rect)
        self.plan_panel.setGeometry(rect)
        self.workflow_panel.setGeometry(rect)
        self.automation_panel.setGeometry(rect)
        self.scheduler_panel.setGeometry(rect)
        if getattr(self, "_nav_rail", None) is not None:
            height = 5 * 44 + 12
            self._nav_rail.setGeometry(24, 78, 46, height)
        search_height = 470
        search_width = min(700, rect.width() - 120)
        self.quick_search.setGeometry((rect.width() - search_width) // 2, 96, search_width, search_height)
        self.toast._reposition()

    # ------------------------------------------------------------ navigation
    def show_home(self, animate: bool = True, close_overlay: bool = True) -> None:
        if close_overlay and self._overlay is not None:
            self._close_overlay()
        self._category_key = "home"
        nodes: List[Node] = []
        favorites = [get_tool(tool_id) for tool_id in settings.favorites]
        favorites = [tool for tool in favorites if tool]
        for spec in favorites[:3]:
            cat = get_category(spec.category)
            nodes.append(Node(id=f"tool:{spec.id}", label=spec.name_for(self.lang), icon=spec.icon,
                              color=cat.color, sub=spec.desc_for(self.lang), kind="tool"))
        for cat, count in registry.categories_with_counts():
            nodes.append(Node(id=f"cat:{cat.key}", label=cat.name_for(self.lang), icon=cat.icon,
                              color=cat.color, sub=cat.tagline_for(self.lang),
                              badge=str(count) if count else "", kind="category"))
        self._pages = [nodes]
        self.radial.set_page_state(0, 1)
        self.radial.set_nodes(
            nodes, title=t("app.title"), subtitle=t("home.hint"),
            center_icon="star", center_color=self.palette_ref.accent, allow_back=False, animate=animate,
        )
        self._back_button.setVisible(False)
        self._tagline_label.setText(APP_TAGLINE if self.lang == "fa" else APP_TAGLINE_EN)

    def open_category(self, key: str, page: int = 0) -> None:
        if key == "home":
            self.show_home()
            return
        if self._overlay is not None:
            self._close_overlay()
        cat = get_category(key)
        tools = registry.tools_in(key)
        if not tools:
            self._show_toast(t("as.no_result"), "warning")
            return
        self._category_key = key
        pages: List[List[Node]] = []
        for start in range(0, len(tools), TOOLS_PER_PAGE):
            chunk = tools[start:start + TOOLS_PER_PAGE]
            pages.append([
                Node(id=f"tool:{spec.id}", label=spec.name_for(self.lang), icon=spec.icon,
                     color=cat.color, sub=spec.desc_for(self.lang), kind="tool")
                for spec in chunk
            ])
        self._pages = pages
        self._back_button.setVisible(True)
        self._tagline_label.setText(f"{cat.name_for(self.lang)} · {len(tools)} "
                                    f"{t('center.tools')}")
        self._show_tool_page(page, animate=True)

    def _show_tool_page(self, page: int, animate: bool = True) -> None:
        if not self._pages:
            return
        page = max(0, min(page, len(self._pages) - 1))
        cat = get_category(self._category_key)
        subtitle = cat.tagline_for(self.lang)
        if len(self._pages) > 1:
            subtitle += "   ·   " + ("با چرخ ماوس صفحه‌ها را عوض کنید" if self.lang == "fa"
                                      else "scroll to switch pages")
        self.radial.set_page_state(page, len(self._pages))
        self.radial.set_nodes(
            self._pages[page], title=cat.name_for(self.lang), subtitle=subtitle,
            center_icon=cat.icon, center_color=cat.color, allow_back=True, animate=animate,
        )

    def _on_page_changed(self, page: int, _pages: int) -> None:
        if self._category_key == "home":
            return
        self._show_tool_page(page, animate=False)

    def _rebuild_page(self) -> None:
        if self._category_key == "home":
            self.show_home(animate=False, close_overlay=False)
        else:
            self._show_tool_page(self.radial._page, animate=False)

    def _on_node_clicked(self, node_id: str) -> None:
        if node_id.startswith("cat:"):
            self.open_category(node_id.split(":", 1)[1])
        elif node_id.startswith("tool:"):
            self._open_tool_by_id(node_id.split(":", 1)[1])

    def _on_center_clicked(self) -> None:
        if self._category_key != "home":
            self.show_home()
        else:
            self.open_search()

    # --------------------------------------------------------------- overlays
    def _open_tool_by_id(self, tool_id: str, values: Optional[Dict] = None) -> None:
        spec = get_tool(tool_id)
        if spec is None:
            self._show_toast(t("as.no_result"), "error")
            return
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        self.tool_panel.load(spec, self.lang, settings.output_dir, values=values)
        self.tool_panel.slide_in(self.tool_panel.width(), self.tool_panel.height())
        self.tool_panel.raise_()
        self._overlay = "tool"
        self.tool_panel.setFocus()

    def open_search(self) -> None:
        if self._overlay == "tool":
            return
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        self.quick_search.raise_()
        self.quick_search.open_palette()
        self._overlay = "search"

    def _on_search_closed(self) -> None:
        # the palette already hid itself; only clean up the dim layer here
        if self._overlay == "search":
            self._overlay = None
            self._dim.hide()
            self.radial.setFocus()

    def open_settings(self) -> None:
        if self._overlay == "tool":
            return
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        self.settings_panel.raise_()
        self.settings_panel.open_panel()
        self._overlay = "settings"
        self.settings_panel.setFocus()

    def open_jobs(self) -> None:
        if self._overlay == "tool":
            return
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        self.jobs_drawer.raise_()
        self.jobs_drawer.open_panel()
        self._overlay = "jobs"

    def _on_job_queued(self, _job_id: str) -> None:
        """A panel run went to the background: show the queue so progress is visible."""
        if self._overlay == "tool":
            self._close_overlay()
        self.open_jobs()

    def _on_parallel_changed(self, count: int) -> None:
        if self.jobs is not None:
            self.jobs.max_parallel = max(1, int(count))
            self.jobs._pump()
            self._show_toast(f"{t('st.parallel')}: {count}", "success")

    def _on_plugins_changed(self) -> None:
        registry.load_all()
        self._rebuild_page()
        self._show_toast(t("st.plugins_reload"), "success")

    def _rerun_job(self, tool_id: str, values: Dict) -> None:
        self._close_overlay()
        self._open_tool_by_id(tool_id, values or {})

    def _update_jobs_badge(self) -> None:
        stats = self.jobs.stats() if self.jobs is not None else {"active": 0}
        active = int(stats.get("active", 0))
        if active:
            self._jobs_button.setText(f"◆ {t('jb.badge', active)}")
        else:
            self._jobs_button.setText(f"◆ {t('cmd.jobs')}")
        self._jobs_button.setToolTip(t("jb.badge", active) if active else t("cmd.jobs"))

    def run_doctor(self) -> None:
        """Open the settings sheet on the diagnostics section."""
        self.open_settings()
        try:
            self.settings_panel.scroll_to_system()
        except Exception:
            pass

    # ============================================================ v3 surfaces
    def _build_nav_rail(self) -> None:
        """A slim rail on the left edge: dashboard, planner, workflows, automation."""
        self._nav_rail = QWidget(self)
        self._nav_rail.setObjectName("NavRail")
        layout = QVBoxLayout(self._nav_rail)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(6)
        entries = (
            ("dashboard", "chart", self.open_dashboard, "cmd.dashboard", "Ctrl+1"),
            ("plan", "sparkles", self.open_plan, "cmd.plan", "Ctrl+2"),
            ("workflows", "layers", self.open_workflows, "cmd.workflows", "Ctrl+3"),
            ("automation", "refresh", self.open_automation, "cmd.automation", "Ctrl+4"),
            ("scheduler", "clock", self.open_scheduler, "cmd.scheduler", "Ctrl+5"),
        )
        self._nav_buttons = []
        for key, icon, slot, tooltip_key, shortcut in entries:
            button = QPushButton()
            button.setObjectName("NavButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(38, 38)
            button.setProperty("navKey", key)
            button.setProperty("tooltipKey", tooltip_key)
            button.setProperty("shortcut", shortcut)
            button.setIcon(_make_icon(icon, QColor(self.palette_ref.text_dim), 34))
            button.clicked.connect(slot)
            # icon-only buttons are invisible to screen readers without a name
            button.setAccessibleName(t(tooltip_key))
            button.setAccessibleDescription(f"{t(tooltip_key)} ({shortcut})")
            layout.addWidget(button)
            self._nav_buttons.append(button)
        layout.addStretch(1)
        self._nav_rail.setStyleSheet(
            f"#NavRail{{background:{self.palette_ref.panel}CC;border:1px solid {self.palette_ref.border};"
            f"border-radius:16px;}}"
            f"#NavRail QPushButton#NavButton{{border:none;border-radius:12px;background:transparent;}}"
            f"#NavRail QPushButton#NavButton:hover{{background:{self.palette_ref.panel_alt};}}")
        self._nav_rail.show()
        self._retranslate_nav_rail()

    def _recolor_nav_rail(self) -> None:
        if not getattr(self, "_nav_buttons", None):
            return
        icons = ("chart", "sparkles", "layers", "refresh", "clock")
        for button, icon in zip(self._nav_buttons, icons, strict=False):   # min-length pairing on purpose
            button.setIcon(_make_icon(icon, QColor(self.palette_ref.text_dim), 34))

    def _retranslate_nav_rail(self) -> None:
        if not getattr(self, "_nav_buttons", None):
            return
        for button in self._nav_buttons:
            key = button.property("tooltipKey")
            shortcut = button.property("shortcut")
            text = t(key) if key else ""
            button.setToolTip(f"{text}  ({shortcut})" if key else "")
            # keep the screen-reader name in sync with the language toggle
            button.setAccessibleName(text)
            button.setAccessibleDescription(f"{text} ({shortcut})")

    def _open_sheet(self, overlay: str, sheet) -> None:
        if self._overlay == "tool":
            self._close_overlay()
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        sheet.raise_()
        sheet.open_panel()
        self._overlay = overlay
        sheet.setFocus()

    def open_dashboard(self) -> None:
        self._open_sheet("dashboard", self.dashboard)

    def open_plan(self, paths: Optional[List[str]] = None) -> None:
        if paths:
            self.plan_panel.set_paths(paths)
        else:
            self.plan_panel.set_paths(self._last_paths)
        self._open_sheet("plan", self.plan_panel)

    def open_workflows(self, workflow_id: str = "") -> None:
        self.workflow_panel.set_paths(self._last_paths)
        self._open_sheet("workflows", self.workflow_panel)
        if workflow_id:
            self.workflow_panel.select(workflow_id)

    def open_automation(self) -> None:
        self._open_sheet("automation", self.automation_panel)

    def open_scheduler(self) -> None:
        self._open_sheet("scheduler", self.scheduler_panel)
        if not self.scheduler.running:
            self.scheduler.start()

    # ------------------------------------------------------------ v4 automation
    def _release_bg_runner(self, attr: str, runner) -> None:
        pool = getattr(self, attr, None)
        if isinstance(pool, list) and runner in pool:
            pool.remove(runner)
        try:
            runner.deleteLater()
        except Exception:
            pass

    def _on_schedule_triggered(self, schedule, reason: str = "auto") -> None:
        """A cron schedule fired: queue its workflow, macro, or tool."""
        from ..core.execution import (ExecutionPolicy, OperationRisk, classify_invocation,
                                      classify_steps)

        def blocked() -> None:
            schedule_store.set_status(schedule.id, "blocked")
            self._show_toast(
                "اجرای خودکار عملیات نیازمند تأیید مسدود است؛ آن را دستی اجرا کنید."
                if self.lang != "en" else
                "Scheduled operations requiring confirmation are blocked; run them manually.",
                "warning",
            )

        try:
            schedule_store.mark_run(schedule.id, "running")
        except OSError as exc:
            self._show_toast(("ثبت اجرای زمان‌بندی ناموفق بود: " if self.lang != "en"
                              else "Could not record scheduled run: ") + str(exc), "error")
            return
        title = schedule.title(self.lang)
        sched_policy = ExecutionPolicy.for_scheduler()
        kind = getattr(schedule, "kind", "workflow")
        if kind == "workflow":
            workflow = workflow_store.get(schedule.target_id)
            if workflow is None:
                schedule_store.set_status(schedule.id, "failed")
                self._show_toast(f"{title}: " + ("گردش‌کار پیدا نشد" if self.lang != "en"
                                                 else "workflow not found"), "warning")
                return
            risk, _ = classify_steps([(step.tool_id, step.values) for step in workflow.steps])
            if risk >= OperationRisk.CONFIRM:
                blocked()
                return
            runner = WorkflowRunner(
                workflow, self.lang, settings.output_dir,
                dict(getattr(schedule, "payload", {}) or {}), self,
                policy=sched_policy,
            )
            runner.finished.connect(self._on_workflow_done)
            runner.finished.connect(lambda res, sid=schedule.id: schedule_store.set_status(sid, res.status))
            runner.finished.connect(lambda _res, r=runner: self._release_bg_runner("_scheduler_runners", r))
            self._scheduler_runners = getattr(self, "_scheduler_runners", [])
            self._scheduler_runners.append(runner)
            runner.start()
            self._show_toast(("⏱ " if reason == "auto" else "▶ ") + title, "info")
        elif kind == "macro":
            payload = dict(getattr(schedule, "payload", {}) or {})
            macro_file = str(payload.get("file") or schedule.target_id or "").strip()
            if not macro_file or not Path(macro_file).is_file():
                schedule_store.set_status(schedule.id, "failed")
                self._show_toast(f"{title}: " + ("فایل ماکرو پیدا نشد" if self.lang != "en"
                                                 else "macro file not found"), "warning")
                return
            macro_values = {"file": macro_file, "dry_run": bool(payload.get("dry_run", False))}
            if classify_invocation("macro_run", macro_values).requires_confirmation:
                blocked()
                return
            job = self.jobs.submit_by_id(
                "macro_run",
                macro_values,
                policy=sched_policy,
            )
            if job is None:
                schedule_store.set_status(schedule.id, "failed")
                self._show_toast(f"{title}: " + ("ابزار ماکرو پیدا نشد" if self.lang != "en"
                                                 else "macro runner unavailable"), "warning")
                return
            self._track_schedule_job(schedule.id, job)
            self._update_jobs_badge()
            self._show_toast(("⏱ " if reason == "auto" else "▶ ") + title, "info")
        else:
            tool_values = dict(getattr(schedule, "payload", {}) or {})
            if classify_invocation(schedule.target_id, tool_values).requires_confirmation:
                blocked()
                return
            job = self.jobs.submit_by_id(
                schedule.target_id,
                tool_values,
                policy=sched_policy,
            )
            if job is None:
                schedule_store.set_status(schedule.id, "failed")
                self._show_toast(f"{title}: " + ("ابزار پیدا نشد" if self.lang != "en"
                                                 else "tool not found"), "warning")
                return
            self._track_schedule_job(schedule.id, job)
            self._update_jobs_badge()
            self._show_toast(("⏱ " if reason == "auto" else "▶ ") + title, "info")

    def _track_schedule_job(self, schedule_id: str, job) -> None:
        def report(finished_job) -> None:
            if finished_job.id != job.id:
                return
            try:
                self.jobs.jobFinished.disconnect(report)
            except (RuntimeError, TypeError):
                pass
            schedule_store.set_status(schedule_id, finished_job.status)
        self.jobs.jobFinished.connect(report)
        if not job.active:
            report(job)

    def _on_macro_run(self, path, dry_run: bool = False) -> None:
        """Run a macro file through the macro tool (queued, cancellable, logged)."""
        if not path:
            return
        from ..core.execution import ExecutionPolicy, classify_invocation, snapshot_invocation
        from ..core.spec import ToolError

        try:
            values = snapshot_invocation("macro_run", {"file": str(path), "dry_run": bool(dry_run)})
        except ToolError as exc:
            self._show_toast(str(exc), "error")
            return
        assessment = classify_invocation("macro_run", values)
        if assessment.requires_confirmation:
            from PySide6.QtWidgets import QMessageBox
            prompt = ("این ماکرو ممکن است فایل‌ها یا تنظیمات را تغییر دهد. ادامه می‌دهید؟"
                      if self.lang != "en" else
                      "This macro may change files or settings. Continue?")
            prompt += "\n\n" + assessment.reason_for(self.lang)
            if QMessageBox.question(self, "WinGenius", prompt,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        job = self.jobs.submit_by_id("macro_run", values,
                                     policy=ExecutionPolicy.for_gui(confirmed=assessment.requires_confirmation,
                                                                    source="macro"))
        if job is None:
            self._show_toast("ماکرو صف نشد." if self.lang != "en" else "macro not queued", "error")
            return
        self._update_jobs_badge()
        self._show_toast(("ماکرو صف شد: " if self.lang != "en" else "macro queued: ")
                         + Path(str(path)).stem, "info")

        def _report(finished_job, job_id: str = job.id) -> None:
            """Report the real outcome once *this* job finishes.

            The previous code read `result` straight after queuing, where no result
            exists yet — it raised NameError the moment a macro was run from the
            scheduler sheet.
            """
            if getattr(finished_job, "id", "") != job_id:
                return
            try:
                self.jobs.jobFinished.disconnect(_report)
            except (RuntimeError, TypeError):
                pass
            if getattr(finished_job, "error", ""):
                self._show_toast(str(finished_job.error)[:140], "error")
                return
            outcome = getattr(finished_job, "result", None)
            message = (getattr(outcome, "message", "") or getattr(finished_job, "message", "")
                       or ("ماکرو تمام شد." if self.lang != "en" else "macro finished"))
            self._show_toast(message.splitlines()[0][:160], "success")

        self.jobs.jobFinished.connect(_report)

    def toggle_api_server(self) -> None:
        """Start/stop the local JSON API and show the URL + token."""
        try:
            if getattr(self, "_api", None) is not None and self._api.running:
                self._api.stop()
                self._api = None
                self._show_toast("API محلی خاموش شد." if self.lang != "en"
                                 else "local API stopped", "info")
                return
            from ..core.api_server import ApiServer

            self._api = ApiServer(port=0, lang=self.lang, output_dir=settings.output_dir,
                                  jobs=self.jobs)
            port = self._api.start()
            self._show_toast(
                (f"API روشن شد: http://127.0.0.1:{port} · توکن در پوشه‌ی داده" if self.lang != "en"
                 else f"API on: http://127.0.0.1:{port} · token in the data folder"), "success")
        except Exception as exc:                      # noqa: BLE001
            self._show_toast(f"API: {exc}", "error")

    def undo_last(self) -> None:
        """Ctrl+Z: reverse the newest journal entry."""
        results = journal().undo_last(1)
        if not results:
            self._show_toast("چیزی برای بازگردانی نیست." if self.lang != "en"
                             else "Nothing to undo", "info")
            return
        item = results[0]
        if item["status"] == "failed":
            self._show_toast(("بازگردانی ناموفق: " if self.lang != "en" else "undo failed: ")
                             + (item["problems"][0] if item["problems"] else ""), "error")
        else:
            self._show_toast(("↺ بازگردانی شد: " if self.lang != "en" else "↺ undone: ")
                             + item["operation"].title(self.lang), "success")
        if self._overlay == "dashboard":
            self.dashboard.refresh() if hasattr(self.dashboard, "refresh") else None

    def _on_dashboard_action(self, action: str) -> None:
        if action == "plan":
            self.open_plan()
        elif action == "workflows":
            self.open_workflows()
        elif action == "automation":
            self.open_automation()
        elif action == "jobs":
            self.open_jobs()
        elif action == "doctor":
            self.run_doctor()
        elif action == "index":
            self._open_tool_by_id("index_search")
        elif action == "settings":
            self.open_settings()

    # ------------------------------------------------------------- automation
    def _start_services(self) -> None:
        """Start the folder watcher and the tray icon when the platform allows it."""
        try:
            from ..core.watcher import store as watch_store

            if any(rule.enabled for rule in watch_store.rules()):
                self.watcher.start()
        except Exception:
            pass
        try:
            if any(item.enabled for item in schedule_store.all()):
                self.scheduler.start()
        except Exception:
            pass
        if tray_available():
            icon_path = Path(__file__).resolve().parents[1] / "assets" / "icon.png"
            self.tray = Tray(icon_path, self)
            self.tray.openRequested.connect(self._restore_from_tray)
            self.tray.paletteRequested.connect(self._restore_from_tray_and_search)
            self.tray.planRequested.connect(self._restore_from_tray_and_plan)
            self.tray.jobsRequested.connect(self._restore_from_tray_and_jobs)
            self.tray.automationRequested.connect(self._restore_from_tray_and_automation)
            self.tray.quitRequested.connect(self.close)
        if bool(settings.get("ui/auto_check_updates")) and not os.environ.get("WINGENIUS_NO_LAUNCH"):
            self.check_updates_background()

    def check_updates_background(self, url: Optional[str] = None) -> None:
        """Run a non-blocking update check and surface the banner if a newer version exists."""
        from .update_dialog import UpdateCheckWorker
        from ..core.updates import RELEASES_URL

        if self._bg_update_worker is not None and self._bg_update_worker.isRunning():
            return
        worker = UpdateCheckWorker(url=url or RELEASES_URL, parent=self)
        worker.checkFinished.connect(self._on_bg_update_checked)
        self._bg_update_worker = worker
        worker.start()

    def _on_bg_update_checked(self, result: dict) -> None:
        self._last_update_check = dict(result)
        if result.get("ok") and result.get("update_available"):
            self.update_banner.show_release(result)
            latest = result.get("latest", "")
            self._show_toast(
                f"نسخه‌ی جدید {latest} منتشر شد!" if self.lang == "fa" else f"Update v{latest} is available!",
                "info",
            )

    def open_update_dialog(self, check_data: Optional[dict] = None) -> None:
        from .update_dialog import UpdateDialog

        initial = check_data or self._last_update_check
        dlg = UpdateDialog(lang=self.lang, initial_check=initial, parent=self)
        dlg.exec()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _restore_from_tray_and_search(self) -> None:
        self._restore_from_tray()
        self.open_search()

    def _restore_from_tray_and_plan(self) -> None:
        self._restore_from_tray()
        self.open_plan()

    def _restore_from_tray_and_jobs(self) -> None:
        self._restore_from_tray()
        self.open_jobs()

    def _restore_from_tray_and_automation(self) -> None:
        self._restore_from_tray()
        self.open_automation()

    def _on_watch_triggered(self, rule, path: str) -> None:
        """The watcher found a new file: queue the rule's action."""
        mark_run(rule, path, "auto")
        self._run_watch_rule(rule, path)
        self._show_toast(f"👁 {rule.title(self.lang)} ← {Path(path).name}", "info")

    def _run_watch_rule(self, rule, path: str) -> None:
        from ..core.execution import ExecutionPolicy

        allow_danger = bool(settings.get("automation/allow_dangerous") or False)
        watch_policy = ExecutionPolicy.for_watcher(allow_dangerous=allow_danger)
        if getattr(rule, "action", "workflow") == "workflow":
            workflow = workflow_store.get(rule.target_id)
            if workflow is None:
                self._show_toast(t("as.no_result"), "warning")
                return
            runner = WorkflowRunner(
                workflow, self.lang, settings.output_dir,
                {"__paths__": [path]}, self,
                policy=watch_policy,
            )
            runner.finished.connect(self._on_workflow_done)
            runner.finished.connect(lambda _res, r=runner: self._release_bg_runner("_watch_runners", r))
            self._watch_runners = getattr(self, "_watch_runners", [])
            self._watch_runners.append(runner)
            runner.start()
        else:
            values = dict(getattr(rule, "values", {}) or {})
            spec = get_tool(rule.target_id)
            if spec is None:
                return
            from ..core.workflows import autofill

            if not values:
                values = autofill(spec, [path])
            else:
                values.setdefault("input", path)
            self.jobs.submit_by_id(rule.target_id, values, policy=watch_policy)
            self.open_jobs()

    def _on_workflow_done(self, result) -> None:
        """Any finished chain refreshes the dashboard and the badges."""
        self._update_jobs_badge()
        if getattr(result, "ok", False):
            self._show_toast(result.message or "✓", "success")
        elif getattr(result, "status", "") == "failed":
            self._show_toast(result.message or "✗", "error")

    # -------------------------------------------------------------- clipboard
    def _on_clipboard_suggestion(self, tool_id: str, kind: str, payload: str) -> None:
        self._clip_hint = (tool_id, kind, payload)
        spec = get_tool(tool_id)
        name = spec.name_for(self.lang) if spec else tool_id
        message = (f"کلیپ‌بورد: {kind} — {name}؟  (Ctrl+Shift+V)" if self.lang == "fa"
                   else f"Clipboard: {kind} — {name}?  (Ctrl+Shift+V)")
        self._show_toast(message, "info")

    def run_clipboard_suggestion(self) -> None:
        if not self._clip_hint:
            self._show_toast("چیزی در کلیپ‌بورد نبود." if self.lang == "fa" else "Clipboard is empty.",
                             "warning")
            return
        tool_id, _kind, payload = self._clip_hint
        values: Dict = {}
        if payload == "__image__":
            image = QApplication.clipboard().image()
            if not image.isNull():
                target_dir = Path(settings.output_dir or Path.home())
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / "clipboard.png"
                image.save(str(target))
                values = {"src": str(target)}
        elif _kind == "files":
            values = {"__paths__": [line for line in payload.splitlines() if line.strip()]}
        elif tool_id == "text_summarize":
            values = {"text": payload}
        else:
            key = "text"
            spec = get_tool(tool_id)
            if spec is not None:
                for field in spec.fields:
                    if field.kind in ("text", "multiline"):
                        key = field.key
                        break
            values = {key: payload}
        self._open_tool_by_id(tool_id, values)

    def _on_command(self, command: str) -> None:
        if command == "dashboard":
            self.open_dashboard()
        elif command == "plan":
            self.open_plan()
        elif command == "workflows":
            self.open_workflows()
        elif command == "automation":
            self.open_automation()
        elif command == "scheduler":
            self.open_scheduler()
        elif command == "undo":
            self.undo_last()
        elif command == "api":
            self.toggle_api_server()
        elif command == "vault":
            self._open_tool_by_id("vault_list")
        elif command == "index":
            self._open_tool_by_id("index_search")
        elif command == "jobs":
            self.open_jobs()
        elif command == "doctor":
            self.run_doctor()
        elif command == "update":
            self.open_update_dialog()
        elif command == "plugins":
            from ..core.plugins import open_plugins_folder

            open_plugins_folder()
            self._show_toast(t("st.plugins_open"), "success")
        elif command == "settings":
            self.open_settings()
        elif command == "cli":
            from PySide6.QtWidgets import QApplication as _App

            command_line = self.tool_panel.cli_command() if self.tool_panel.spec else "WinGenius.exe --list"
            _App.clipboard().setText(command_line)
            self._show_toast(t("st.copied"), "success")

    # ------------------------------------------------------------------- drops
    def _on_drop_choice(self, tool_id: str, paths: List[str]) -> None:
        self._last_paths = list(paths or [])
        self._close_overlay()
        if tool_id:
            self._open_tool_by_id(tool_id, {"__paths__": list(paths)})
        else:
            self.open_search()

    def _browse_for_drop(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        files, _filter = QFileDialog.getOpenFileNames(self, t("dz.browse"))
        if files:
            self._show_drop(files)

    def _show_drop(self, paths: List[str]) -> None:
        if not paths:
            return
        if self._overlay is not None:
            self._close_overlay()
        self._show_dim()
        self.drop_zone.raise_()
        self.drop_zone.show_files(paths)
        self._overlay = "drop"

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                self._show_drop(paths)
                return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self._show_drop(paths)
            return
        super().dropEvent(event)

    def _show_dim(self) -> None:
        self._dim.show()
        self._dim.raise_()

    def _close_overlay(self) -> None:
        overlay, self._overlay = self._overlay, None
        if overlay == "tool":
            self.tool_panel.dismiss()
        elif overlay == "search":
            self.quick_search.close_palette()
        elif overlay == "settings":
            self.settings_panel.close_panel()
        elif overlay == "jobs":
            self.jobs_drawer.close_panel()
        elif overlay == "drop":
            self.drop_zone.close_panel()
        elif overlay == "dashboard":
            self.dashboard.close_panel()
        elif overlay == "plan":
            self.plan_panel.close_panel()
        elif overlay == "workflows":
            self.workflow_panel.close_panel()
        elif overlay == "automation":
            self.automation_panel.close_panel()
        elif overlay == "scheduler":
            self.scheduler_panel.close_panel()
        self._dim.hide()
        self.radial.setFocus()

    def closeEvent(self, event) -> None:
        stopped = self.tool_panel.shutdown()
        for panel_attr in ("plan_panel", "workflow_panel"):
            panel = getattr(self, panel_attr, None)
            if panel is not None and hasattr(panel, "shutdown"):
                try:
                    if not panel.shutdown():
                        stopped = False
                except Exception:
                    stopped = False
        for pool_attr in ("_scheduler_runners", "_watch_runners"):
            for runner in list(getattr(self, pool_attr, []) or []):
                try:
                    if not runner.shutdown(1500):
                        stopped = False
                except Exception:
                    stopped = False
        if getattr(self, "_bg_update_worker", None) is not None and self._bg_update_worker.isRunning():
            self._bg_update_worker.quit()
            if not self._bg_update_worker.wait(1500):
                stopped = False
        if getattr(self, "jobs", None) is not None and not self.jobs.shutdown():
            stopped = False
        if not stopped:
            self._show_toast(
                "کارهای در حال اجرا هنوز متوقف نشده‌اند؛ کمی بعد دوباره ببندید." if self.lang == "fa"
                else "Some jobs are still stopping. Please try closing again shortly.", "error")
            event.ignore()
            return
        for service in ("scheduler",):
            try:
                getattr(self, service).stop()
            except Exception:
                pass
        try:
            if getattr(self, "_api", None) is not None:
                self._api.stop()
                self._api = None
        except Exception:
            pass
        try:
            self.watcher.stop()
            self.clipboard_watcher.set_enabled(False)
            if self.tray is not None:
                self.tray.hide()
        except Exception:
            pass
        settings.set("app/window_pos", f"{self.x()},{self.y()}")
        super().closeEvent(event)

    # ---------------------------------------------------------------- theming
    def _apply_theme(self, rebuild: bool = True) -> None:
        self.palette_ref = theme.build_palette(settings.theme, settings.accent)
        app = QApplication.instance()
        direction = "rtl" if self.lang == "fa" else "ltr"
        if app is not None:
            app.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft if self.lang == "fa" else Qt.LayoutDirection.LeftToRight
            )
            app.setStyleSheet(theme.stylesheet(self.palette_ref, direction=direction))
        self.radial.apply_palette(self.palette_ref, settings.animations, settings.get("ui/glow"))
        self.tool_panel.apply_palette(self.palette_ref)
        self.quick_search.apply_palette(self.palette_ref)
        self.settings_panel.apply_palette(self.palette_ref)
        self.jobs_drawer.apply_palette(self.palette_ref)
        self.drop_zone.apply_palette(self.palette_ref)
        self.dashboard.apply_palette(self.palette_ref)
        self.plan_panel.apply_palette(self.palette_ref)
        self.workflow_panel.apply_palette(self.palette_ref)
        self.automation_panel.apply_palette(self.palette_ref)
        self.toast.set_palette_ref(self.palette_ref)
        self._recolor_nav_rail()
        self._update_jobs_badge()
        self._dim.apply_palette(self.palette_ref)
        self._logo.setPixmap(pixmap_icon("star", QColor(self.palette_ref.accent), 34))
        self._recolor_titlebar_icons()
        self._retranslate()
        if rebuild:
            self._rebuild_page()
        self.update()

    def _recolor_titlebar_icons(self) -> None:
        """Keep the title-bar glyphs readable in every palette."""
        dim = QColor(self.palette_ref.text_dim)
        strong = QColor(self.palette_ref.text)
        self._settings_button.setIcon(_make_icon("sliders", dim, 34))
        self._minimize_button.setIcon(_make_icon("minus", dim, 34))
        self._sync_max_button()
        self._close_button.setIcon(_make_icon("x", QColor(self.palette_ref.danger), 34))
        self._back_button.setIcon(_make_icon("back", strong, 34))

    def _retranslate(self) -> None:
        self._title_label.setText(APP_NAME)
        self._tagline_label.setText(APP_TAGLINE if self.lang == "fa" else APP_TAGLINE_EN)
        self._search_button.setText(t("home.search"))
        self._lang_button.setText(t("tb.lang"))
        self._settings_button.setToolTip(t("tb.settings"))
        self._minimize_button.setToolTip(t("tb.minimize"))
        self._max_button.setToolTip(t("tb.restore") if self.isMaximized() else t("tb.maximize"))
        self._close_button.setToolTip(t("tb.close"))
        self._back_button.setToolTip(t("center.back"))
        self._settings_button.setAccessibleName(t("tb.settings"))
        self._minimize_button.setAccessibleName(t("tb.minimize"))
        self._sync_max_button()
        self._close_button.setAccessibleName(t("tb.close"))
        self._back_button.setAccessibleName(t("center.back"))
        self._search_button.setAccessibleName(t("home.search"))
        self._lang_button.setAccessibleName(t("tb.lang"))
        self.quick_search.set_language(self.lang)
        self.tool_panel.set_language(self.lang)
        self.settings_panel.set_language(self.lang)
        self.jobs_drawer.set_language(self.lang)
        self.drop_zone.set_language(self.lang)
        self.dashboard.set_language(self.lang)
        self.plan_panel.set_language(self.lang)
        self.workflow_panel.set_language(self.lang)
        self.automation_panel.set_language(self.lang)
        self.scheduler_panel.set_language(self.lang)
        if getattr(self, "update_banner", None) is not None:
            self.update_banner.set_language(self.lang)
        if self.tray is not None:
            self.tray.set_language(self.lang)
        self._retranslate_nav_rail()
        self._jobs_button.setText(f"◆ {t('cmd.jobs')}")
        self._update_jobs_badge()

    def _toggle_language(self) -> None:
        new_lang = "en" if self.lang == "fa" else "fa"
        self._on_language_changed(new_lang)

    def _on_language_changed(self, lang: str) -> None:
        self.lang = lang
        settings.lang = lang
        set_language(lang)
        self._apply_theme(rebuild=False)
        self._rebuild_page()
        # `_apply_theme()` already retranslates every panel, and
        # `ToolPanel.set_language()` re-renders the open form *with the values the
        # user has typed*. Reloading here as well reset the form to its defaults
        # (a half-filled form was wiped by pressing the EN/فا button).

    def _show_toast(self, message: str, kind: str = "info") -> None:
        self.toast.set_palette_ref(self.palette_ref)
        self.toast.show_toast(message, kind)
        self.toast.raise_()

    # ---------------------------------------------------------------- window
    def _restore_position(self) -> None:
        raw = str(settings.get("app/window_pos") or "")
        if "," in raw:
            try:
                x, y = (int(part) for part in raw.split(",", 1))
                screen = QGuiApplication.primaryScreen().availableGeometry()
                if screen.contains(QPoint(x + 200, y + 120)):
                    self.move(x, y)
                    return
            except ValueError:
                pass
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._overlay:
                self._close_overlay()
                return
            if self._category_key != "home":
                self.show_home()
                return
        super().keyPressEvent(event)
