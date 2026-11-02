"""Workflow sheet: browse, build, reorder, save and run tool chains."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..core import nlsearch
from ..core.registry import category as get_category
from ..core.registry import get as get_tool
from ..core.registry import tools_in
from ..core.settings import settings
from ..core.theme import Palette
from ..core.workflows import (
    Step, Workflow, WorkflowResult, WorkflowRunner, autofill, chain_values, input_field,
    missing_required, store,
)
from .overlay import OverlaySheet
from .widgets import divider, label, pixmap_icon


class StepRow(QFrame):
    """Editable step row inside the workflow editor."""

    moved = Signal(int, int)
    removed = Signal(int)
    selected = Signal(int)

    def __init__(self, index: int, step: Step, lang: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.step_data = step
        self.setObjectName("WfStep")
        self.setStyleSheet(
            f"#WfStep{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:10px;}}")
        self.setFixedHeight(52)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)
        spec = get_tool(step.tool_id)
        if spec is not None:
            icon = QLabel()
            icon.setFixedSize(24, 24)
            icon.setPixmap(pixmap_icon(spec.icon, QColor(get_category(spec.category).color), 20))
            row.addWidget(icon)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        title = label((spec.name_for(lang) if spec else step.tool_id), "RowTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(title)
        hint = label(self._hint(index, spec, lang), "FieldHelp")
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(hint)
        row.addLayout(text_box, 1)
        self.status = label("", "Hint")
        row.addWidget(self.status)
        for text, tip, slot in (("↑", "up", lambda: self.moved.emit(self.index, self.index - 1)),
                                ("↓", "down", lambda: self.moved.emit(self.index, self.index + 1)),
                                ("✕", "remove", lambda: self.removed.emit(self.index))):
            button = QPushButton(text)
            button.setObjectName("Ghost")
            button.setFixedWidth(28)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)

    @staticmethod
    def _hint(index: int, spec, lang: str) -> str:
        if spec is None:
            return "tool missing"
        key, takes_list = input_field(spec)
        if index == 0:
            return (f"ورودی از فایل‌های انتخابی ({key})" if lang != "en"
                    else f"input from picked files ({key})")
        return "↳ خروجی مرحله‌ی قبل" if lang != "en" else "↳ previous output"

    def set_status(self, status: str) -> None:
        colours = {"running": "#18C8F5", "done": "#2BD9A0", "failed": "#FF5A6E", "cancelled": "#FFC24B"}
        text = {"running": "…", "done": "✓", "failed": "✗", "cancelled": "■"}.get(status, "")
        self.status.setText(text)
        colour = colours.get(status)
        if colour:
            self.status.setStyleSheet(f"color:{colour};font-weight:700;")


class WorkflowPanel(OverlaySheet):
    """Left: the library. Right: the editor of the selected chain."""

    toastRequested = Signal(str, str)
    runFinished = Signal(object)

    card_width = 820
    body_max_height = 560

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._paths: List[str] = []
        self._workflow: Optional[Workflow] = None
        self._step_rows: List[StepRow] = []
        self._runner: Optional[WorkflowRunner] = None
        self._build_layout()
        self._build_actions()
        self.retranslate()

    # ------------------------------------------------------------------ build
    def _build_layout(self) -> None:
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(244)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        self._list = QVBoxLayout()
        self._list.setSpacing(4)
        list_holder = QWidget()
        list_holder.setLayout(self._list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(list_holder)
        scroll.setFixedHeight(262)
        left_layout.addWidget(scroll)
        self._new_button = QPushButton()
        self._new_button.setObjectName("Ghost")
        self._new_button.clicked.connect(self.new_workflow)
        left_layout.addWidget(self._new_button)
        self._dup_button = QPushButton()
        self._dup_button.setObjectName("Ghost")
        self._dup_button.clicked.connect(self.duplicate_workflow)
        left_layout.addWidget(self._dup_button)
        self._delete_button = QPushButton()
        self._delete_button.setObjectName("Ghost")
        self._delete_button.clicked.connect(self.delete_workflow)
        left_layout.addWidget(self._delete_button)
        left_layout.addStretch(1)
        holder_layout.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self._name_input = QLineEdit()
        self._name_input.setMinimumHeight(34)
        right_layout.addWidget(self._name_input)
        self._steps = QVBoxLayout()
        self._steps.setSpacing(6)
        steps_holder = QWidget()
        steps_holder.setLayout(self._steps)
        steps_scroll = QScrollArea()
        steps_scroll.setWidgetResizable(True)
        steps_scroll.setFrameShape(QFrame.Shape.NoFrame)
        steps_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        steps_scroll.setWidget(steps_holder)
        steps_scroll.setFixedHeight(140)
        self._steps_scroll = steps_scroll
        right_layout.addWidget(steps_scroll)
        right_layout.addWidget(divider())
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._search_input = QLineEdit()
        self._search_input.setMinimumHeight(32)
        self._search_input.textChanged.connect(self._refresh_picker)
        add_row.addWidget(self._search_input, 1)
        self._add_button = QPushButton()
        self._add_button.setObjectName("Ghost")
        self._add_button.clicked.connect(self.add_first_match)
        add_row.addWidget(self._add_button)
        right_layout.addLayout(add_row)
        self._picker = QVBoxLayout()
        self._picker.setSpacing(3)
        picker_holder = QWidget()
        picker_holder.setLayout(self._picker)
        picker_scroll = QScrollArea()
        picker_scroll.setWidgetResizable(True)
        picker_scroll.setFrameShape(QFrame.Shape.NoFrame)
        picker_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        picker_scroll.setWidget(picker_holder)
        picker_scroll.setFixedHeight(160)
        right_layout.addWidget(picker_scroll)
        right_layout.addStretch(1)
        holder_layout.addWidget(right, 1)
        self.body_layout.addWidget(holder)

    def _build_actions(self) -> None:
        self._run_button = self.add_footer_button("اجرا", lambda: self.run_workflow(), primary=True)
        self._save_button = self.add_footer_button("ذخیره", self._save_current)
        self._pick_button = self.add_footer_button("انتخاب فایل‌ها…", self._pick_files)
        self._footer.addStretch(1)
        self._close_footer = self.add_footer_button("بستن", self.close_panel)

    def _pick_files(self) -> None:
        files, _filter = QFileDialog.getOpenFileNames(self, "انتخاب فایل" if self.lang != "en" else "Pick files")
        if files:
            self.set_paths(files)
            if self._workflow is not None:
                self._rechain()
            self.toastRequested.emit(f"{len(files)} " + ("فایل انتخاب شد" if self.lang != "en" else "file(s) picked"),
                                     "info")
            self._refresh()

    @staticmethod
    def _search_tools(query: str, limit: int = 6):
        try:
            return [spec for _score, spec in nlsearch.search(query, limit=limit)]
        except Exception:
            return []

    # ------------------------------------------------------------------- i18n
    def retranslate(self) -> None:
        fa = self.lang != "en"
        self._title.setText("گردش‌کارها" if fa else "Workflows")
        self._subtitle.setText(
            "ابزارها را پشت سر هم بچینید؛ خروجی هر مرحله خودکار به مرحله‌ی بعد می‌رود."
            if fa else
            "Chain tools together — each step feeds its output into the next one.")
        self._new_button.setText("＋ گردش‌کار تازه" if fa else "＋ New workflow")
        self._dup_button.setText("تکرار" if fa else "Duplicate")
        self._delete_button.setText("حذف" if fa else "Delete")
        self._add_button.setText("افزودن" if fa else "Add")
        if hasattr(self, "_run_button"):
            self._run_button.setText("اجرا" if fa else "Run")
            self._save_button.setText("ذخیره" if fa else "Save")
            self._pick_button.setText("انتخاب فایل‌ها…" if fa else "Pick files…")
            self._close_footer.setText("بستن" if fa else "Close")
        self._search_input.setPlaceholderText("جست‌وجوی ابزار…" if fa else "Search tools…")
        self._name_input.setPlaceholderText("نام گردش‌کار" if fa else "Workflow name")

    # ------------------------------------------------------------------- data
    def set_paths(self, paths: List[str]) -> None:
        self._paths = [str(path) for path in paths if str(path)]

    def new_workflow(self) -> None:
        self._workflow = store.new("گردش‌کار تازه" if self.lang != "en" else "New workflow")
        self._refresh()

    def duplicate_workflow(self) -> None:
        if self._workflow is None:
            return
        copy_ = self._workflow.clone(name=self._workflow.name + " (copy)")
        store.save(copy_)
        self._workflow = copy_
        self._refresh()

    def delete_workflow(self) -> None:
        if self._workflow is None or self._workflow.source == "builtin":
            self.toastRequested.emit("گردش‌کارهای آماده حذف نمی‌شوند؛ می‌توانید تکرارشان کنید."
                                     if self.lang != "en" else "Built-ins cannot be deleted — duplicate them.",
                                     "warning")
            return
        store.delete(self._workflow.id)
        self._workflow = None
        self._refresh()

    def select(self, workflow_id: str) -> None:
        workflow = store.get(workflow_id)
        if workflow is not None:
            self._workflow = workflow
            self._refresh()

    def _save_current(self) -> None:
        if self._workflow is None:
            return
        name = self._name_input.text().strip()
        if name:
            self._workflow.name = name
        if self._workflow.source == "builtin":
            self._workflow = self._workflow.clone(name=(name or self._workflow.name))
        store.save(self._workflow)
        self.toastRequested.emit("ذخیره شد ✓" if self.lang != "en" else "Saved ✓", "success")
        self._refresh()

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for workflow in store.all():
            star = "★ " if workflow.source == "builtin" else ""
            button = QPushButton()
            button.setObjectName("Ghost")
            metrics = QFontMetrics(button.font())
            button.setText(star + metrics.elidedText(
                workflow.name_for(self.lang), Qt.TextElideMode.ElideRight, 150))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(" → ".join(workflow.step_titles(self.lang)))
            selected = self._workflow is not None and workflow.id == self._workflow.id
            if selected:
                button.setStyleSheet(f"border:1px solid {self.palette_ref.accent};")
            button.clicked.connect(lambda _checked=False, wid=workflow.id: self.select(wid))
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self._list.addWidget(button)
        self._list.addStretch(1)
        QTimer.singleShot(0, lambda: self.fit_body_height())
        self._render_steps()
        self._refresh_picker()

    def _render_steps(self) -> None:
        while self._steps.count():
            item = self._steps.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._step_rows = []
        if self._workflow is None:
            self._steps.addWidget(label("یک گردش‌کار از فهرست انتخاب کنید یا تازه بسازید."
                                        if self.lang != "en" else "Pick a workflow or create one.",
                                        "Hint", wrap=True))
            return
        self._name_input.setText(self._workflow.name_for(self.lang))
        if not self._workflow.steps:
            self._steps.addWidget(label("هنوز مرحله‌ای ندارد — از پایین ابزار اضافه کنید."
                                        if self.lang != "en" else "No steps yet — add tools below.",
                                        "Hint"))
            return
        for index, step in enumerate(self._workflow.steps):
            row = StepRow(index, step, self.lang, self.palette_ref)
            row.moved.connect(self._move_step)
            row.removed.connect(self._remove_step)
            self._steps.addWidget(row)
            self._step_rows.append(row)
        self._steps.addStretch(1)
        problems = missing_required(self._workflow)
        if problems:
            self._steps.addWidget(label(("مقادیر لازم: " if self.lang != "en" else "Missing: ")
                                        + " · ".join(problems[:4]), "Hint", wrap=True))

    def _refresh_picker(self) -> None:
        while self._picker.count():
            item = self._picker.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        query = self._search_input.text().strip()
        if query:
            matches = self._search_tools(query, limit=6)
        else:
            matches = [spec for cat in ("pdf", "image", "files", "media", "convert") for spec in tools_in(cat)[:2]]
        for spec in matches[:8]:
            button = QPushButton(spec.name_for(self.lang))
            button.setObjectName("Ghost")
            button.setStyleSheet("text-align:right;" if self.lang != "en" else "text-align:left;")
            button.clicked.connect(lambda _checked=False, tool_id=spec.id: self.add_step(tool_id))
            self._picker.addWidget(button)
        self._picker.addStretch(1)

    def add_first_match(self) -> None:
        query = self._search_input.text().strip()
        if not query:
            return
        matches = self._search_tools(query, limit=1)
        if matches:
            self.add_step(matches[0].id)

    def add_step(self, tool_id: str) -> None:
        if self._workflow is None:
            self._workflow = store.new("گردش‌کار تازه" if self.lang != "en" else "New workflow")
        spec = get_tool(tool_id)
        if spec is None:
            return
        if self._workflow.source == "builtin":
            self._workflow = self._workflow.clone(name=self._workflow.name)
        values = autofill(spec, []) if not self._workflow.steps else chain_values(spec)
        self._workflow.steps.append(Step(tool_id=tool_id, values=values))
        store.save(self._workflow)
        self._refresh()

    def _move_step(self, index: int, target: int) -> None:
        if self._workflow is None:
            return
        steps = self._workflow.steps
        if not (0 <= index < len(steps)) or not (0 <= target < len(steps)) or index == target:
            return
        steps.insert(target, steps.pop(index))
        self._rechain()
        store.save(self._workflow)
        self._refresh()

    def _remove_step(self, index: int) -> None:
        if self._workflow is None:
            return
        if 0 <= index < len(self._workflow.steps):
            self._workflow.steps.pop(index)
            self._rechain()
            store.save(self._workflow)
            self._refresh()

    def _rechain(self) -> None:
        """Keep step 1 bound to picked files and the rest to the previous output."""
        if self._workflow is None:
            return
        for index, step in enumerate(self._workflow.steps):
            spec = get_tool(step.tool_id)
            if spec is None:
                continue
            key, takes_list = input_field(spec)
            if not key:
                continue
            if index == 0:
                step.values[key] = list(self._paths) if takes_list else (self._paths[0] if self._paths else "")
            else:
                step.values[key] = ["{prev_all}"] if takes_list else "{prev}"

    # --------------------------------------------------------------------- run
    def run_workflow(self, workflow: Optional[Workflow] = None) -> None:
        target = workflow or self._workflow
        if target is None:
            self.toastRequested.emit("اول یک گردش‌کار انتخاب کنید." if self.lang != "en"
                                     else "Select a workflow first.", "warning")
            return
        if not target.steps:
            self.toastRequested.emit("گردش‌کار خالی است." if self.lang != "en"
                                     else "The workflow is empty.", "warning")
            return
        if self._workflow is not None and target.id == self._workflow.id:
            self._rechain()
        problems = missing_required(target)
        if problems:
            self.toastRequested.emit(("این مقادیر لازم است: " if self.lang != "en" else "Required: ")
                                     + " · ".join(problems[:3]), "error")
            return
        if self._runner is not None and self._runner.running:
            self.toastRequested.emit("در حال اجراست…" if self.lang != "en" else "Already running…",
                                     "warning")
            return
        runner = WorkflowRunner(target, self.lang, settings.output_dir, {"__paths__": self._paths}, self)
        runner.stepStarted.connect(self._on_step_started)
        runner.stepFinished.connect(self._on_step_finished)
        runner.finished.connect(self._on_finished)
        self._runner = runner
        for row in self._step_rows:
            row.set_status("")
        runner.start()
        self.toastRequested.emit("گردش‌کار شروع شد…" if self.lang != "en" else "Workflow started…", "info")

    def _on_step_started(self, index: int, _tool_id: str, _title: str) -> None:
        if 0 <= index < len(self._step_rows):
            self._step_rows[index].set_status("running")

    def _on_step_finished(self, index: int, status: str, _message: str) -> None:
        if 0 <= index < len(self._step_rows):
            self._step_rows[index].set_status(status)

    def _on_finished(self, result: WorkflowResult) -> None:
        self._runner = None
        self.runFinished.emit(result)
        if result.ok:
            self.toastRequested.emit(("گردش‌کار کامل شد ✓ " if self.lang != "en" else "Finished ✓ ")
                                     + result.message, "success")
        elif result.status == "cancelled":
            self.toastRequested.emit("لغو شد" if self.lang != "en" else "Cancelled", "warning")
        else:
            self.toastRequested.emit(("خطا: " if self.lang != "en" else "Failed: ")
                                     + (result.message or ""), "error")

    def close_panel(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._runner = None
        super().close_panel()
