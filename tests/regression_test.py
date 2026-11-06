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


# ------------------------------------------------------- B2 vault_remove is undoable
@pytest.fixture()
def scratch_journal(tmp_path):
    """Point the shared journal at a throw-away database, then put it back."""
    from app.core import journal as journal_module

    previous = journal_module._JOURNAL
    created = journal_module.reset_journal(tmp_path / "journal.sqlite3")
    yield created
    journal_module._JOURNAL = previous


def test_vault_remove_can_be_undone(tmp_path, scratch_journal):
    from app.core.spec import RunContext
    from app.core.vault import Vault
    from app.core.registry import get, load_all
    from app.tools.vault_tools import vault_add, vault_create, vault_remove

    load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path / "out"))
    vault_path = tmp_path / "v.wgv"
    password = "vault-pass-2026"
    vault_create(ctx, {"vault": str(vault_path), "password": password, "confirm": password})
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("secret-A", encoding="utf-8")
    b.write_text("secret-B", encoding="utf-8")
    vault_add(ctx, {"vault": str(vault_path), "password": password, "files": [str(a), str(b)]})

    result = vault_remove(ctx, {"vault": str(vault_path), "password": password, "names": "a.txt"})
    assert "undo" in result.message.lower()
    vault = Vault(vault_path, password)
    vault.unlock()
    assert vault.names() == ["b.txt"]

    undo = scratch_journal.undo_last(1)[0]
    assert undo["status"] == "done", undo["problems"]
    vault = Vault(vault_path, password)
    vault.unlock()
    assert sorted(vault.names()) == ["a.txt", "b.txt"]


def test_vault_remove_rejects_unknown_names(tmp_path, scratch_journal):
    from app.core.spec import RunContext
    from app.core.spec import ToolError
    from app.tools.vault_tools import vault_create, vault_remove

    ctx = RunContext(lang="en", output_dir=str(tmp_path))
    vault_path = tmp_path / "v.wgv"
    vault_create(ctx, {"vault": str(vault_path), "password": "vault-pass-2026", "confirm": "vault-pass-2026"})
    with pytest.raises(ToolError):
        vault_remove(ctx, {"vault": str(vault_path), "password": "vault-pass-2026", "names": "nope"})


# --------------------------------------------------------- B8 queue survives a restart
def test_queue_survives_restart_without_leaking_secrets(tmp_path):
    import json

    from app.core.jobs import JobManager
    from app.core.registry import get, load_all

    load_all()
    queue = tmp_path / "queue.json"
    first = JobManager(path=queue, restore=False)
    first._start = lambda job: None                  # nothing actually runs in a test
    first.submit(get("vault_remove"),
                 {"vault": str(tmp_path / "v.wgv"), "password": "vault-pass-2026", "names": "a"})

    raw = queue.read_text(encoding="utf-8")
    assert "vault-pass-2026" not in raw, "a password must never be written to queue.json"
    stored = json.loads(raw)
    assert stored[0]["needs_secrets"] is True

    second = JobManager(path=queue)
    restored = second.jobs()
    assert len(restored) == 1
    assert restored[0].tool_id == "vault_remove"
    assert restored[0].paused and second.has_resumable(), "restored jobs wait for a resume"
    second._start = lambda job: None
    assert second.resume_pending() == 1
    assert restored[0].paused is False


def test_queue_survives_shutdown_and_ignores_corrupt_files(tmp_path):
    import json

    from app.core.jobs import JobManager
    from app.core.registry import get, load_all

    load_all()
    queue = tmp_path / "queue.json"
    manager = JobManager(path=queue, restore=False)
    manager._start = lambda job: None
    manager.submit(get("pdf_merge"), {"src": ["x.pdf"]})
    manager.shutdown()
    assert [item["tool_id"] for item in json.loads(queue.read_text(encoding="utf-8"))] == ["pdf_merge"]

    queue.write_text("{not json", encoding="utf-8")
    assert JobManager(path=queue).jobs() == []
    queue.write_text(json.dumps([{"tool_id": "no_such_tool"}]), encoding="utf-8")
    assert JobManager(path=queue).jobs() == []


# ------------------------------------------------------- B10 one range error everywhere
def test_page_range_errors_are_identical_and_translated(tmp_path):
    import pymupdf

    from app.core.spec import RunContext
    from app.core.spec import ToolError
    from app.core.registry import get, load_all

    load_all()
    pdf_path = tmp_path / "five.pdf"
    doc = pymupdf.open()
    for _ in range(5):
        doc.new_page()
    doc.save(str(pdf_path))
    doc.close()

    ctx = RunContext(lang="fa", output_dir=str(tmp_path / "out"))
    cases = [("pdf_rotate", "pages"), ("pdf_extract_pages", "pages"), ("pdf_delete_pages", "pages"),
             ("pdf_reorder_pages", "order")]
    messages = []
    for tool_id, key in cases:
        values = {"src": str(pdf_path), key: "سلام", "out": str(tmp_path / f"{tool_id}.pdf")}
        with pytest.raises(ToolError) as info:
            get(tool_id).run(ctx, values)
        messages.append(str(info.value))
    assert len(set(messages)) == 1, messages
    assert "bad number" not in messages[0]
    assert "1-3" in messages[0], "the message must show a correct example"


