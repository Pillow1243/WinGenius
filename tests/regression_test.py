"""Regressions for the 2026-11 review (docs/review_2026-11_fa.md).

Each test pins one *user-visible* defect so it cannot come back:
  * media duration without ffprobe          (B1)
  * tool panel ignored the language switch  (B4)
  * radial count badge clipped its text     (B5)
  * CLI crashed on a bad --set value        (B17)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# Persian/Arabic *letters* — digits (۰-۹, ٠-٩) are deliberately excluded: labels
# such as "Persian ۱۲۳" describe a numeral system and must stay as they are.
from app.core.i18n import ARABIC_LETTER  # noqa: E402  (shared by tool_panel)

#: Tools whose English metadata *deliberately* shows Persian sample text
#: (a Persian text engine, a form-filler example, a letter-fixing tool).
PERSIAN_SAMPLES_OK = {"persian_fix", "text_to_pdf", "pdf_forms_fill"}


# --------------------------------------------------------------- B1 media probe
def test_ffmpeg_banner_parsing():
    from app.tools.media_tools import _parse_ffmpeg_report

    duration, kinds = _parse_ffmpeg_report(
        "  Duration: 00:01:02.50, start: 0.000000, bitrate: 12 kb/s\n"
        "  Stream #0:0: Video: h264, yuv420p\n  Stream #0:1: Audio: aac\n"
    )
    assert duration == pytest.approx(62.5, abs=0.01)
    assert kinds == {"video", "audio"}
    assert _parse_ffmpeg_report("Duration: N/A, bitrate: N/A") == (0.0, set())


def test_duration_uses_ffmpeg_when_ffprobe_is_missing(monkeypatch):
    """imageio-ffmpeg ships ffmpeg only — the fallback must still report a duration."""
    from app.tools import media_tools

    media_tools._PROBE_CACHE.clear()
    monkeypatch.setattr(media_tools, "has_ffprobe", lambda: None)
    monkeypatch.setattr(media_tools, "has_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(
        media_tools, "subprocess",
        type("Fake", (), {"run": staticmethod(
            lambda *a, **k: type("R", (), {"stdout": "", "stderr": "  Duration: 00:00:02.00, start: 0"})()
        )}),
    )
    assert media_tools._duration(Path("clip.mp4")) == pytest.approx(2.0)
    assert media_tools._stream_kinds(Path("clip.mp4")) == set()


def test_probe_is_cached_per_file_version(monkeypatch, tmp_path):
    from app.tools import media_tools

    media_tools._PROBE_CACHE.clear()
    calls = []
    monkeypatch.setattr(media_tools, "has_ffprobe", lambda: None)
    monkeypatch.setattr(media_tools, "has_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(media_tools, "_probe_with_ffmpeg",
                        lambda path: (calls.append(1), (3.0, {"video"}))[1])
    media = tmp_path / "x.mp4"
    media.write_bytes(b"0")
    for _ in range(4):
        assert media_tools._duration(media) == 3.0
    assert len(calls) == 1, "the same unchanged file must be probed once"


# ---------------------------------------------------------------- B17 CLI --set
def test_cli_rejects_bad_numbers_instead_of_crashing():
    from app.core.cli import CliUsageError, _build_values
    from app.core.registry import load_all, get

    load_all()
    spec = get("pdf_to_images")
    with pytest.raises(CliUsageError):
        _build_values(spec, [], [("dpi", "abc")], "")
    with pytest.raises(CliUsageError):
        _build_values(spec, [], [("fmt", "not-a-format")], "")


def test_cli_warns_about_unknown_set_key_and_suggests():
    from app.core.cli import _build_values
    from app.core.registry import load_all, get

    load_all()
    spec = get("video_contact_sheet")
    warnings: list[str] = []
    values = _build_values(spec, [], [("out_dir", "/tmp/x")], "", warnings)
    assert "out_dir" not in values          # an unknown key is never invented
    assert any("out_dir" in w and "out" in w for w in warnings)


def test_cli_out_of_range_is_a_usage_error():
    from app.core.cli import CliUsageError, _build_values
    from app.core.registry import load_all, get

    load_all()
    spec = get("pdf_to_images")          # dpi: 36…600
    with pytest.raises(CliUsageError):
        _build_values(spec, [], [("dpi", "99999")], "")


# ------------------------------------------------------------- B5 radial badge
def test_badge_pill_is_wide_enough_for_its_text():
    from PySide6.QtGui import QFontMetrics

    from app.core import theme
    from app.ui.radial import RadialMenu

    menu = RadialMenu()
    menu.resize(964, 824)
    metrics = QFontMetrics(theme.make_font(7.4, 600))
    centre = menu._center()
    radius = menu._node_radius()
    for text in ("5", "16", "43", "292", "۱٬۲۳۴"):
        rect = menu._badge_rect(text, centre, radius, metrics)
        assert rect.width() >= metrics.horizontalAdvance(text) + 8, text
        assert menu.rect().contains(rect.toRect()), f"badge for {text!r} leaves the widget"


# ------------------------------------------------ B4 tool panel language switch
@pytest.fixture(scope="module")
def window(qapplication):
    """One MainWindow for the whole module: creating a second one stresses Qt on
    old CPython builds (and mirrors how tests/ui_test.py drives the shell)."""
    from app.core.i18n import set_language
    from app.core.registry import load_all
    from app.core.settings import settings
    from app.ui.main_window import MainWindow

    load_all()
    set_language("fa")
    # No window animation and no visible window: these tests assert *state*, and
    # driving Qt's paint path here only makes old CPython+PySide combos crash.
    animations = settings.get("ui/animations")
    settings.set("ui/animations", False)
    win = MainWindow()
    win.resize(1000, 920)
    yield win
    win.close()
    settings.set("ui/animations", animations)


def test_tool_panel_follows_the_language_switch(window):
    from app.core.i18n import set_language

    set_language("fa")
    window._on_language_changed("fa")
    window._open_tool_by_id("pdf_to_images")
    panel = window.tool_panel
    assert panel._run_button.text() == "اجرا"

    panel._widgets["dpi"].setValue(321)
    window._on_language_changed("en")

    assert panel._run_button.text() == "Run"
    assert panel._queue_button.text() == "Run in background"
    assert panel._cancel_button.text() == "Cancel"
    assert panel._log_button.text() == "Log"
    assert ARABIC_LETTER.search(panel._status.text() or "") is None
    # …and the half-filled form survives the switch.
    assert panel._widgets["dpi"].value() == 321
    assert panel.spec is not None and panel.spec.id == "pdf_to_images"
    window._on_language_changed("fa")


def test_tool_metadata_is_fully_translated():
    """Data-level guard (no Qt): every label, placeholder and choice has English."""
    from app.core.registry import all_tools, load_all
    from app.core.spec import K_CHOICE

    load_all()
    offenders: list[str] = []
    for spec in all_tools():
        if ARABIC_LETTER.search(spec.name_en or ""):
            offenders.append(f"{spec.id}: name_en is not English")
        if ARABIC_LETTER.search(spec.desc_en or "") and spec.id not in PERSIAN_SAMPLES_OK:
            offenders.append(f"{spec.id}: desc_en is not English")
        for field in spec.fields:
            if not (field.label_en or "").strip():
                offenders.append(f"{spec.id}.{field.key}: no label_en")
            if (field.placeholder and not field.placeholder_en
                    and ARABIC_LETTER.search(field.placeholder)
                    and spec.id not in PERSIAN_SAMPLES_OK):
                offenders.append(f"{spec.id}.{field.key}: Persian-only placeholder")
            if field.help and not field.help_en:
                offenders.append(f"{spec.id}.{field.key}: help without help_en")
            if field.kind == K_CHOICE:
                for _value, label_fa, label_en in field.choices:
                    if label_fa and not label_en:
                        offenders.append(f"{spec.id}.{field.key}: choice without English label")
    assert not offenders, "untranslated tool metadata:\n" + "\n".join(offenders[:20])


def _live_form_widgets(panel):
    """Every widget that belongs to the *currently loaded* tool form.

    Deliberately walks the form layout instead of panel.findChildren(): widgets
    from the previous tool are only queued for deletion, and flushing deferred
    deletes from Python (``sendPostedEvents(None, DeferredDelete)``) crashes this
    PySide6 build (none_dealloc).  ToolPanel.load() now detaches them, so the
    layout is the single source of truth either way.
    """
    out = []
    for i in range(panel._form.count()):
        widget = panel._form.itemAt(i).widget()
        if widget is not None:
            out.append(widget)
    return out


def test_field_placeholder_follows_the_language(window):
    """PathEdit / multiline / text fields must show placeholder_en in English and
    never fall back to a Persian hint (B4, widget level)."""
    from app.core.spec import K_TEXT, Field
    from PySide6.QtWidgets import QLineEdit

    window._on_language_changed("en")
    window._open_tool_by_id("pdf_merge")
    panel = window.tool_panel
    fa_field = Field(key="x", label="X", kind=K_TEXT, placeholder="مسیر فایل را بچسبانید")
    assert panel._placeholder(fa_field) == ""
    en_field = Field(key="x", label="X", kind=K_TEXT, placeholder="مسیر فایل را بچسبانید",
                     placeholder_en="Paste a path")
    assert panel._placeholder(en_field) == "Paste a path"
    widget = panel._create_widget(en_field)
    assert isinstance(widget, QLineEdit) and widget.placeholderText() == "Paste a path"
    window._on_language_changed("fa")
    assert panel._placeholder(en_field) == "مسیر فایل را بچسبانید"


def test_no_persian_left_in_english_tool_forms(window):
    """Widget-level guard: in English no visible label/button of a tool form may
    fall back to Persian.  This is what let the untranslated footer through."""
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit

    window._on_language_changed("en")
    # A representative slice: the footer bug showed up on every tool, and the
    # "؟" help glyph only on the fields that actually have help text.
    sample = ["pdf_to_images", "pdf_sign", "vault_extract", "date_math", "subtitle_convert"]
    offenders: list[str] = []
    for tool_id in sample:
        # No processEvents() in the loop: load() detaches the previous form
        # synchronously, and letting Qt delete those widgets from inside the
        # loop crashes this PySide6 build (see _live_form_widgets).
        window._open_tool_by_id(tool_id)
        panel = window.tool_panel
        roots = _live_form_widgets(panel)
        assert roots, f"{tool_id}: form did not load"
        widgets = [panel._run_button, panel._queue_button, panel._cancel_button, panel._log_button]
        for root in roots:
            if isinstance(root, (QLabel, QCheckBox)):
                widgets.append(root)
            widgets += root.findChildren(QLabel) + root.findChildren(QCheckBox)
        for widget in widgets:
            if ARABIC_LETTER.search(widget.text() or ""):
                offenders.append(f"{tool_id}: '{widget.text()[:40]}'")
        for root in roots:
            edits = root.findChildren(QLineEdit) + ([root] if isinstance(root, QLineEdit) else [])
            for edit in edits:
                if ARABIC_LETTER.search(edit.placeholderText() or ""):
                    offenders.append(f"{tool_id}: placeholder '{edit.placeholderText()[:40]}'")
            combos = root.findChildren(QComboBox) + ([root] if isinstance(root, QComboBox) else [])
            for combo in combos:
                for i in range(combo.count()):
                    if ARABIC_LETTER.search(combo.itemText(i) or ""):
                        offenders.append(f"{tool_id}: choice '{combo.itemText(i)[:40]}'")
    assert not offenders, "Persian text in the English UI:\n" + "\n".join(offenders[:20])
    window._on_language_changed("fa")
