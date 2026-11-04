"""Assistant planner sheet: one sentence → a ready multi-step plan."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import planner
from ..core.registry import category as get_category
from ..core.registry import get as get_tool
from ..core.settings import settings
from ..core.theme import Palette
from ..core.workflows import Step, Workflow, WorkflowResult, WorkflowRunner, missing_required, store
from .overlay import OverlaySheet
from .widgets import divider, label, pixmap_icon

from PySide6.QtGui import QColor, QFontMetrics


class StepCard(QFrame):
    """One line of the plan with its live status."""

    def __init__(self, index: int, step: Step, lang: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QLabel

        self.step = step
        self.lang = lang
        self.setObjectName("PlanStep")
        self.setStyleSheet(
            f"#PlanStep{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:12px;}}")
        self.setMinimumHeight(58)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        badge = label(str(index), "Hint")
        badge.setFixedWidth(18)
        row.addWidget(badge)
        spec = get_tool(step.tool_id)
        if spec is not None:
            category = get_category(spec.category)
            icon = QLabel()
            icon.setFixedSize(30, 30)
            icon.setPixmap(pixmap_icon(spec.icon, QColor(category.color), 24))
            row.addWidget(icon)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        title = label(spec.name_for(lang) if spec else step.tool_id, "RowTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(title)
        subtitle = spec.desc_for(lang) if spec else ""
        if index > 1:
            subtitle = ("↳ نتیجه‌ی مرحله‌ی قبل" if lang != "en" else "↳ takes the previous output")
        subtitle_label = label(subtitle[:110], "FieldHelp")
        subtitle_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(subtitle_label)
        row.addLayout(text_box, 1)
        self._status = label("", "Hint")
        self._status.setStyleSheet(f"color:{palette.text_dim};")
        row.addWidget(self._status)

    def set_status(self, status: str, message: str = "") -> None:
        table = {
            "running": ("در حال اجرا…", "running…", self.palette_color("warning") if False else ""),
            "done": ("انجام شد ✓", "done ✓", ""),
            "failed": ("خطا ✗", "failed ✗", ""),
            "cancelled": ("لغو شد", "cancelled", ""),
        }
        fa, en, _ = table.get(status, ("", "", ""))
        self._status.setText(en if self.lang == "en" else fa)
        colour = {"done": "#2BD9A0", "failed": "#FF5A6E", "cancelled": "#FFC24B",
                  "running": "#18C8F5"}.get(status, "")
        if colour:
            self._status.setStyleSheet(f"color:{colour};")
        if message:
            self.setToolTip(message)

    def palette_color(self, name: str) -> str:  # pragma: no cover - kept tiny
        return ""


class PlanPanel(OverlaySheet):
    """Type a sentence, get a chain of tools, run it."""

    toastRequested = Signal(str, str)
    workflowSaved = Signal(str)
    runFinished = Signal(object)

    card_width = 700
    body_max_height = 520

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._paths: List[str] = []
        self._workflow: Optional[Workflow] = None
        self._runner: Optional[WorkflowRunner] = None
        self._cards: List[StepCard] = []
        self._build_top()
        self._build_footer()
        self.retranslate()

    # ------------------------------------------------------------------ build
    def _build_top(self) -> None:
        self._input = QLineEdit()
        self._input.setMinimumHeight(40)
        self._input.returnPressed.connect(self.make_plan)
        self._card_layout.insertWidget(2, self._input)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self._pick_button = QPushButton()
        self._pick_button.setObjectName("Ghost")
        self._pick_button.clicked.connect(self._pick_files)
        row_layout.addWidget(self._pick_button)
        self._files_label = label("", "Hint")
        self._files_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row_layout.addWidget(self._files_label, 1)
        self._make_button = QPushButton()
        self._make_button.setObjectName("Primary")
        self._make_button.clicked.connect(self.make_plan)
        row_layout.addWidget(self._make_button)
        self._card_layout.insertWidget(3, row)

    def _build_footer(self) -> None:
        self._run_button = self.add_footer_button("اجرا", self.run_plan, primary=True)
        self._save_button = self.add_footer_button("ذخیره به گردش‌کارها", self.save_plan)
        self._footer.addStretch(1)
        self._close_footer = self.add_footer_button("بستن", self.close_panel)

    # ------------------------------------------------------------------- i18n
    def retranslate(self) -> None:
        fa = self.lang != "en"
        self._title.setText("دستیار برنامه‌ریز" if fa else "Planner assistant")
        self._subtitle.setText(
            "یک جمله بنویسید؛ وین‌جنیوس چند ابزار را پشت سر هم می‌چیند و یک‌جا اجرا می‌کند."
            if fa else
            "Describe the outcome; WinGenius chains the right tools and runs them in one go.")
        self._input.setPlaceholderText(
            "مثلاً: این عکس‌ها را کم‌حجم کن و PDF بساز" if fa
            else "e.g. compress these photos and make a PDF")
        self._pick_button.setText("انتخاب فایل‌ها…" if fa else "Pick files…")
        self._make_button.setText("ساخت پلن" if fa else "Build plan")
        self._files_label.setText(
            ("بدون فایل (صفر)" if fa else "no files") if not self._paths
            else f"{len(self._paths)} " + ("فایل انتخاب شده" if fa else "file(s) selected"))
        if hasattr(self, "_run_button"):
            self._run_button.setText("اجرا" if fa else "Run")
            self._save_button.setText("ذخیره به گردش‌کارها" if fa else "Save as workflow")
            self._close_footer.setText("بستن" if fa else "Close")

    # ------------------------------------------------------------------ files
    def set_paths(self, paths: List[str]) -> None:
        self._paths = [str(path) for path in paths if str(path)]
        self.retranslate()

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "انتخاب فایل" if self.lang != "en" else "Pick files")
        if files:
            self._paths = files
            self.retranslate()
            if self._input.text().strip():
                self.make_plan()

    # ------------------------------------------------------------------ build
    def make_plan(self) -> None:
        text = self._input.text().strip()
        if not text:
            self.toastRequested.emit("یک جمله بنویسید، مثلاً: «عکس‌ها را PDF کن»"
                                     if self.lang != "en" else "Type a sentence first", "warning")
            return
        workflow = planner.build_plan(text, self._paths, self.lang)
        if workflow is None:
            self.toastRequested.emit(
                "برای این جمله پلنی پیدا نشد — از پالت (Ctrl+K) ابزار را جست‌وجو کنید."
                if self.lang != "en" else
                "No plan matched — try searching the palette (Ctrl+K).", "warning")
            return
        self._workflow = workflow
        self._render_plan(workflow)

    def _render_plan(self, workflow: Workflow) -> None:
        self.clear_body()
        self._cards = []
        self.body_layout.addWidget(label(
            f"{workflow.name_for(self.lang)} · {len(workflow.steps)} " +
            ("مرحله" if self.lang != "en" else "steps"), "SectionTitle"))
        for index, step in enumerate(workflow.steps, start=1):
            card = StepCard(index, step, self.lang, self.palette_ref)
            self._cards.append(card)
            self.body_layout.addWidget(card)
        problems = missing_required(workflow)
        if problems:
            self.body_layout.addWidget(divider())
            self.body_layout.addWidget(label(
                ("مقادیر لازم: " if self.lang != "en" else "Missing: ") + " · ".join(problems[:6]),
                "Hint", wrap=True))
        else:
            self.body_layout.addWidget(label(
                "همه‌ی مقادیر لازم آماده است ✓" if self.lang != "en"
                else "All required values are set ✓", "Hint"))
        self._add_chips()
        self.fit_body_height()

    def _add_chips(self) -> None:
        chips = QWidget()
        layout = QHBoxLayout(chips)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        for text, _intent in planner.suggestions(self.lang, limit=3):
            chip = QPushButton()
            chip.setObjectName("Ghost")
            chip.setMinimumHeight(36)
            chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(text)
            metrics = QFontMetrics(chip.font())
            chip.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, 172))
            chip.clicked.connect(lambda _checked=False, value=text: self._use_example(value))
            layout.addWidget(chip)
        layout.addStretch(1)
        self.body_layout.addWidget(chips)

    def _use_example(self, text: str) -> None:
        self._input.setText(text)
        self.make_plan()

    def _refresh(self) -> None:
        if self._workflow is None:
            self.clear_body()
            self.body_layout.addWidget(label(
                "مثال‌ها:" if self.lang != "en" else "Examples:", "SectionTitle"))
            for text, _intent in planner.suggestions(self.lang, limit=6):
                button = QPushButton(text)
                button.setObjectName("Ghost")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, value=text: self._use_example(value))
                self.body_layout.addWidget(button)
            self.body_layout.addStretch(1)
        else:
            self._render_plan(self._workflow)

    # -------------------------------------------------------------------- run
    def run_plan(self) -> None:
        if self._workflow is None:
            self.make_plan()
            if self._workflow is None:
                return
        if self._runner is not None and self._runner.running:
            self.toastRequested.emit("پلن در حال اجراست…" if self.lang != "en" else "Already running…",
                                     "warning")
            return
        problems = missing_required(self._workflow)
        if problems:
            self.toastRequested.emit(
                ("این مقادیر لازم است: " if self.lang != "en" else "Required: ") + " · ".join(problems[:3]),
                "error")
            return
        from ..core.execution import ExecutionPolicy, OperationRisk, classify_steps

        highest_risk, flagged = classify_steps([(step.tool_id, step.values) for step in self._workflow.steps])
        if settings.get("ui/confirm_danger") and highest_risk >= OperationRisk.CONFIRM and flagged:
            from PySide6.QtWidgets import QMessageBox

            tier = highest_risk.label_for(self.lang)
            reason = " · ".join(f"{a.tool_id}: {a.reason_for(self.lang)}" for a in flagged[:3])
            prompt = (f"این پلن شامل عملیات حساس است:\n[{tier}] {reason}\n\nآیا اجرا شود؟"
                      if self.lang != "en"
                      else f"This plan includes sensitive operations:\n[{tier}] {reason}\n\nRun anyway?")
            answer = QMessageBox.question(
                self, "تأیید اجرا" if self.lang != "en" else "Confirm execution", prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        runner = WorkflowRunner(
            self._workflow, self.lang, settings.output_dir,
            {"__paths__": self._paths}, self,
            policy=ExecutionPolicy.for_gui(confirmed=True, record_history=True),
        )
        runner.stepStarted.connect(self._on_step_started)
        runner.stepFinished.connect(self._on_step_finished)
        runner.finished.connect(self._on_finished)
        self._retiring_runners = getattr(self, "_retiring_runners", [])
        self._retiring_runners.append(runner)
        runner.finished.connect(lambda _res, r=runner: self._release_runner(r))
        self._runner = runner
        for card in self._cards:
            card.set_status("")
        runner.start()
        self.toastRequested.emit("پلن شروع شد…" if self.lang != "en" else "Plan started…", "info")

    def _on_step_started(self, index: int, _tool_id: str, _title: str) -> None:
        if 0 <= index < len(self._cards):
            self._cards[index].set_status("running")

    def _on_step_finished(self, index: int, status: str, message: str) -> None:
        if 0 <= index < len(self._cards):
            self._cards[index].set_status(status, message)

    def _on_finished(self, result: WorkflowResult) -> None:
        self._runner = None
        self.runFinished.emit(result)
        if result.ok:
            self.toastRequested.emit(
                ("پلن کامل شد ✓ " + result.message) if self.lang != "en"
                else ("Plan finished ✓ " + result.message), "success")
        elif result.status == "cancelled":
            self.toastRequested.emit("لغو شد" if self.lang != "en" else "Cancelled", "warning")
        else:
            self.toastRequested.emit(
                ("خطا: " if self.lang != "en" else "Failed: ") + (result.message or ""), "error")

    # -------------------------------------------------------------------- save
    def save_plan(self) -> None:
        if self._workflow is None:
            self.make_plan()
            if self._workflow is None:
                return
        workflow = self._workflow.clone(name=self._workflow.name)
        workflow.source = "user"
        store.save(workflow)
        self.workflowSaved.emit(workflow.id)
        self.toastRequested.emit(
            "به گردش‌کارها اضافه شد ✓" if self.lang != "en" else "Saved to workflows ✓", "success")

    def _release_runner(self, runner: WorkflowRunner) -> None:
        pool = getattr(self, "_retiring_runners", None)
        if isinstance(pool, list) and runner in pool:
            pool.remove(runner)
        runner.deleteLater()

    def cancel_run(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

    def close_panel(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self._runner = None
        super().close_panel()

    def shutdown(self, timeout_ms: int = 2000) -> None:
        runners = list(getattr(self, "_retiring_runners", []) or [])
        if self._runner is not None and self._runner not in runners:
            runners.append(self._runner)
        for runner in runners:
            try:
                runner.shutdown(timeout_ms)
            except Exception:
                pass
        self._runner = None