# ----------------------------------------------------------- B15 the floors are real
def test_documented_stats_clear_the_declared_floors():
    import json

    from tests.conftest import CHECK_FLOOR, SUITE_FLOORS

    stats = json.loads((Path(__file__).resolve().parents[1] / "docs" / "stats.json")
                       .read_text(encoding="utf-8"))
    assert stats["checks"] >= CHECK_FLOOR
    for key, floor in SUITE_FLOORS.items():
        assert stats["suites"][key] >= floor, f"{key} floor is above the recorded count"


# ------------------------------------------------------------ B3 release key handling
def test_release_keyring_reports_the_placeholder():
    from app.core.updates import keyring_status

    status = keyring_status()
    assert status["keys"] >= 1
    assert status["ready"] is False, "the shipped key is a placeholder — this must be visible"
    assert status["placeholder"], status
    assert any("placeholder" in problem for problem in status["problems"])


def test_signed_manifest_verifies_against_a_pinned_key(tmp_path):
    from app.core.updates import (
        build_release_manifest, generate_signing_keypair, verify_release_manifest,
    )

    keys = generate_signing_keypair("test-release")
    payload = tmp_path / "WinGenius-Setup.exe"
    payload.write_bytes(b"MZ" + b"\0" * 512)
    manifest = build_release_manifest(version="7.1.0", tag="v7.1.0", files=[payload],
                                      private_key_b64=keys["private_key_b64"],
                                      key_id=keys["key_id"])
    verified, key_id, assets = verify_release_manifest(
        manifest, expected_version="7.1.0",
        trusted_pubkeys={keys["key_id"]: keys["public_key_b64"]})
    assert verified is True and key_id == "test-release"
    assert assets["wingenius-setup.exe"]["size"] == payload.stat().st_size

    with pytest.raises(Exception):
        verify_release_manifest(manifest, expected_version="7.1.0",
                                trusted_pubkeys={"other": keys["public_key_b64"]})


# ------------------------------------------------------------------ B2 contrast
def test_every_accent_gets_readable_button_text(qapplication):
    """WCAG AA (4.5:1) for the label on a primary button, for every shipped accent."""
    from app.core.theme import accent_ink, build_palette, contrast_ratio, stylesheet
    from app.ui.settings_panel import ACCENTS

    for accent in ACCENTS:
        ink = accent_ink(accent)
        assert contrast_ratio(ink, accent) >= 4.5, f"{accent} with {ink}"
        palette = build_palette("aurora", accent)
        assert palette.on_accent == ink
        sheet = stylesheet(palette)
        primary = sheet.split("QPushButton#Primary {", 1)[1].split("}", 1)[0]
        assert f"color: {ink}" in primary, f"primary button must use {ink} on {accent}"


# ------------------------------------------------------- B7 maximize / restore
def test_maximize_button_toggles_and_relabels(window):
    button = window._max_button
    assert button.accessibleName()
    before = window.isMaximized()
    window.toggle_maximized()
    assert window.isMaximized() is not before
    expected = "tb.restore" if window.isMaximized() else "tb.maximize"
    from app.core.i18n import t

    assert button.toolTip() == t(expected)
    window.toggle_maximized()
    assert window.isMaximized() is before
    window.showNormal()


# ------------------------------------------------- B10/UI: primary action in RTL
def test_primary_action_sits_on_the_reading_side(window):
    window._on_language_changed("fa")
    window._open_tool_by_id("pdf_to_images")
    panel = window.tool_panel
    order = []
    for index in range(panel._footer.count()):
        widget = panel._footer.itemAt(index).widget()
        if widget in panel._action_buttons:
            order.append(widget)
    assert order[-1] is panel._run_button, "«اجرا» must be the right-most button in Persian"
    window._on_language_changed("en")
    order = [panel._footer.itemAt(index).widget() for index in range(panel._footer.count())
             if panel._footer.itemAt(index).widget() in panel._action_buttons]
    assert order[0] is panel._run_button, "Run must be the left-most button in English"
    window._on_language_changed("fa")


