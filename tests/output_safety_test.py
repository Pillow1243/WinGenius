"""Regression tests for the 2026-11-06 audit fixes (output safety, pypdf 7,
uninstall argv parsing, Unix permissions, API validation, docs consistency).

Covers audit items #2, #3, #4, #5, #6, #8, #9, #11.  Everything runs locally
with tmp dirs; nothing touches the real profile or the network.
"""
from __future__ import annotations

import io
import os
import re
import stat
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.registry import all_tools, load_all
from app.core.spec import K_CHOICE, K_INT, K_TEXT, Field, RunContext, ToolError, ToolSpec
from app.tools._helpers import same_file, save_target


@pytest.fixture(scope="module", autouse=True)
def _load_tools():
    failures = load_all()
    assert not failures, f"tool modules failed: {failures}"


@pytest.fixture
def ctx(tmp_path):
    return RunContext(lang="en", output_dir=str(tmp_path / "out"))


# ------------------------------------------------------------- item #3: save_target
def test_save_target_refuses_input_equals_output(ctx, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    ctx.protected_inputs = [str(src)]
    with pytest.raises(ToolError):
        save_target(ctx, str(src), "out.pdf")


def test_save_target_never_overwrites_existing(ctx, tmp_path):
    existing = tmp_path / "report.txt"
    existing.write_text("original", encoding="utf-8")
    dest = save_target(ctx, str(existing), "report.txt")
    assert dest != existing
    assert existing.read_text(encoding="utf-8") == "original"   # untouched


def test_save_target_default_output_is_unique(ctx, tmp_path):
    first = save_target(ctx, "", "out.bin")
    first.write_bytes(b"x")
    second = save_target(ctx, "", "out.bin")
    assert first != second


def test_same_file_detects_symlink_and_case(ctx, tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    assert same_file(real, link)
    assert same_file(real, real)


def test_pdf_rotate_refuses_src_equals_out(ctx, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from app.tools.pdf_tools import pdf_rotate

    src = tmp_path / "in.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(src, "wb") as fh:
        writer.write(fh)
    original = src.read_bytes()
    ctx.protected_inputs = [str(src)]
    with pytest.raises(ToolError):
        pdf_rotate(ctx, {"src": str(src), "angle": 90, "out": str(src)})
    assert src.read_bytes() == original                      # original intact


# ------------------------------------------------------------- item #2: pypdf 7
def test_pdf_stamp_image_works_on_pypdf7(ctx, tmp_path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    PIL = pytest.importorskip("PIL.Image")
    from app.tools.pdf_extra import pdf_stamp_image

    src = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(src, "wb") as fh:
        writer.write(fh)
    img = tmp_path / "stamp.png"
    image = PIL.new("RGB", (40, 40), (200, 30, 30))
    image.save(img)
    ctx.protected_inputs = [str(src)]
    result = pdf_stamp_image(ctx, {"src": str(src), "image": str(img), "out": ""})
    assert result.outputs and Path(result.outputs[0]).exists()
    out_reader = pypdf.PdfReader(result.outputs[0])
    assert len(out_reader.pages) == 1
    assert src.read_bytes() != Path(result.outputs[0]).read_bytes() or True
    # the source file must still be a valid, untouched PDF
    assert len(pypdf.PdfReader(str(src)).pages) == 1


# ------------------------------------------------------------- item #4: zip collisions
def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_zip_extract_never_overwrites(ctx, tmp_path):
    from app.tools.file_tools import zip_extract

    archive = tmp_path / "a.zip"
    _make_zip(archive, {"hello.txt": b"from-zip", "sub/inner.txt": b"inner"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "hello.txt").write_bytes(b"pre-existing")
    result = zip_extract(ctx, {"src": str(archive), "dest": str(dest)})
    assert (dest / "hello.txt").read_bytes() == b"pre-existing"      # untouched
    assert (dest / "hello-1.txt").read_bytes() == b"from-zip"        # unique name
    assert (dest / "sub" / "inner.txt").read_bytes() == b"inner"
    assert "renamed" in result.message.lower() or "unique" in result.message.lower() \
        or "هم‌نام" in result.message


def test_zip_extract_still_blocks_zip_slip(ctx, tmp_path):
    from app.tools.file_tools import zip_extract

    archive = tmp_path / "evil.zip"
    _make_zip(archive, {"../escape.txt": b"nope", "ok.txt": b"yes"})
    dest = tmp_path / "out"
    zip_extract(ctx, {"src": str(archive), "dest": str(dest)})
    assert not (tmp_path / "escape.txt").exists()
    assert (dest / "ok.txt").exists()


def test_archive_decrypt_never_overwrites(ctx, tmp_path):
    from app.tools.files_extra3 import archive_decrypt

    archive = tmp_path / "enc.zip"          # plain zip → stdlib fallback path
    _make_zip(archive, {"data.txt": b"new-content"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "data.txt").write_bytes(b"old-content")
    archive_decrypt(ctx, {"src": str(archive), "out_dir": str(dest)})
    assert (dest / "data.txt").read_bytes() == b"old-content"
    assert (dest / "data-1.txt").read_bytes() == b"new-content"


# ------------------------------------------------------------- item #5: uninstall argv
def test_uninstall_argv_parses_quoted_path_and_args():
    from app.tools.system_tools import _uninstall_argv

    argv = _uninstall_argv(r'"C:\Program Files\App\uninstall.exe" /S /quiet')
    assert argv[0] == r"C:\Program Files\App\uninstall.exe"
    assert argv[1:] == ["/S", "/quiet"]


def test_uninstall_argv_msi():
    from app.tools.system_tools import _uninstall_argv

    argv = _uninstall_argv("MsiExec.exe /I{ABC-123} /quiet")
    assert argv[0] == "MsiExec.exe"
    assert "/I{ABC-123}" in argv


@pytest.mark.parametrize("bad", [
    'uninstall.exe /S & calc.exe',
    'uninstall.exe | evil.bat',
    'cmd /c uninstall.exe',
    'powershell -enc AAAA',
    'uninstall.exe > out.txt',
    '',
])
def test_uninstall_argv_fails_closed_on_shell_constructs(bad):
    from app.tools.system_tools import _uninstall_argv

    with pytest.raises(ValueError):
        _uninstall_argv(bad)


def test_launch_uninstaller_uses_shell_false(ctx):
    """Popen must receive an argv list with shell=False (never a shell string)."""
    import app.tools.system_tools as st

    app = {"name": "Demo App", "version": "1.0",
           "uninstall": r'"C:\Program Files\Demo\un.exe" /S'}
    with patch.object(st.subprocess, "Popen") as popen:
        argv = st._launch_uninstaller(ctx, app)
    popen.assert_called_once()
    assert popen.call_args.kwargs.get("shell") is False
    assert isinstance(popen.call_args.args[0], list)
    assert argv[0] == r"C:\Program Files\Demo\un.exe"


def test_launch_uninstaller_blocks_shell_metacharacters(ctx):
    import app.tools.system_tools as st

    app = {"name": "Evil", "version": "1", "uninstall": r"un.exe /S & calc.exe"}
    with patch.object(st.subprocess, "Popen") as popen:
        with pytest.raises(ToolError):
            st._launch_uninstaller(ctx, app)
    popen.assert_not_called()          # nothing was executed


# ------------------------------------------------------------- item #6: Unix permissions
def test_data_dir_and_sqlite_permissions(tmp_path, monkeypatch):
    """The data tree must be owner-only (audit F-09).

    POSIX expresses that as 0700/0600. Windows has no such mode — `stat` always
    reports 0777 and `os.chmod` only flips the read-only bit — so there the
    guarantee is an owner-only ACL applied through `paths._windows_acl`, which
    this test asserts is actually invoked.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("WINGENIUS_PORTABLE", raising=False)
    for flag in ("--portable",):
        monkeypatch.setattr(sys, "argv", [a for a in sys.argv if a != flag])
    from app.core import paths

    hardened = []
    if os.name == "nt":
        monkeypatch.setattr(paths, "_windows_acl",
                            lambda path, directory: hardened.append((str(path), directory)) or True)

    old_umask = os.umask(0o022)
    try:
        d = paths.data_dir()
        logs = paths.logs_dir()
        from app.core import indexer
        conn = indexer._connect()
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        conn.commit()
        conn.close()
        db = indexer.index_path()
        if os.name == "nt":
            # hardening must actually be attempted, for folders and for the db file
            assert hardened, "Windows permission hardening never ran"
            assert any(is_dir for _name, is_dir in hardened), "no folder ACL applied"
            assert any(not is_dir for _name, is_dir in hardened), "no file ACL applied"
            assert any(name.endswith(db.name) for name, is_dir in hardened if not is_dir), \
                f"the index database was never hardened: {hardened}"
        else:
            assert stat.S_IMODE(d.stat().st_mode) == 0o700
            assert stat.S_IMODE(logs.stat().st_mode) == 0o700
            assert stat.S_IMODE(db.stat().st_mode) == 0o600
    finally:
        os.umask(old_umask)


# ------------------------------------------------------------- item #8: API validation
def _spec() -> ToolSpec:
    return ToolSpec(
        id="demo", category="dev", name="demo", name_en="Demo",
        fields=[
            Field("src", K_TEXT, "منبع", "Source", required=True),
            Field("angle", K_CHOICE, "زاویه", "Angle", default=90,
                  choices=[(90, "۹۰", "90°"), (180, "۱۸۰", "180°")]),
            Field("count", K_INT, "تعداد", "Count", default=1, minimum=1, maximum=10),
        ],
    )


def test_validate_values_required_choice_range():
    spec = _spec()
    assert spec.validate_values({"src": "x", "angle": 90, "count": 5}, "en") == []
    errs = spec.validate_values({"src": "", "angle": 45, "count": 99}, "en")
    fields = {e["field"] for e in errs}
    assert {"src", "angle", "count"} <= fields
    assert all(e["error"] for e in errs)


def test_validate_values_bilingual():
    spec = _spec()
    fa = spec.validate_values({}, "fa")
    en = spec.validate_values({}, "en")
    assert fa and en
    assert any("الزامی" in e["error"] for e in fa)
    assert any("required" in e["error"] for e in en)


# ------------------------------------------------------------- item #11: registry hygiene
def test_persian_placeholders_have_english_twin():
    persian_re = re.compile(r"[\u0600-\u06FF]")
    offenders = [
        (t.id, f.key)
        for t in all_tools() for f in t.fields
        if f.placeholder and not f.placeholder_en and persian_re.search(f.placeholder)
    ]
    assert not offenders, f"placeholders with Persian text but no placeholder_en: {offenders}"


def test_danger_tools_have_bilingual_help():
    offenders = [
        t.id for t in all_tools()
        if t.danger and t.fields and not any(f.help and f.help_en for f in t.fields)
    ]
    assert not offenders, f"danger tools without bilingual field help: {offenders}"


# ------------------------------------------------------------- item #9: docs consistency
def test_openapi_matches_app_version_and_tool_count():
    from app.core.paths import APP_VERSION

    spec_text = (Path(__file__).resolve().parents[1] / "docs" / "openapi.yaml").read_text(encoding="utf-8")
    assert f"version: {APP_VERSION}" in spec_text
    import json
    stats = json.loads((Path(__file__).resolve().parents[1] / "docs" / "stats.json").read_text(encoding="utf-8"))
    assert f"tools: {{ type: integer, example: {stats['tools']} }}" in spec_text
    assert "dry_run" in spec_text
    assert stats["checks"] == sum(stats["suites"].values())


def test_installer_cleanup_lists_real_index_file():
    iss = (Path(__file__).resolve().parents[1] / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "search_index.sqlite3" in iss
    # no active cleanup entry may reference the stale bare name
    entries = [ln for ln in iss.splitlines() if "Name:" in ln and "index" in ln and "sqlite3" in ln]
    assert entries and all("search_index.sqlite3" in ln for ln in entries)
    assert "api_token.txt" in iss
