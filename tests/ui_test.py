"""GUI regression test: build every tool form, switch language/theme, run a tool.

    QT_QPA_PLATFORM=offscreen python tests/ui_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never open real windows from a test run: the shell launcher becomes a dry run.
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")
# keep the developer's real profile clean: tests get their own data folder
if os.name != "nt":
    import tempfile as _tempfile
    os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="wg-test-cfg-"))

from PySide6.QtCore import QEventLoop, QTimer                     # noqa: E402

from app.bootstrap import create_app                              # noqa: E402
from app.core.registry import all_tools, load_all                 # noqa: E402
from app.core.settings import settings                            # noqa: E402
from app.ui.main_window import MainWindow                         # noqa: E402

failures: list[str] = []
checks = 0


def spin(ms: int = 120) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)
        print(f"  ✗ {message}")


def main() -> int:
    app = create_app([])
    load_all()
    window = MainWindow()
    window.resize(1000, 920)
    window.show()
    spin(400)

    from app.core.watcher import store as watch_store
    from app.core.workflows import store
    saved_ids = {workflow.id for workflow in store.user_items()}

    print("[navigation]")
    check(len(window.radial._nodes) >= 9, "home page should show all categories")
    for category in ("pdf", "image", "media", "files", "system", "network",
                     "security", "convert", "windows"):
        window.open_category(category)
        spin(60)
        check(bool(window.radial._nodes), f"category {category} has no nodes")
        check(window.radial._pages >= 1, f"category {category} pagination broken")
    window.show_home()
    spin(60)
    print("  ok")

    print("[tool forms]")
    for spec in all_tools():
        try:
            window._open_tool_by_id(spec.id)
            spin(30)
            check(window.tool_panel.spec is not None and window.tool_panel.spec.id == spec.id,
                  f"panel did not load {spec.id}")
            # every visible field must have a widget
            for field in spec.fields:
                if window.tool_panel._is_visible(field):
                    check(field.key in window.tool_panel._widgets,
                          f"{spec.id}: widget missing for {field.key}")
            window._close_overlay()
            spin(10)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"form build failed for {spec.id}: {exc}")
            print(f"  ✗ {spec.id}: {exc}")
    print(f"  {len(all_tools())} forms built")

    print("[language + theme]")
    for lang in ("en", "fa"):
        window._on_language_changed(lang)
        spin(80)
        check(window.lang == lang, f"language did not switch to {lang}")
        window.show_home(animate=False)
        spin(50)
    for theme_key in ("midnight", "daylight", "aurora"):
        settings.theme = theme_key
        window._apply_theme()
        window.show_home(animate=False)
        spin(80)
    print("  ok")

    print("[search]")
    window.open_search()
    window.quick_search._input.setText("عکس به پی دی اف")
    spin(200)
    check(window.quick_search._list.count() > 0, "search returned no results")
    first = window.quick_search._list.item(0).data(0x0100)
    check(first == "images_to_pdf", f"assistant ranked '{first}' instead of images_to_pdf")
    window.quick_search.close_palette()
    spin(100)

    print("[run a tool through the worker thread]")
    tmp = Path(tempfile.mkdtemp(prefix="wingenius_ui_"))
    sample = tmp / "sample.txt"
    sample.write_text("سلام دنیا\nWinGenius UI test\n", encoding="utf-8")
    window._open_tool_by_id("base64_tool")
    spin(80)
    panel = window.tool_panel
    panel._set_widget_value(panel._widgets["source"], "file") if False else None
    for key, value in (("mode", "encode"), ("source", "file"), ("text", ""),
                       ("file", str(sample)), ("save", str(tmp / "out.txt"))):
        widget = panel._widgets.get(key)
        if widget is not None:
            panel._set_widget_value(widget, value)
    panel._refresh_visibility()
    panel._run()
    deadline = time.time() + 20
    while panel._runner is not None and time.time() < deadline:
        spin(60)
    check((tmp / "out.txt").exists(), "worker run produced no output file")
    check(panel._result is not None, "panel did not receive a result")
    print("  ok")
    window._close_overlay()
    spin(120)

    print("[error path]")
    window._open_tool_by_id("base64_tool")
    spin(80)
    panel = window.tool_panel
    for key, value in (("mode", "decode"), ("source", "text"), ("text", "!!!not-base64!!!"),
                       ("file", ""), ("save", "")):
        widget = panel._widgets.get(key)
        if widget is not None:
            panel._set_widget_value(widget, value)
    panel._refresh_visibility()
    panel._run()
    deadline = time.time() + 15
    while panel._runner is not None and time.time() < deadline:
        spin(60)
    check(panel._result_card.isVisible(), "error result card not shown")
    check(panel._result is not None and ("Base64" in panel._result.message
                                         or "base64" in panel._result.message),
          "unexpected error message for invalid Base64")
    window._close_overlay()
    spin(120)

    print("[overlay edge cases]")
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtTest import QTest

    window.open_search()
    spin(150)
    check(window.quick_search.isVisible(), "search palette did not open")
    QTest.keyClick(window.quick_search._input, _Qt.Key.Key_Escape)
    spin(200)
    check(not window.quick_search.isVisible(), "Esc did not close the palette")
    check(window._overlay is None, "overlay state not cleared after Esc")

    window.open_search()
    spin(120)
    window.open_settings()
    spin(220)
    check(not window.quick_search.isVisible(), "settings did not replace the palette")
    check(window.settings_panel.isVisible(), "settings panel not visible")
    QTest.keyClick(window.settings_panel, _Qt.Key.Key_Escape)
    spin(200)
    check(not window.settings_panel.isVisible(), "Esc did not close settings")

    window._open_tool_by_id("pdf_info")
    spin(200)
    window.open_category("files")
    spin(100)
    check(window._overlay == "none" or window._overlay is None, "category click should close the sheet")
    print("  ok")

    # ---------------------------------------------------------------- v2 UI
    print("[job queue drawer]")
    window.show_home()
    spin(60)
    check(hasattr(window, "jobs") and window.jobs is not None, "job manager missing on the shell")
    window.open_jobs()
    spin(150)
    check(window.jobs_drawer.isVisible(), "job drawer did not open")
    check(window._overlay == "jobs", f"overlay state is {window._overlay!r}")
    job = window.jobs.submit_by_id("uuid_generate", {"count": 2, "upper": False, "braces": False})
    check(job is not None, "could not submit a background job")
    deadline = time.time() + 20
    while time.time() < deadline and job is not None and job.active:
        spin(80)
    check(job is not None and job.status == "done", f"job finished with status {getattr(job, 'status', '?')}")
    window.jobs_drawer.refresh()
    spin(120)
    check(job.id in window.jobs_drawer._rows, "finished job does not appear in the drawer")
    drawer = window.jobs_drawer
    from app.ui.jobs_drawer import JOB_ROW_HEIGHT, JOB_ROW_SPACING
    heights = {row.height() for row in drawer._rows.values()}
    check(heights == {JOB_ROW_HEIGHT}, f"job rows are not uniform: {heights}")
    shown = min(len(drawer._rows), 5)
    needed = shown * JOB_ROW_HEIGHT + (shown - 1) * JOB_ROW_SPACING
    check(drawer._scroll.height() >= needed,
          f"job list clips its rows ({drawer._scroll.height()}px for {needed}px of rows)")
    window.jobs_drawer._clear_finished()
    spin(80)
    window._close_overlay()
    spin(120)
    check(not window.jobs_drawer.isVisible(), "job drawer did not close")
    check(window._overlay is None, "overlay state not cleared after closing jobs")

    print("[background run from the tool panel]")
    window._open_tool_by_id("text_stats")
    spin(120)
    panel = window.tool_panel
    panel._set_widget_value(panel._widgets["text"], "سلام دنیا سلام")
    panel._run_in_background()
    spin(150)
    check(window._overlay == "jobs", "background run should open the job queue")
    deadline = time.time() + 20
    while time.time() < deadline and window.jobs.has_active():
        spin(80)
    stats = window.jobs.stats()
    newest = window.jobs.jobs()[0] if window.jobs.jobs() else None
    check(newest is not None and newest.status == "done",
          f"background job did not finish: {newest.status if newest else 'none'} ({stats})")
    check(newest is not None and newest.tool_id == "text_stats",
          f"unexpected background tool: {newest.tool_id if newest else '?'}")
    window._close_overlay()
    spin(120)

    print("[smart drop suggestions]")
    sample_pdf = tmp / "drop.pdf"
    from reportlab.pdfgen import canvas as _canvas

    doc = _canvas.Canvas(str(sample_pdf))
    doc.drawString(60, 700, "drop test")
    doc.save()
    window._show_drop([str(sample_pdf)])
    spin(150)
    check(window.drop_zone.isVisible(), "drop zone did not open")
    check(window._overlay == "drop", f"overlay state is {window._overlay!r}")
    rows = [window.drop_zone._rows.itemAt(i).widget() for i in range(window.drop_zone._rows.count())]
    rows = [row for row in rows if row is not None and hasattr(row, "spec")]
    check(len(rows) >= 3, f"expected several suggestions, got {len(rows)}")
    check(any(row.spec.id.startswith("pdf") for row in rows), "no PDF tool suggested for a PDF")
    from app.ui.drop_zone import ROW_HEIGHT, ROW_SPACING
    check({row.height() for row in rows} == {ROW_HEIGHT}, "suggestion rows are not uniform")
    visible = min(len(rows), 5)
    check(window.drop_zone._scroll.height() >= visible * ROW_HEIGHT + (visible - 1) * ROW_SPACING,
          "suggestion list clips its rows")
    chosen: list = []
    window.drop_zone.toolChosen.connect(lambda tool_id, paths: chosen.append((tool_id, paths)))
    rows[0].picked.emit(rows[0].spec.id)
    spin(150)
    check(bool(chosen), "clicking a suggestion emitted nothing")
    if chosen:
        tool_id, paths = chosen[0]
        check(tool_id == rows[0].spec.id, "wrong tool id emitted")
        check(paths and str(sample_pdf) in paths, "dropped path not forwarded")
    window._close_overlay()
    spin(120)

    print("[drop -> tool panel prefill]")
    window._on_drop_choice("pdf_to_images", [str(sample_pdf)])
    spin(200)
    panel = window.tool_panel
    check(panel.spec is not None and panel.spec.id == "pdf_to_images", "drop did not open the chosen tool")
    values = panel._collect()
    check(values.get("src") == str(sample_pdf), f"file field not prefilled: {values.get('src')!r}")
    window._close_overlay()
    spin(120)

    print("[palette commands + recents]")
    window.open_search()
    spin(150)
    palette = window.quick_search
    palette._input.setText("jobs")
    palette._refresh()
    spin(80)
    keys = [palette._list.item(i).data(_Qt.ItemDataRole.UserRole)
            for i in range(palette._list.count())]
    check(any(isinstance(key, str) and key.startswith("cmd:") for key in keys),
          f"command entries missing from the palette: {keys}")
    fired: list = []
    palette.commandChosen.connect(fired.append)
    palette._input.setText("")
    palette._refresh()
    spin(80)
    for index in range(palette._list.count()):
        item = palette._list.item(index)
        key = item.data(_Qt.ItemDataRole.UserRole)
        if isinstance(key, str) and key == "cmd:doctor":
            palette._on_item_activated(item)
            break
    spin(200)
    check(fired == ["doctor"], f"command not dispatched: {fired}")
    check(window.settings_panel.isVisible(), "doctor command did not open settings")
    window._close_overlay()
    spin(120)

    print("[settings diagnostics section]")
    window.open_settings()
    spin(200)
    check(window.settings_panel._diag_view.toPlainText().strip() != "", "diagnostics view is empty")
    check(window.settings_panel._parallel.count() >= 3, "parallel job selector missing")
    window._close_overlay()
    spin(120)

    # ---------------------------------------------------------------- v3 UI
    print("[nav rail + sheets]")
    rail = getattr(window, "_nav_rail", None)
    check(rail is not None and rail.isVisible(), "nav rail missing")
    check(len(window._nav_buttons) == 5, f"nav rail has {len(window._nav_buttons)} buttons")
    for button, overlay in zip(window._nav_buttons,
                               ("dashboard", "plan", "workflows", "automation", "scheduler")):
        button.click()
        spin(220)
        check(window._overlay == overlay, f"nav button did not open {overlay} (got {window._overlay})")
        sheet = getattr(window, {"dashboard": "dashboard", "plan": "plan_panel",
                                 "workflows": "workflow_panel",
                                 "automation": "automation_panel",
                                 "scheduler": "scheduler_panel"}[overlay])
        check(sheet.isVisible(), f"{overlay} sheet is not visible")
        window._close_overlay()
        spin(140)
        check(window._overlay is None, f"{overlay} overlay state not cleared")

    print("[dashboard]")
    window.open_dashboard()
    spin(250)
    dashboard = window.dashboard
    check(dashboard._card.width() >= 600, "dashboard card too narrow")
    tiles = [child for child in dashboard.findChildren(__import__("PySide6.QtWidgets", fromlist=["QFrame"]).QFrame)
             if child.objectName() == "StatTile"]
    check(len(tiles) >= 4, f"expected 4 stat tiles, got {len(tiles)}")
    check(dashboard._chart is not None, "usage chart missing")
    actions: list = []
    dashboard.actionRequested.connect(actions.append)
    dashboard._shortcut_buttons[0].click()
    spin(150)
    check(actions == ["plan"], f"dashboard shortcut emitted {actions}")
    window._close_overlay()
    spin(150)

    print("[planner sheet]")
    window.open_plan()
    spin(220)
    panel = window.plan_panel
    panel._input.setText("یک رمز عبور قوی بساز")
    panel.make_plan()
    spin(200)
    check(panel._workflow is not None, "planner did not build a plan")
    check([step.tool_id for step in panel._workflow.steps] == ["password_generate"],
          f"unexpected plan: {[step.tool_id for step in panel._workflow.steps]}")
    check(len(panel._cards) == 1, f"plan rendered {len(panel._cards)} step cards")
    finished: list = []
    panel.runFinished.connect(finished.append)
    panel.run_plan()
    deadline = time.time() + 30
    while time.time() < deadline and panel._runner is not None:
        spin(120)
    check(bool(finished) and finished[0].ok, f"plan run failed: {finished[0].status if finished else 'timeout'}")
    check(panel._cards[0]._status.text().endswith("✓"), f"step status is {panel._cards[0]._status.text()!r}")
    before = len(store.user_items())
    panel.save_plan()
    spin(150)
    check(len(store.user_items()) == before + 1, "plan was not saved as a workflow")
    for workflow in store.user_items():
        if workflow.id not in saved_ids:
            store.delete(workflow.id)
    panel._input.setText("")
    window._close_overlay()
    spin(150)

    print("[workflow sheet]")
    window.open_workflows()
    spin(250)
    editor = window.workflow_panel
    check(editor._list.count() >= len(store.all()), "workflow library not listed")
    editor.select("pdf_to_images_zip")
    spin(150)
    check(len(editor._step_rows) == 2, f"workflow editor shows {len(editor._step_rows)} steps")
    editor.set_paths([str(sample_pdf)])
    editor.add_step("text_stats")
    spin(120)
    check(len(editor._workflow.steps) == 3, "add_step did not append")
    editor._move_step(2, 0)
    spin(120)
    check(editor._workflow.steps[0].tool_id == "text_stats", "move_step did not reorder")
    editor._remove_step(0)
    spin(120)
    check(len(editor._workflow.steps) == 2, "remove_step did not delete")
    check(editor._workflow.source != "builtin", "editing a built-in should fork it")
    for workflow in store.user_items():
        if workflow.id not in saved_ids:
            store.delete(workflow.id)
    editor.run_workflow(store.get("pdf_to_images_zip"))
    spin(200)
    deadline = time.time() + 40
    while time.time() < deadline and editor._runner is not None:
        spin(150)
    statuses = [row.status.text() for row in editor._step_rows]
    check(all(text == "✓" for text in statuses), f"workflow rows: {statuses}")
    window._close_overlay()
    spin(150)

    print("[automation sheet]")
    window.open_automation()
    spin(250)
    automation = window.automation_panel
    watch_dir = tmp / "watched_ui"
    watch_dir.mkdir(exist_ok=True)
    for stale in watch_dir.iterdir():
        stale.unlink()
    automation.reset_form()
    automation._name_input.setText("UI rule")
    automation._folder_input.setText(str(watch_dir))
    automation._pattern_input.setText("*.txt")
    automation._action_combo.setCurrentIndex(0)
    automation._refresh_targets()
    index = automation._target_combo.findData("pdf_to_images_zip")
    automation._target_combo.setCurrentIndex(max(0, index))
    automation._settle_spin.setValue(0)
    automation.save_rule()
    spin(200)
    rules = watch_store.rules()
    check(bool(rules), "automation rule was not saved")
    check(automation.watcher is not None and automation.watcher.running,
          "watcher did not start after saving a rule")
    triggered: list = []
    automation.ruleTriggered.connect(lambda rule, path: triggered.append((rule.id, path)))
    (watch_dir / "fresh.txt").write_text("hello", encoding="utf-8")
    automation.scan_now()
    spin(200)
    check(bool(triggered), "watch rule did not trigger for a new file")
    check(window._overlay is not None, "automation sheet closed unexpectedly")
    window._close_overlay()
    spin(120)
    for rule in watch_store.rules():
        watch_store.delete(rule.id)
    window.watcher.stop()

    print("[clipboard intelligence]")
    from PySide6.QtWidgets import QApplication as _QA
    _QA.clipboard().setText('{"hello": "world"}')
    spin(250)
    check(window._clip_hint is not None and window._clip_hint[0] == "json_tool",
          f"clipboard hint: {window._clip_hint}")
    window.run_clipboard_suggestion()
    spin(250)
    check(window.tool_panel.spec is not None and window.tool_panel.spec.id == "json_tool",
          "clipboard suggestion did not open the tool")
    window._close_overlay()
    spin(150)

    print("[scheduler sheet]")
    from app.core.scheduler import store as schedule_store
    for existing in schedule_store.all():
        schedule_store.delete(existing.id)
    window.open_scheduler()
    spin(300)
    scheduler_panel = window.scheduler_panel
    check(scheduler_panel.isVisible(), "scheduler sheet is not visible")
    check(window.scheduler.running, "scheduler service did not start")
    check(scheduler_panel._target_combo.count() > 0, "scheduler target list is empty")
    scheduler_panel._name_input.setText("UI schedule")
    scheduler_panel._cron_input.setText("*/30 * * * *")
    spin(80)                                    # the hint follows the field live
    check("دقیقه" in scheduler_panel._cron_hint.text() or "minute" in scheduler_panel._cron_hint.text(),
          f"cron hint: {scheduler_panel._cron_hint.text()}")
    check("بعدی" in scheduler_panel._cron_next.text() or "next" in scheduler_panel._cron_next.text(),
          f"cron next line: {scheduler_panel._cron_next.text()}")
    check(scheduler_panel._cron_hint.height() <= scheduler_panel._cron_hint.sizeHint().height() + 24,
          "cron hint grew taller than one wrapped line")
    check(scheduler_panel._cron_hint.geometry().width() == scheduler_panel._cron_next.geometry().width(),
          "cron hint and next-run line do not share a width")
    scheduler_panel.save_schedule()
    spin(250)
    saved = schedule_store.all()
    check(len(saved) == 1, f"schedule not saved ({len(saved)})")
    check(saved and saved[0].cron == "*/30 * * * *", "schedule cron was not stored")
    check(len(scheduler_panel._cards) == 1, f"scheduler card count {len(scheduler_panel._cards)}")
    check("هر روز" in scheduler_panel._cron_hint.text() or "daily" in scheduler_panel._cron_hint.text(),
          f"form did not fall back to the default cron: {scheduler_panel._cron_hint.text()}")
    scheduler_panel._cron_input.setText("not a cron")
    check("نامعتبر" in scheduler_panel._cron_hint.text(), "invalid cron was not flagged")
    scheduler_panel._cron_input.setText("*/30 * * * *")
    scheduler_panel.save_templates()
    spin(200)
    from app.core import macro as macro_module
    check(len(macro_module.library()) >= 3, "macro templates were not written")
    scheduler_panel._refresh()
    spin(200)
    scheduler_panel.toggle_schedule(saved[0].id)
    spin(150)
    check(not schedule_store.all()[0].enabled, "toggle did not disable the schedule")
    scheduler_panel.toggle_schedule(saved[0].id)
    spin(150)
    scheduler_panel.delete_schedule(saved[0].id)
    spin(150)
    check(not schedule_store.all(), "schedule was not deleted")
    window._close_overlay()
    spin(150)

    print("[undo journal + dashboard]")
    from app.core.journal import journal, reset_journal
    journal_file = tmp / "ui_journal.sqlite3"
    reset_journal(journal_file)
    rename_dir = tmp / "undo_dir"
    rename_dir.mkdir(exist_ok=True)
    for stale in rename_dir.iterdir():
        stale.unlink()
    for index in range(2):
        (rename_dir / f"file_{index}.txt").write_text("x", encoding="utf-8")
    window._open_tool_by_id("batch_rename")
    spin(150)
    panel = window.tool_panel
    for key, value in (("folder", str(rename_dir)), ("mode", "template"),
                       ("template", "moved_{n}"), ("preview", False)):
        widget = panel._widgets.get(key)
        if widget is not None:
            panel._set_widget_value(widget, value)
    panel._refresh_visibility()
    panel._run()
    deadline = time.time() + 20
    while panel._runner is not None and time.time() < deadline:
        spin(60)
    renamed = sorted(item.name for item in rename_dir.iterdir())
    check(renamed == ["moved_01.txt", "moved_02.txt"], f"rename output: {renamed}")
    window._close_overlay()
    spin(150)
    check(len(journal().entries(limit=5)) >= 1, "journal recorded nothing")
    window.open_dashboard()
    spin(300)
    check(len(getattr(window.dashboard, "_undo_rows", [])) >= 1, "dashboard undo list is empty")
    window._close_overlay()
    spin(120)
    window.undo_last()
    spin(250)
    restored = sorted(item.name for item in rename_dir.iterdir())
    check(restored == ["file_0.txt", "file_1.txt"], f"undo output: {restored}")
    check(journal().entries(limit=5)[0].undone, "journal entry not marked undone")

    print("[v4 tools: vault + macros]")
    window._open_tool_by_id("vault_create")
    spin(200)
    check(window.tool_panel.spec.id == "vault_create", "vault tool did not open")
    check(len(window.tool_panel._widgets) >= 3, "vault form is missing fields")
    window._close_overlay()
    window._open_tool_by_id("undo_history")
    spin(200)
    check(window.tool_panel.spec.id == "undo_history", "journal tool did not open")
    window._close_overlay()
    spin(120)

    print("[accessibility]")
    # Every auto-generated field must carry a screen-reader name; without it the whole
    # form is a pile of anonymous edit boxes for anyone using a screen reader.
    unnamed = []
    inspected = 0
    lang_for_names = window.lang
    for spec in all_tools():
        if spec.category == "home" or not spec.fields:
            continue
        window._open_tool_by_id(spec.id)
        spin(8)
        for key, widget in window.tool_panel._widgets.items():
            inspected += 1
            if not widget.accessibleName():
                unnamed.append(f"{spec.id}.{key}")
    check(inspected > 400, f"only {inspected} field widgets were inspected")
    check(not unnamed, f"fields without an accessible name: {unnamed[:6]}")

    nav_names = [button.accessibleName() for button in window._nav_buttons]
    check(all(nav_names), f"nav buttons without a name: {nav_names}")
    window._on_language_changed("en" if lang_for_names == "fa" else "fa")
    spin(80)
    nav_other = [button.accessibleName() for button in window._nav_buttons]
    check(all(nav_other) and nav_other != nav_names,
          f"nav names did not follow the language: {nav_names} -> {nav_other}")
    window._on_language_changed(lang_for_names)
    spin(80)

    print("[macro run path]")
    # Regression: running a macro used to raise NameError right after queuing it,
    # because the toast read a `result` that did not exist yet.
    from app.core import macro as macro_module

    macro_file = macro_module.save_macro(macro_module.builtin_macros("fa")[0])
    toasts: list = []
    original_toast = window._show_toast
    window._show_toast = lambda text, kind="info", *a, **k: (toasts.append(str(text)),
                                                            original_toast(text, kind, *a, **k))
    try:
        window._on_macro_run(macro_file, False)
        deadline = time.time() + 25
        while time.time() < deadline and len(toasts) < 2:
            app.processEvents()
            time.sleep(0.05)
    finally:
        window._show_toast = original_toast
    check(any("صف" in t or "queued" in t.lower() for t in toasts), f"no queued toast: {toasts[:3]}")
    check(len(toasts) >= 2, f"macro run never reported a result: {toasts}")
    finished = " ".join(toasts[1:]).lower()
    check(any(mark in finished for mark in ("done", "مرحله", "تمام", "failed", "خطا")),
          f"unexpected macro result: {toasts[1:]}")
    check("nameerror" not in finished, "macro run leaked a NameError")

    print("[local api toggle]")
    window.toggle_api_server()
    spin(400)
    check(getattr(window, "_api", None) is not None and window._api.running,
          "local API did not start")
    port = window._api.port
    check(port > 0, f"api port {port}")
    window.toggle_api_server()
    spin(300)
    check(getattr(window, "_api", None) is None, "local API did not stop")

    print("[v4 sheets in English]")
    window._on_language_changed("en")
    spin(250)
    window.open_scheduler()
    spin(300)
    check("Scheduler" in window.scheduler_panel._title.text(),
          f"scheduler title: {window.scheduler_panel._title.text()}")
    window._close_overlay()
    spin(150)
    window._on_language_changed("fa")
    spin(200)

    print("[v3 sheets in English + light theme]")
    window._on_language_changed("en")
    spin(250)
    window.open_dashboard()
    spin(250)
    check("Dashboard" in window.dashboard._title.text(), f"dashboard title: {window.dashboard._title.text()}")
    window._close_overlay()
    window.open_plan()
    spin(200)
    check("Planner" in window.plan_panel._title.text(), f"plan title: {window.plan_panel._title.text()}")
    window._close_overlay()
    spin(120)
    window._on_language_changed("fa")
    spin(200)

    window.close()
    spin(100)
    print()
    print(f"{checks} UI checks executed")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for item in failures:
            print(" -", item)
        return 1
    print("ALL UI CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