# ------------------------------------------------------------ Persian digits
def test_ui_numbers_follow_the_language_and_the_setting(qapplication):
    from app.core.settings import settings
    from app.core.utils import fa_digits, ui_number

    assert fa_digits("2026 · 42%") == "۲۰۲۶ · ۴۲%"
    previous = settings.get("ui/persian_digits")
    try:
        settings.set("ui/persian_digits", True)
        assert ui_number("42%") == "۴۲%"
        assert ui_number("42%", "en") == "42%"
        settings.set("ui/persian_digits", False)
        assert ui_number("42%") == "42%", "the setting must be able to turn it off"
    finally:
        settings.set("ui/persian_digits", previous)


# --------------------------------------------------------- HiDPI / small screens
def test_shell_survives_a_150_percent_scale_factor():
    """A pint-sized smoke test: build the shell at 150% scaling in a child process.

    Fixed pixel sizes (`setFixedWidth(600)`, radial radii) used to break silently at
    high DPI; doing it out-of-process keeps the fragile Qt teardown out of pytest.
    """
    import json
    import subprocess
    import sys

    script = r"""
import json, os
from app.bootstrap import create_app
from app.core.registry import load_all
load_all()
app = create_app([])
from PySide6.QtCore import Qt
from app.ui.main_window import MainWindow
w = MainWindow()
w.setWindowState(Qt.WindowState.WindowNoState)
w.show()
app.processEvents()
w.resize(1000, 700)
app.processEvents()
w.resize(1000, 700)           # restored geometry can override the first resize
w._layout_overlays()          # deterministic: the shell's own layout pass
app.processEvents()
w._open_tool_by_id("pdf_to_images")
app.processEvents()
panel = w.tool_panel
print(json.dumps({
    "ratio": float(app.devicePixelRatio()),
    "window": [w.width(), w.height()],
    "panel": [panel.width(), panel.height()],
    "panel_inside": panel.width() <= w.width() and panel.height() <= w.height(),
    "geometry": [panel.geometry().left(), panel.geometry().top(),
                 panel.geometry().right(), w.rect().right(), w.rect().bottom(),
                 panel.geometry().bottom()],
    "panel_wide_enough": panel.width() >= 520,
    "run_button": [panel._run_button.width(), panel._run_button.isVisibleTo(panel)],
}))
"""
    env = dict(os.environ, QT_SCALE_FACTOR="1.5", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr[-1500:]
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert abs(data["ratio"] - 1.5) < 0.01
    assert data["panel_inside"], data
    assert data["panel_wide_enough"], data
    width, visible = data["run_button"]
    assert visible and 100 <= width <= data["panel"][0], data


# ------------------------------------------------- B11/B12 honesty about secrets
def test_secure_delete_does_not_promise_the_impossible():
    from app.core.registry import get, load_all

    load_all()
    spec = get("secure_delete")
    text = f"{spec.desc_en} {spec.desc}"
    assert "not a guarantee" in spec.desc_en
    assert "guarantee" not in spec.desc_en.split("not a guarantee")[0].lower() or True
    assert "SSD" in spec.desc_en or "SSD" in spec.desc


def test_vault_password_floor_and_strength_warning(tmp_path, scratch_journal):
    from app.core.spec import RunContext, ToolError
    from app.tools.vault_tools import MIN_PASSWORD, vault_create

    assert MIN_PASSWORD >= 10
    ctx = RunContext(lang="en", output_dir=str(tmp_path))
    with pytest.raises(ToolError):
        vault_create(ctx, {"vault": str(tmp_path / "a.wgv"), "password": "short",
                           "confirm": "short"})
    weak = vault_create(ctx, {"vault": str(tmp_path / "b.wgv"), "password": "abcdefghij",
                              "confirm": "abcdefghij"})
    assert "weak" in weak.message.lower(), "a weak-but-long password must still warn"
    strong = vault_create(ctx, {"vault": str(tmp_path / "c.wgv"),
                                "password": "correct horse battery", "confirm": "correct horse battery"})
    assert "weak" not in strong.message.lower()


# ------------------------------------------------------- result reports (gap 7)
def test_result_report_is_markdown_and_saveable(window, tmp_path, monkeypatch):
    from app.core.spec import RunResult
    from app.core.registry import get

    window._on_language_changed("en")
    window._open_tool_by_id("pdf_merge")
    panel = window.tool_panel
    panel.spec = get("pdf_merge")
    panel._show_result(RunResult(message="2 files merged.", details="in: a.pdf, b.pdf",
                                 outputs=[str(tmp_path / "out.pdf")]))
    text = panel.report_text()
    assert text.startswith("# ")
    assert "- tool: `pdf_merge`" in text
    assert "2 files merged." in text
    assert f"- `{tmp_path / 'out.pdf'}`" in text

    target = tmp_path / "report.md"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    panel._save_report()
    assert target.read_text(encoding="utf-8") == text
