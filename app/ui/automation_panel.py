"""Automation sheet: watch a folder and let WinGenius do the work by itself."""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..core.theme import Palette
from ..core.utils import human_duration
from ..core.watcher import WatchRule, mark_run, store
from ..core.workflows import store as workflow_store
from .overlay import OverlaySheet
from .widgets import divider, label


class RuleCard(QFrame):
    """One automation rule with inline controls."""

    runNow = Signal(str)
    toggle = Signal(str)
    remove = Signal(str)
    edit = Signal(str)

    def __init__(self, rule: WatchRule, lang: str, palette: Palette, parent=None) -> None:
        super().__init__(parent)
        self.rule = rule
        self.lang = lang
        self.setObjectName("RuleCard")
        self.setStyleSheet(
            f"#RuleCard{{background:{palette.panel_alt};border:1px solid {palette.border};"
            f"border-radius:12px;}}")
        self.setMinimumHeight(88)
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 8)
        box.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        dot = label("●", "Hint")
        dot.setStyleSheet(f"color:{palette.success if rule.enabled else palette.text_dim};")
        head.addWidget(dot)
        title = label(rule.title(lang), "RowTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(title, 1)
        state = label(("فعال" if rule.enabled else "خاموش") if lang != "en"
                      else ("on" if rule.enabled else "off"), "Hint")
        head.addWidget(state)
        box.addLayout(head)
        summary = label(rule.summary(lang), "FieldHelp")
        summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(summary)
        info = ""
        if rule.runs:
            info = (f"{rule.runs} اجرا" if lang != "en" else f"{rule.runs} runs")
            if rule.last_run:
                ago = human_duration(time.time() - rule.last_run)
                info += (" · آخرین: " if lang != "en" else " · last: ") + ago
                if rule.last_status:
                    info += f" ({rule.last_status})"
        else:
            info = ("هنوز اجرا نشده" if lang != "en" else "never ran")
        foot = QHBoxLayout()
        foot.setSpacing(6)
        info_label = label(info, "Hint")
        info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        foot.addWidget(info_label, 1)
        run_button = QPushButton("اجرا کن" if lang != "en" else "Run now")
        run_button.setObjectName("Ghost")
        run_button.clicked.connect(lambda: self.runNow.emit(self.rule.id))
        foot.addWidget(run_button)
        toggle_button = QPushButton("خاموش" if (lang != "en" and rule.enabled)
                                    else ("روشن" if lang != "en" else ("off" if rule.enabled else "on")))
        toggle_button.setObjectName("Ghost")
        toggle_button.clicked.connect(lambda: self.toggle.emit(self.rule.id))
        foot.addWidget(toggle_button)
        delete_button = QPushButton("حذف" if lang != "en" else "Delete")
        delete_button.setObjectName("Ghost")
        delete_button.clicked.connect(lambda: self.remove.emit(self.rule.id))
        foot.addWidget(delete_button)
        box.addLayout(foot)


class AutomationPanel(OverlaySheet):
    """Create folder rules and see what the watcher has been doing."""

    toastRequested = Signal(str, str)
    ruleTriggered = Signal(object, str)      # forwarded to the shell so it can queue a job

    card_width = 720
    body_max_height = 560

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.watcher = None
        self._editing: Optional[WatchRule] = None
        self._build_form()
        self._build_actions()
        self.retranslate()

    # ------------------------------------------------------------------ build
    def _build_form(self) -> None:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("نام قانون")
        row1.addWidget(self._name_input, 1)
        self._folder_input = QLineEdit()
        self._folder_input.setPlaceholderText("پوشه‌ی دیده‌بان")
        row1.addWidget(self._folder_input, 1)
        self._browse_button = QPushButton("…")
        self._browse_button.setObjectName("Ghost")
        self._browse_button.setFixedWidth(34)
        self._browse_button.clicked.connect(self._pick_folder)
        row1.addWidget(self._browse_button)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._pattern_input = QLineEdit("*.pdf")
        self._pattern_input.setPlaceholderText("*.pdf, *.png")
        row2.addWidget(self._pattern_input, 1)
        self._action_combo = QComboBox()
        self._action_combo.addItem("گردش‌کار", "workflow")
        self._action_combo.addItem("یک ابزار", "tool")
        self._action_combo.currentIndexChanged.connect(self._refresh_targets)
        row2.addWidget(self._action_combo)
        self._target_combo = QComboBox()
        row2.addWidget(self._target_combo, 1)
        self._settle_spin = QSpinBox()
        self._settle_spin.setRange(0, 120)
        self._settle_spin.setValue(2)
        self._settle_spin.setSuffix("s")
        row2.addWidget(self._settle_spin)
        self._recursive_check = QCheckBox("زیرپوشه‌ها")
        row2.addWidget(self._recursive_check)
        self._enabled_check = QCheckBox("فعال")
        self._enabled_check.setChecked(True)
        row2.addWidget(self._enabled_check)
        layout.addLayout(row2)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._save_button = QPushButton("ذخیره‌ی قانون")
        self._save_button.setObjectName("Primary")
        self._save_button.clicked.connect(self.save_rule)
        buttons.addWidget(self._save_button)
        self._clear_button = QPushButton("فرم تازه")
        self._clear_button.setObjectName("Ghost")
        self._clear_button.clicked.connect(self.reset_form)
        buttons.addWidget(self._clear_button)
        buttons.addStretch(1)
        self._status_label = label("", "Hint")
        buttons.addWidget(self._status_label)
        layout.addLayout(buttons)

        self._card_layout.insertWidget(2, form)
        # kept as an attribute so retranslate() can update it (audit F-15)
        self._rules_title = label("قانون‌های فعال", "SectionTitle")
        self.body_layout.addWidget(self._rules_title)

    def _build_actions(self) -> None:
        self._scan_button = self.add_footer_button("بررسی فوری پوشه‌ها", self.scan_now)
        self._footer.addStretch(1)
        self._close_footer = self.add_footer_button("بستن", self.close_panel)

    # ------------------------------------------------------------------- i18n
    def retranslate(self) -> None:
        fa = self.lang != "en"
        self._title.setText("خودکارسازی پوشه" if fa else "Folder automation")
        self._subtitle.setText(
            "یک پوشه را زیر نظر بگیرید: هر فایل تازه‌ای که اضافه شود، ابزار یا گردش‌کار دلخواه شما خودکار اجرا می‌شود."
            if fa else
            "Watch a folder: whenever a new file appears, your chosen tool or workflow runs automatically.")
        self._name_input.setPlaceholderText("نام قانون" if fa else "Rule name")
        self._folder_input.setPlaceholderText("پوشه‌ی دیده‌بان" if fa else "Watched folder")
        self._save_button.setText("ذخیره‌ی قانون" if fa else "Save rule")
        self._clear_button.setText("فرم تازه" if fa else "Reset form")
        self._scan_button.setText("بررسی فوری پوشه‌ها" if fa else "Scan now")
        self._close_footer.setText("بستن" if fa else "Close")
        if getattr(self, "_rules_title", None) is not None:
            self._rules_title.setText("قانون‌های فعال" if fa else "Active rules")
        self._recursive_check.setText("زیرپوشه‌ها" if fa else "Subfolders")
        self._enabled_check.setText("فعال" if fa else "Enabled")
        if self._action_combo.count() >= 2:
            self._action_combo.setItemText(0, "گردش‌کار" if fa else "Workflow")
            self._action_combo.setItemText(1, "یک ابزار" if fa else "Single tool")

    # ------------------------------------------------------------------ state
    def attach(self, watcher) -> None:
        self.watcher = watcher

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "انتخاب پوشه" if self.lang != "en" else "Pick folder")
        if folder:
            self._folder_input.setText(folder)

    def _refresh_targets(self) -> None:
        self._target_combo.clear()
        if self._action_combo.currentData() == "workflow":
            for workflow in workflow_store.all():
                self._target_combo.addItem(workflow.name_for(self.lang), workflow.id)
        else:
            from ..core.registry import all_tools

            for spec in all_tools():
                self._target_combo.addItem(f"{spec.name_for(self.lang)} · {spec.category}", spec.id)

    def reset_form(self) -> None:
        self._editing = None
        self._name_input.clear()
        self._folder_input.clear()
        self._pattern_input.setText("*.pdf")
        self._action_combo.setCurrentIndex(0)
        self._refresh_targets()
        self._settle_spin.setValue(2)
        self._recursive_check.setChecked(False)
        self._enabled_check.setChecked(True)

    def edit_rule(self, rule_id: str) -> None:
        rule = store.get(rule_id)
        if rule is None:
            return
        self._editing = rule
        self._name_input.setText(rule.name)
        self._folder_input.setText(rule.folder)
        self._pattern_input.setText(rule.pattern)
        index = self._action_combo.findData(rule.action)
        self._action_combo.setCurrentIndex(max(0, index))
        self._refresh_targets()
        target_index = self._target_combo.findData(rule.target_id)
        if target_index >= 0:
            self._target_combo.setCurrentIndex(target_index)
        self._settle_spin.setValue(int(rule.settle))
        self._recursive_check.setChecked(rule.recursive)
        self._enabled_check.setChecked(rule.enabled)

    # ------------------------------------------------------------------- save
    def save_rule(self) -> None:
        folder = self._folder_input.text().strip()
        if not folder:
            self.toastRequested.emit("پوشه را انتخاب کنید." if self.lang != "en" else "Pick a folder.",
                                     "warning")
            return
        if not Path(folder).is_dir():
            self.toastRequested.emit("این پوشه وجود ندارد." if self.lang != "en"
                                     else "That folder does not exist.", "error")
            return
        rule = self._editing or store.new()
        rule.name = self._name_input.text().strip() or Path(folder).name
        rule.folder = folder
        rule.pattern = self._pattern_input.text().strip() or "*.*"
        rule.action = self._action_combo.currentData() or "workflow"
        rule.target_id = self._target_combo.currentData() or ""
        rule.settle = float(self._settle_spin.value())
        rule.recursive = self._recursive_check.isChecked()
        rule.enabled = self._enabled_check.isChecked()
        if not rule.target_id:
            self.toastRequested.emit("یک گردش‌کار یا ابزار انتخاب کنید."
                                     if self.lang != "en" else "Pick a target.", "warning")
            return
        store.save(rule)
        primed = 0
        if self.watcher is not None:
            primed = self.watcher.prime(rule)
            self.watcher.start()
        self._editing = rule
        self.toastRequested.emit(
            (f"قانون ذخیره شد ✓ ({primed} فایل فعلی نادیده گرفته شد)" if self.lang != "en"
             else f"Rule saved ✓ ({primed} existing files ignored)"), "success")
        self._refresh()

    def delete_rule(self, rule_id: str) -> None:
        store.delete(rule_id)
        if self._editing is not None and self._editing.id == rule_id:
            self.reset_form()
        self._refresh()

    def toggle_rule(self, rule_id: str) -> None:
        rule = store.get(rule_id)
        if rule is None:
            return
        rule.enabled = not rule.enabled
        store.save(rule)
        if rule.enabled and self.watcher is not None:
            self.watcher.prime(rule)
            self.watcher.start()
        self._refresh()

    def run_rule_now(self, rule_id: str) -> None:
        rule = store.get(rule_id)
        if rule is None:
            return
        from ..core.watcher import read_folder

        files = read_folder(rule.folder, rule.pattern, rule.recursive, limit=5)
        if not files:
            self.toastRequested.emit("فایلی برای این قانون پیدا نشد." if self.lang != "en"
                                     else "No file matches this rule.", "warning")
            return
        path = str(files[0])
        mark_run(rule, path, "manual")
        self.ruleTriggered.emit(rule, path)
        self.toastRequested.emit(("اجرا شد: " if self.lang != "en" else "Queued: ") + Path(path).name,
                                 "info")
        self._refresh()

    def scan_now(self) -> None:
        if self.watcher is None:
            return
        hits = self.watcher.scan_now()
        for rule, path in hits:
            mark_run(rule, path, "auto")
            self.ruleTriggered.emit(rule, path)
        message = ((f"{len(hits)} فایل تازه پردازش شد." if self.lang != "en"
                    else f"{len(hits)} new file(s) handled.") if hits else
                   ("فایل تازه‌ای نبود." if self.lang != "en" else "No new files."))
        self.toastRequested.emit(message, "success" if hits else "info")
        self._refresh()

    # ---------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        # keep the form at the top, rebuild the rule list underneath
        while self.body_layout.count() > 1:
            item = self.body_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()
        self._refresh_targets() if self._target_combo.count() == 0 else None
        rules = store.rules()
        status = "دیده‌بان خاموش است" if self.lang != "en" else "watcher stopped"
        if self.watcher is not None and self.watcher.running:
            status = ((f"دیده‌بان فعال · هر {self.watcher.interval_ms // 1000} ثانیه"
                       if self.lang != "en" else
                       f"watcher running · every {self.watcher.interval_ms // 1000}s"))
        self._status_label.setText(status)
        if not rules:
            self.body_layout.addWidget(label(
                "هنوز قانونی نساخته‌اید. پوشه‌ی دانلود یا اسکنر را زیر نظر بگیرید تا فایل‌های تازه خودکار تبدیل شوند."
                if self.lang != "en" else
                "No rules yet. Watch your Downloads or scanner folder so new files convert themselves.",
                "Hint", wrap=True))
            self.body_layout.addStretch(1)
            self.fit_body_height()
            return
        for rule in rules:
            card = RuleCard(rule, self.lang, self.palette_ref)
            card.runNow.connect(self.run_rule_now)
            card.toggle.connect(self.toggle_rule)
            card.remove.connect(self.delete_rule)
            self.body_layout.addWidget(card)
        self.body_layout.addWidget(divider())
        self.body_layout.addStretch(1)
        self.fit_body_height()
