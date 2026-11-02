"""Scheduler sheet — cron-style automation, the agenda and the macro library."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core import macro as macro_module
from ..core.registry import all_tools
from ..core.scheduler import (
    PRESETS, Schedule, describe_cron, parse_cron, store, upcoming,
)
from ..core.theme import Palette
from ..core.workflows import store as workflow_store
from .overlay import OverlaySheet
from .widgets import divider, label


class ScheduleCard(QFrame):
    """One schedule with its own action buttons."""

    runNow = Signal(str)
    toggle = Signal(str)
    remove = Signal(str)

    def __init__(self, item: Schedule, lang: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.lang = lang
        self.setObjectName("ScheduleCard")
        self.setStyleSheet(
            f"#ScheduleCard{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:12px;}}")
        self.setMinimumHeight(96)
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        dot = label("●", "Hint")
        dot.setStyleSheet(f"color:{palette.success if item.enabled else palette.text_dim};")
        head.addWidget(dot)
        title = label(item.title(lang), "RowTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(title, 1)
        head.addWidget(label("فعال" if item.enabled else "خاموش" if lang != "en"
                             else ("on" if item.enabled else "off"), "Hint"))
        box.addLayout(head)

        when = label(f"{item.cron} · {item.when(lang)}", "FieldHelp")
        when.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(when)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        nxt = item.next_at()
        info = ((f"بعدی: {nxt.strftime('%m-%d %H:%M')}" if nxt else "بعدی: —") if lang != "en"
                else (f"next {nxt.strftime('%m-%d %H:%M')}" if nxt else "next —"))
        if item.runs:
            info += (f" · {item.runs} اجرا" if lang != "en" else f" · {item.runs} runs")
            if item.last_status:
                info += f" ({item.last_status})"
        info_label = label(info, "Hint")
        info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        foot.addWidget(info_label, 1)
        for text, slot in ((("اجرا کن" if lang != "en" else "Run"), lambda: self.runNow.emit(self.item.id)),
                           (("خاموش" if item.enabled else "روشن") if lang != "en"
                            else ("off" if item.enabled else "on"), lambda: self.toggle.emit(self.item.id)),
                           (("حذف" if lang != "en" else "Delete"), lambda: self.remove.emit(self.item.id))):
            button = QPushButton(text)
            button.setObjectName("Ghost")
            button.clicked.connect(slot)
            foot.addWidget(button)
        box.addLayout(foot)


class SchedulerPanel(OverlaySheet):
    """Create cron schedules, watch the agenda, run macros."""

    toastRequested = Signal(str, str)
    scheduleTriggered = Signal(object, str)      # (schedule, reason) → shell queues a job
    macroRunRequested = Signal(str, bool)        # (macro file path, dry_run)

    card_width = 860
    body_max_height = 560

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: List[ScheduleCard] = []
        self._editing: Optional[Schedule] = None
        self._build_actions()
        self.retranslate()

    # ----------------------------------------------------------------- building
    def _build_form(self) -> QFrame:
        form = QFrame()
        form.setObjectName("FormCard")
        layout = QGridLayout(form)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self._name_input = QLineEdit()
        self._name_input.setMinimumHeight(34)
        self._name_input.setPlaceholderText("مثلاً هر ۱۵ دقیقه" if self.lang != "en"
                                            else "e.g. every 15 minutes")
        layout.addWidget(label("نام زمان‌بندی" if self.lang != "en" else "Schedule name", "Hint"),
                         0, 0)
        layout.addWidget(self._name_input, 1, 0, 1, 2)

        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumHeight(34)
        self._preset_combo.currentIndexChanged.connect(self._on_preset)
        layout.addWidget(label("تکرار" if self.lang != "en" else "Repeat", "Hint"), 0, 2)
        layout.addWidget(self._preset_combo, 1, 2)

        self._cron_input = QLineEdit("0 9 * * *")
        self._cron_input.setMinimumHeight(34)
        self._cron_input.textChanged.connect(self._update_cron_hint)
        layout.addWidget(label("عبارت cron" if self.lang != "en" else "cron expression", "Hint"), 0, 3)
        layout.addWidget(self._cron_input, 1, 3)

        self._kind_combo = QComboBox()
        self._kind_combo.setMinimumHeight(34)
        self._kind_combo.currentIndexChanged.connect(self._refresh_targets)
        layout.addWidget(label("نوع" if self.lang != "en" else "Kind", "Hint"), 2, 0)
        layout.addWidget(self._kind_combo, 3, 0)

        self._target_combo = QComboBox()
        self._target_combo.setMinimumHeight(34)
        self._target_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(label("هدف" if self.lang != "en" else "Target", "Hint"), 2, 1)
        layout.addWidget(self._target_combo, 3, 1, 1, 2)

        self._catchup_check = QCheckBox("جبران اجرای ازدست‌رفته" if self.lang != "en"
                                        else "Catch up missed runs")
        self._catchup_check.setChecked(True)
        layout.addWidget(self._catchup_check, 3, 3)

        self._cron_hint = label("", "Hint", wrap=True)
        self._cron_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._cron_hint, 4, 0, 1, 4)
        self._cron_next = label("", "Hint", wrap=True)
        self._cron_next.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._cron_next, 5, 0, 1, 4)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._save_button = QPushButton("ذخیره" if self.lang != "en" else "Save")
        self._save_button.setObjectName("Primary")
        self._save_button.setMinimumHeight(34)
        self._save_button.clicked.connect(self.save_schedule)
        buttons.addWidget(self._save_button)
        self._reset_button = QPushButton("فرم تازه" if self.lang != "en" else "New")
        self._reset_button.setObjectName("Ghost")
        self._reset_button.setMinimumHeight(34)
        self._reset_button.clicked.connect(self.reset_form)
        buttons.addWidget(self._reset_button)
        buttons.addStretch(1)
        holder = QWidget()
        holder.setLayout(buttons)
        layout.addWidget(holder, 6, 3)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 3)

        self._form = form
        self._refresh_presets()
        self._refresh_kinds()
        self._refresh_targets()
        self._update_cron_hint()
        return form

    def _build_actions(self) -> None:
        self._run_footer = self.add_footer_button("اجرای زمان‌بندی‌ها" if self.lang != "en" else "Run",
                                                  self.run_selected_now, primary=True)
        self._template_button = self.add_footer_button("قالب ماکرو" if self.lang != "en" else "Macro templates",
                                                       self.save_templates)
        self._agenda_button = self.add_footer_button("نمایش برنامه" if self.lang != "en" else "Agenda",
                                                     self.show_agenda)
        self._footer.addStretch(1)
        self._close_footer = self.add_footer_button("بستن" if self.lang != "en" else "Close",
                                                    self.close_panel)

    # ------------------------------------------------------------------- i18n
    def retranslate(self) -> None:
        fa = self.lang != "en"
        self._title.setText("زمان‌بند و ماکرو" if fa else "Scheduler & macros")
        self._subtitle.setText(
            "کارها را با عبارت cron زمان‌بندی کنید و ماکروهای شرطی بسازید." if fa
            else "Schedule jobs with cron expressions and run conditional macros.")
        for attribute, text_fa, text_en in (("_save_button", "ذخیره", "Save"),
                                            ("_reset_button", "فرم تازه", "New")):
            widget = getattr(self, attribute, None)
            if widget is not None:
                try:
                    widget.setText(text_fa if fa else text_en)
                except RuntimeError:
                    pass
        if hasattr(self, "_run_footer"):
            self._run_footer.setText("اجرا کن" if fa else "Run")
            self._template_button.setText("قالب ماکرو" if fa else "Macro templates")
            self._agenda_button.setText("برنامه" if fa else "Agenda")
            self._close_footer.setText("بستن" if fa else "Close")
        if hasattr(self, "_cron_input"):
            try:
                self._catchup_check.setText("جبران اجرای ازدست‌رفته" if fa else "Catch up missed runs")
                self._refresh_presets()
                self._refresh_kinds()
                self._refresh_targets()
                self._update_cron_hint()
            except RuntimeError:
                pass

    def _refresh_presets(self) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("دلخواه" if self.lang != "en" else "Custom", "")
        for expr, fa, en in PRESETS:
            self._preset_combo.addItem(f"{fa if self.lang != 'en' else en}  ·  {expr}", expr)
        self._preset_combo.blockSignals(False)

    def _refresh_kinds(self) -> None:
        current = self._kind_combo.currentData()
        self._kind_combo.blockSignals(True)
        self._kind_combo.clear()
        self._kind_combo.addItem("گردش‌کار" if self.lang != "en" else "Workflow", "workflow")
        self._kind_combo.addItem("ابزار" if self.lang != "en" else "Tool", "tool")
        self._kind_combo.blockSignals(False)
        if current:
            index = self._kind_combo.findData(current)
            if index >= 0:
                self._kind_combo.setCurrentIndex(index)

    def _refresh_targets(self) -> None:
        kind = self._kind_combo.currentData() or "workflow"
        self._target_combo.clear()
        if kind == "workflow":
            for item in workflow_store.all():
                self._target_combo.addItem(item.name_for(self.lang), item.id)
        else:
            for spec in all_tools():
                label_text = spec.name_for(self.lang)
                self._target_combo.addItem(f"{label_text}  ·  {spec.id}", spec.id)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.retranslate()
        self._refresh()

    # ------------------------------------------------------------------ actions
    def _on_preset(self, index: int) -> None:
        expr = self._preset_combo.itemData(index) or ""
        if expr:
            self._cron_input.setText(expr)

    def _update_cron_hint(self) -> None:
        """Two short lines instead of one long mixed-script string.

        Keeping the meaning and the next-run times on separate labels avoids the
        bidi jumble (and the clipping) you get when Latin clock times sit inside a
        Persian sentence.
        """
        expr = self._cron_input.text().strip()
        fa = self.lang != "en"
        try:
            parse_cron(expr)
        except Exception as exc:
            self._cron_hint.setText(("عبارت نامعتبر: " if fa else "invalid: ") + str(exc))
            self._cron_hint.setStyleSheet("color:#FF5A6E;")
            self._cron_next.setText("")
            return
        self._cron_hint.setStyleSheet("")
        self._cron_hint.setText(describe_cron(expr, self.lang))
        runs = upcoming(expr, 2)
        stamp = " · ".join(moment.strftime("%m-%d %H:%M") for moment in runs)
        self._cron_next.setText((f"بعدی: {stamp}" if fa else f"next: {stamp}") if runs else "")

    def reset_form(self) -> None:
        self._editing = None
        self._name_input.setText("")
        self._cron_input.setText("0 9 * * *")
        self._preset_combo.setCurrentIndex(0)
        self._catchup_check.setChecked(True)

    def save_schedule(self) -> None:
        expr = self._cron_input.text().strip()
        try:
            parse_cron(expr)
        except Exception as exc:
            self.toastRequested.emit(("عبارت cron نامعتبر است: " if self.lang != "en"
                                      else "invalid cron: ") + str(exc), "warning")
            return
        target = self._target_combo.currentData()
        if not target and self._target_combo.count():
            target = self._target_combo.itemData(0)
        if not target:
            self.toastRequested.emit("یک هدف انتخاب کنید." if self.lang != "en" else "Pick a target",
                                     "warning")
            return
        item = self._editing or store.new()
        item.name = self._name_input.text().strip()
        item.cron = expr
        item.kind = self._kind_combo.currentData() or "workflow"
        item.target_id = str(target)
        item.catch_up = self._catchup_check.isChecked()
        item.enabled = True
        store.save(item)
        self._editing = None
        self.toastRequested.emit("زمان‌بندی ذخیره شد." if self.lang != "en" else "Schedule saved", "success")
        self._refresh()

    def toggle_schedule(self, schedule_id: str) -> None:
        item = store.get(schedule_id)
        if item is None:
            return
        item.enabled = not item.enabled
        store.save(item)
        self._refresh()

    def delete_schedule(self, schedule_id: str) -> None:
        store.delete(schedule_id)
        self._refresh()
        self.toastRequested.emit("زمان‌بندی حذف شد." if self.lang != "en" else "Schedule removed", "info")

    def run_schedule_now(self, schedule_id: str) -> None:
        item = store.get(schedule_id)
        if item is None:
            return
        self.scheduleTriggered.emit(item, "manual")
        self.toastRequested.emit(
            ("اجرا شد: " if self.lang != "en" else "queued: ") + item.title(self.lang), "info")

    def run_selected_now(self) -> None:
        first = store.all()
        if not first:
            self.toastRequested.emit("اول یک زمان‌بندی بسازید." if self.lang != "en"
                                     else "Create a schedule first", "warning")
            return
        self.run_schedule_now(first[0].id)

    def show_agenda(self) -> None:
        pairs = []
        for item in store.all():
            if item.enabled:
                for moment in upcoming(item.cron, 2):
                    pairs.append((moment, item))
        pairs.sort(key=lambda pair: pair[0])
        if not pairs:
            self.toastRequested.emit("برنامه‌ای برای نمایش نیست." if self.lang != "en"
                                     else "Nothing scheduled", "info")
            return
        text = " · ".join(f"{moment.strftime('%m-%d %H:%M')} {item.title(self.lang)}"
                          for moment, item in pairs[:4])
        self.toastRequested.emit(text, "info")

    # ------------------------------------------------------------------- macros
    def save_templates(self) -> None:
        written = [macro_module.save_macro(template) for template in macro_module.builtin_macros(self.lang)]
        self.toastRequested.emit(
            (f"{len(written)} قالب ماکرو ذخیره شد." if self.lang != "en"
             else f"{len(written)} macro template(s) saved"), "success")
        self._refresh()

    def run_macro_file(self, path: Path, dry_run: bool = False) -> None:
        try:
            macro_module.load_macro(path)          # validates the file before queuing
        except macro_module.MacroError as exc:
            self.toastRequested.emit(str(exc), "error")
            return
        self.macroRunRequested.emit(str(path), dry_run)

    # ------------------------------------------------------------------ refresh
    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()

    def _refresh(self) -> None:
        self.clear_body()
        self._cards = []
        self._editing = None
        self.body_layout.addWidget(self._build_form())
        self.body_layout.addWidget(label("زمان‌بندی‌ها" if self.lang != "en" else "Schedules",
                                         "SectionTitle"))
        self._list_holder = QWidget()
        self._list_layout = QVBoxLayout(self._list_holder)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self.body_layout.addWidget(self._list_holder)

        items = store.all()
        if not items:
            self._list_layout.addWidget(label(
                "هنوز زمان‌بندی‌ای نساخته‌اید. مثلاً «هر روز ۹ صبح گردش‌کار PDF → عکس → ZIP را اجرا کن»."
                if self.lang != "en" else
                "No schedules yet — e.g. run the PDF → images → ZIP workflow every day at 09:00.",
                "Hint", wrap=True))
        for item in items:
            card = ScheduleCard(item, self.lang, self.palette_ref)
            card.runNow.connect(self.run_schedule_now)
            card.toggle.connect(self.toggle_schedule)
            card.remove.connect(self.delete_schedule)
            self._cards.append(card)
            self._list_layout.addWidget(card)

        self.body_layout.addWidget(divider())
        self.body_layout.addWidget(label("کتابخانه‌ی ماکرو" if self.lang != "en" else "Macro library",
                                         "SectionTitle"))
        self._macro_holder = QWidget()
        self._macro_layout = QVBoxLayout(self._macro_holder)
        self._macro_layout.setContentsMargins(0, 0, 0, 0)
        self._macro_layout.setSpacing(6)
        self.body_layout.addWidget(self._macro_holder)

        files = macro_module.library()
        if not files:
            self._macro_layout.addWidget(label(
                "قالب‌های آماده را با دکمه‌ی «قالب ماکرو» اضافه کنید." if self.lang != "en"
                else "Add the ready-made templates with the “Macro templates” button.", "Hint", wrap=True))
        seen_names = set()
        shown = []
        for path in files:
            try:
                key = str(macro_module.load_macro(path).get("name") or path.stem)
            except macro_module.MacroError:
                key = path.stem
            if key in seen_names:
                continue
            seen_names.add(key)
            shown.append(path)
        for path in shown[:6]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            try:
                macro_name = str(macro_module.load_macro(path).get("name") or path.stem)
            except macro_module.MacroError:
                macro_name = path.stem
            name = label(macro_name, "RowTitle")
            name.setToolTip(path.name)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(name, 1)
            run_button = QPushButton("اجرا" if self.lang != "en" else "Run")
            run_button.setObjectName("Ghost")
            run_button.clicked.connect(lambda _c=False, p=path: self.run_macro_file(p, False))
            row_layout.addWidget(run_button)
            dry_button = QPushButton("تمرینی" if self.lang != "en" else "Dry run")
            dry_button.setObjectName("Ghost")
            dry_button.clicked.connect(lambda _c=False, p=path: self.run_macro_file(p, True))
            row_layout.addWidget(dry_button)
            self._macro_layout.addWidget(row)

        self.body_layout.addStretch(1)
        self.fit_body_height()
        QTimer.singleShot(0, self.fit_body_height)
