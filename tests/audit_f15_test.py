"""F-15 regression test — 2026-11 independent audit.

Switching the UI to English must not leave Persian text behind. Several widgets
built their labels/tooltips once, from a Persian literal, and were never
retranslated — the Dashboard shortcut tooltips and the Automation panel section
title being the visible cases. This test scans the live widget tree for Arabic
script after a language switch, which catches the next one automatically.
"""
from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

ARABIC = re.compile(r"[\u0600-\u06FF]")

# Text that is legitimately shown while the UI is in English:
#   * a language name is written in its own script ("فارسی" next to "English"),
#   * the Persian-digits sample "۰-۹" inside an English label,
#   * strings the bidi/RTL helper inserts.
ALLOWED = re.compile(r"^(فارسی|English)$|[۰-۹]|^[\u200e\u200f]+$")


def _leaks(widget) -> list:
    from PySide6.QtWidgets import QWidget

    found = []
    for child in widget.findChildren(QWidget):
        for attribute in ("text", "toolTip", "placeholderText", "accessibleName"):
            try:
                value = getattr(child, attribute)()
            except Exception:                    # not every widget has every property
                continue
            if isinstance(value, str) and ARABIC.search(value) and not ALLOWED.search(value):
                found.append((type(child).__name__, attribute, value[:60]))
    return found


def _flush(app) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QCoreApplication.processEvents()


@pytest.fixture()
def english():
    """Switch the string table to English and restore it afterwards."""
    from app.core.i18n import set_language

    set_language("en")
    yield
    set_language("fa")


def _panel(name: str, factory):
    from PySide6.QtWidgets import QApplication

    assert QApplication.instance() is not None
    try:
        return factory()
    except TypeError:
        return factory(None)


@pytest.mark.parametrize("panel_name", ["Dashboard", "AutomationPanel", "SchedulerPanel",
                                        "JobsDrawer", "ToolPanel", "SettingsPanel"])
def test_f15_no_persian_left_after_switching_to_english(panel_name, english):
    from app.core.i18n import set_language
    from app.ui import automation_panel, dashboard, jobs_drawer, scheduler_panel
    from app.ui import settings_panel, tool_panel

    factories = {
        "Dashboard": dashboard.Dashboard,
        "AutomationPanel": automation_panel.AutomationPanel,
        "SchedulerPanel": scheduler_panel.SchedulerPanel,
        "JobsDrawer": jobs_drawer.JobsDrawer,
        "ToolPanel": tool_panel.ToolPanel,
        "SettingsPanel": settings_panel.SettingsPanel,
    }
    set_language("fa")                      # build in Persian…
    widget = _panel(panel_name, factories[panel_name])
    set_language("en")                      # …then switch, as the toggle does
    widget.set_language("en")
    _flush(None)
    leaks = _leaks(widget)
    assert not leaks, f"{panel_name} kept Persian text in the English UI: {leaks[:6]}"


def test_f15_string_table_has_both_languages():
    from app.core.i18n import STRINGS

    missing = {key: sorted({"fa", "en"} - set(entry)) for key, entry in STRINGS.items()
               if not {"fa", "en"} <= set(entry)}
    assert not missing, f"entries missing a language: {missing}"
    empty = [key for key, entry in STRINGS.items()
             if not entry["fa"].strip() or not entry["en"].strip()]
    assert not empty, f"empty translations: {empty}"


def test_f15_every_used_key_exists():
    """A missing key silently renders as the raw key, so the table must cover usage."""
    from pathlib import Path

    from app.core.i18n import STRINGS

    used = {}
    for path in list((Path(__file__).resolve().parents[1] / "app").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'\bt\(\s*"([\w.]+)"', text):
            used.setdefault(match.group(1), path.name)
    missing = {key: where for key, where in used.items() if key not in STRINGS}
    assert not missing, f"t() keys with no entry: {missing}"


def test_f15_unknown_key_falls_back_instead_of_crashing():
    from app.core.i18n import t

    assert t("does.not.exist") == "does.not.exist"
