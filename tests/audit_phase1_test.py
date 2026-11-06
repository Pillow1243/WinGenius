"""Regression tests for the 2026-11 independent audit — Phase 1 findings.

F-01  watcher must honour the settle window (no instant processing)
F-02  excluded folders must be pruned from the search index on rebuild
F-03  macros must never silently overwrite an existing file (undoable)
F-04  cron must OR day-of-month and day-of-week when both are restricted
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")


# --------------------------------------------------------------------- F-01
class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def watcher_env(tmp_path, monkeypatch):
    """A Watcher with its own rule store, seen file and a controllable clock."""
    from PySide6.QtWidgets import QApplication

    from app.core import watcher as watcher_mod

    clock = _FakeClock()
    monkeypatch.setattr(watcher_mod, "time", clock)          # watcher uses time.time()
    store = watcher_mod.WatchStore()
    store._path = tmp_path / "watch_rules.json"
    store._loaded = True
    monkeypatch.setattr(watcher_mod, "store", store)
    # parent the QObject to the app: a parentless QObject GC'd during interpreter
    # finalization trips a known PySide6/CPython-3.11 refcount bug (3.11 is not a
    # supported runtime; CI runs 3.12/3.13).
    watcher = watcher_mod.Watcher(interval_ms=60_000, parent=QApplication.instance())
    watcher._seen_path = tmp_path / "watch_seen.json"
    watcher._seen = {}
    watcher._pending = {}
    yield watcher_mod, store, watcher, clock, tmp_path
    watcher.stop()
    watcher.deleteLater()
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.sendPostedEvents(None, 0)   # flush deferred deletes before shutdown


def _rule(store, folder, settle=2.0, pattern="*.txt"):
    rule = store.new()
    rule.folder = str(folder)
    rule.pattern = pattern
    rule.settle = settle
    rule.action = "tool"
    rule.target_id = "notepad"
    return store.save(rule)


def test_f01_first_scan_does_not_fire_immediately(watcher_env):
    mod, store, watcher, clock, tmp = watcher_env
    folder = tmp / "inbox"
    folder.mkdir()
    (folder / "a.txt").write_text("hello", encoding="utf-8")
    _rule(store, folder, settle=2.0)
    assert watcher.scan_now() == [], "first scan must only register the file as pending"


def test_f01_hit_only_after_settle_and_exactly_once(watcher_env):
    mod, store, watcher, clock, tmp = watcher_env
    folder = tmp / "inbox"
    folder.mkdir()
    target = folder / "a.txt"
    target.write_text("hello", encoding="utf-8")
    _rule(store, folder, settle=2.0)
    assert watcher.scan_now() == []
    clock.advance(1.0)
    assert watcher.scan_now() == [], "still inside the settle window"
    clock.advance(1.5)
    hits = watcher.scan_now()
    assert [key for _rule, key in hits] == [str(target)], "exactly one hit after settle"
    assert watcher.scan_now() == [], "no repeat hit without a change"


def test_f01_file_change_restarts_settle_window(watcher_env):
    mod, store, watcher, clock, tmp = watcher_env
    folder = tmp / "inbox"
    folder.mkdir()
    target = folder / "a.txt"
    target.write_text("v1", encoding="utf-8")
    _rule(store, folder, settle=2.0)
    watcher.scan_now()                                   # pending starts
    clock.advance(1.8)
    target.write_text("v2-changed", encoding="utf-8")    # growth resets the timer
    os.utime(target, (clock.now + 5, clock.now + 5))
    assert watcher.scan_now() == [], "a changed file must restart its settle window"
    clock.advance(2.5)
    hits = watcher.scan_now()
    assert [key for _r, key in hits] == [str(target)]
    # changing again after it was processed re-arms it (no early trigger)
    clock.advance(0.5)
    target.write_text("v3", encoding="utf-8")
    os.utime(target, (clock.now + 9, clock.now + 9))
    assert watcher.scan_now() == []
    clock.advance(2.5)
    assert len(watcher.scan_now()) == 1


def test_f01_pending_does_not_grow_for_missing_files(watcher_env):
    mod, store, watcher, clock, tmp = watcher_env
    folder = tmp / "inbox"
    folder.mkdir()
    target = folder / "gone.txt"
    target.write_text("x", encoding="utf-8")
    _rule(store, folder, settle=2.0)
    watcher.scan_now()
    assert str(target) in watcher._pending
    target.unlink()
    watcher.scan_now()
    watcher.scan_now()
    assert str(target) not in watcher._pending, "deleted files must leave the pending map"
    # a disabled rule must not accumulate pending entries either
    rule = store.rules()[0]
    rule.enabled = False
    store.save(rule)
    (folder / "b.txt").write_text("y", encoding="utf-8")
    watcher.scan_now()
    assert str(folder / "b.txt") not in watcher._pending


# --------------------------------------------------------------------- F-02
def _settings():
    from app.core.settings import Settings

    return Settings()


def test_f02_excluded_folder_is_pruned_from_index(tmp_path):
    from app.core import indexer

    folder = tmp_path / "docs"
    secret_dir = folder / "private"
    secret_dir.mkdir(parents=True)
    (folder / "public.txt").write_text("alpha beta gamma public", encoding="utf-8")
    (secret_dir / "secret.txt").write_text("alpha beta gamma secret", encoding="utf-8")

    indexer.clear()
    indexer.build([str(folder)])
    assert len(indexer.search("alpha")) == 2

    _settings().set("index/exclude_folders", str(secret_dir))
    indexer.build([str(folder)])                          # rebuild
    hits = indexer.search("alpha")
    assert [h.path for h in hits] == [str((folder / "public.txt").resolve())], \
        "excluded folder content must be pruned from the FTS index"


def test_f02_sensitive_files_are_pruned_and_never_indexed(tmp_path):
    from app.core import indexer

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "ok.txt").write_text("unique-token-aaa", encoding="utf-8")
    key_file = folder / "id_rsa"
    key_file.write_text("unique-token-bbb", encoding="utf-8")

    indexer.clear()
    indexer.build([str(folder)])
    # the sensitive file must never have been indexed in the first place
    assert [h.name for h in indexer.search("unique-token")] == ["ok.txt"]

    # …and an already-indexed sensitive record is pruned on the next rebuild
    conn = indexer._connect()
    key = str(key_file.resolve())
    conn.execute("INSERT OR REPLACE INTO docs(path, mtime, size, chars, added, language)"
                 " VALUES(?,1,1,10,1,'en')", (key,))
    conn.execute("INSERT INTO docs_fts(path, body) VALUES(?,?)", (key, "unique-token-bbb"))
    conn.commit()
    conn.close()
    assert any(h.name == "id_rsa" for h in indexer.search("unique-token"))
    indexer.build([str(folder)])
    assert not any(h.name == "id_rsa" for h in indexer.search("unique-token"))
    assert [h.name for h in indexer.search("unique-token")] == ["ok.txt"]


def test_f02_temporarily_unreadable_file_is_not_pruned(tmp_path, monkeypatch):
    from app.core import indexer

    folder = tmp_path / "docs"
    folder.mkdir()
    target = folder / "keep.txt"
    target.write_text("keep-token-ccc", encoding="utf-8")
    indexer.clear()
    indexer.build([str(folder)])
    assert [h.name for h in indexer.search("keep-token")] == ["keep.txt"]

    # simulate a transient stat failure (AV lock, network drive hiccup)
    real_stat = Path.stat

    def flaky_stat(self, *a, **k):
        if self == target:
            raise OSError("temporarily unavailable")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    indexer.build([str(folder)])
    monkeypatch.undo()
    assert [h.name for h in indexer.search("keep-token")] == ["keep.txt"], \
        "a transient read error must not prune a valid index record"


# --------------------------------------------------------------------- F-03
@pytest.fixture()
def scratch_journal(tmp_path):
    from app.core import journal as journal_module

    previous = journal_module._JOURNAL
    created = journal_module.reset_journal(tmp_path / "journal.sqlite3")
    yield created
    journal_module._JOURNAL = previous


def _run_macro(macro, tmp_path, dry_run=False):
    from app.core.macro import MacroRunner

    runner = MacroRunner(lang="en", output_dir=str(tmp_path), dry_run=dry_run)
    return runner.execute(macro)


def test_f03_dir_copy_does_not_overwrite_existing_file(tmp_path, scratch_journal):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "sub").mkdir(parents=True)
    dst.mkdir()
    (src / "data.txt").write_text("NEW-CONTENT", encoding="utf-8")
    (src / "sub" / "inner.txt").write_text("NEW-INNER", encoding="utf-8")
    (dst / "data.txt").write_text("ORIGINAL-IMPORTANT", encoding="utf-8")

    macro = {"name": "copytest", "steps": [{"type": "copy", "src": str(src), "dst": str(dst)}]}
    result = _run_macro(macro, tmp_path)
    assert result.ok, result.errors
    assert (dst / "data.txt").read_text(encoding="utf-8") == "ORIGINAL-IMPORTANT", \
        "an existing destination file must never be silently overwritten"
    # the new content landed under a unique name and was journalled
    names = sorted(p.name for p in dst.iterdir())
    assert "data.txt" in names and any(n.startswith("data-") for n in names if n.endswith(".txt"))
    assert any("inner.txt" in n for n in names) or (dst / "sub" / "inner.txt").exists()
    ops = scratch_journal.entries()
    assert any(op.kind == "copy" for op in ops), "created files must be journalled"


def test_f03_write_existing_file_is_undoable(tmp_path, scratch_journal):
    target = tmp_path / "notes.txt"
    target.write_text("ORIGINAL-IMPORTANT", encoding="utf-8")
    macro = {"name": "writetest",
             "steps": [{"type": "write", "path": str(target), "text": "NEW"}]}
    result = _run_macro(macro, tmp_path)
    assert result.ok, result.errors
    assert target.read_text(encoding="utf-8") == "NEW"
    assert scratch_journal.stats()["pending"] >= 1, "the write must be journalled"
    undo = scratch_journal.undo_last(1)[0]
    assert undo["status"] == "done", undo["problems"]
    assert target.read_text(encoding="utf-8") == "ORIGINAL-IMPORTANT", "undo restores content"


def test_f03_dry_run_writes_nothing_and_reports_collisions(tmp_path, scratch_journal):
    target = tmp_path / "notes.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "notes.txt").write_text("NEW", encoding="utf-8")
    macro = {"name": "dry",
             "steps": [{"type": "copy", "src": str(src), "dst": str(tmp_path)},
                       {"type": "write", "path": str(target), "text": "NEW"}]}
    result = _run_macro(macro, tmp_path, dry_run=True)
    assert result.status == "dry-run"
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    assert not (tmp_path / "notes-1.txt").exists()
    assert scratch_journal.stats()["total"] == 0, "dry-run must not journal real changes"
    assert any("would" in line for line in result.log)


def test_f03_cancel_leaves_no_partial_state(tmp_path, scratch_journal):
    import threading

    from app.core.macro import MacroRunner

    cancel = threading.Event()
    cancel.set()
    target = tmp_path / "notes.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    runner = MacroRunner(lang="en", output_dir=str(tmp_path), cancel_event=cancel)
    from app.core.spec import Cancelled

    with pytest.raises(Cancelled):
        runner.execute({"name": "c", "steps": [{"type": "write", "path": str(target), "text": "X"}]})
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


# --------------------------------------------------------------------- F-04
def test_f04_dom_and_dow_are_ored_when_both_restricted():
    from app.core.scheduler import matches, next_run

    expr = "0 9 1 * 1"                       # 09:00 on the 1st OR any Monday
    assert matches(expr, datetime(2026, 6, 1, 9, 0)), "the 1st (a Monday) matches"
    assert matches(expr, datetime(2026, 6, 8, 9, 0)), "a plain Monday must match (OR)"
    assert matches(expr, datetime(2026, 7, 1, 9, 0)), "the 1st of July (Wednesday) matches"
    assert not matches(expr, datetime(2026, 6, 9, 9, 0)), "a plain Tuesday must not match"
    assert not matches(expr, datetime(2026, 6, 8, 10, 0)), "wrong hour must not match"
    # next_run after 2026-06-02 must be Monday 2026-06-08, not 2027-02-01
    nxt = next_run(expr, datetime(2026, 6, 2, 12, 0))
    assert nxt == datetime(2026, 6, 8, 9, 0), nxt


def test_f04_single_restricted_day_field_keeps_and_semantics():
    from app.core.scheduler import matches, next_run

    assert matches("0 9 1 * *", datetime(2026, 6, 1, 9, 0))
    assert not matches("0 9 1 * *", datetime(2026, 6, 8, 9, 0))
    assert matches("0 9 * * 1", datetime(2026, 6, 8, 9, 0))
    assert not matches("0 9 * * 1", datetime(2026, 6, 9, 9, 0))
    assert next_run("0 9 1 * *", datetime(2026, 6, 2)) == datetime(2026, 7, 1, 9, 0)
    assert next_run("0 9 * * 1", datetime(2026, 6, 2)) == datetime(2026, 6, 8, 9, 0)


def test_f04_next_run_always_matches_and_leap_years_work():
    from app.core.scheduler import matches, next_run, upcoming

    for expr in ("0 9 1 * 1", "0 9 29 2 *", "*/15 * * * *", "0 0 31 * *", "30 18 * * 0-4"):
        for moment in upcoming(expr, 8, after=datetime(2026, 1, 1)):
            assert matches(expr, moment), f"{expr} next_run returned non-matching {moment}"
    assert matches("0 9 29 2 *", datetime(2028, 2, 29, 9, 0)), "leap day"
    assert not matches("0 9 29 2 *", datetime(2027, 2, 28, 9, 0))
    # 31st of February never happens: next_run must skip to a real month
    assert next_run("0 9 31 2 *", datetime(2026, 1, 1)) is None
    assert next_run("0 9 31 * *", datetime(2026, 2, 1)) == datetime(2026, 3, 31, 9, 0)


def test_f04_weekday_aliases_mon_to_sun():
    from app.core.scheduler import matches, parse_cron

    assert parse_cron("0 9 * * MON")["weekday"] == {1}
    assert parse_cron("0 9 * * sun")["weekday"] == {0}
    assert parse_cron("0 9 * * MON-FRI")["weekday"] == {1, 2, 3, 4, 5}
    assert matches("0 9 * * MON", datetime(2026, 6, 8, 9, 0))
    assert not matches("0 9 * * MON", datetime(2026, 6, 9, 9, 0))


def test_f04_describe_cron_mentions_the_or_case():
    from app.core.scheduler import describe_cron

    fa = describe_cron("0 9 1 * 1", "fa")
    en = describe_cron("0 9 1 * 1", "en")
    assert "یا" in fa, fa
    assert "or" in en.lower(), en


# --------------------------------------------------------------------- F-10
# The project requires Python >= 3.12; on CPython 3.11 + PySide6 the interpreter
# aborts in a C-extension dealloc *during shutdown GC* after this test's 4500
# signal emissions (ci.yml documents 3.11 as an unsupported matrix cell for the
# same reason). CI runs 3.12/3.13 where the test executes fully.
@pytest.mark.skipif(__import__("sys").version_info < (3, 12),
                    reason="PySide6 on CPython 3.11 crashes at shutdown GC; supported runtimes are 3.12/3.13")
def test_f10_seen_cap_covers_full_scan_across_restart(watcher_env):
    """4500 files: scan → persist → reload → scan again must not reprocess."""
    mod, store, watcher, clock, tmp = watcher_env
    folder = tmp / "big"
    folder.mkdir()
    for i in range(4500):
        (folder / f"f{i:05d}.txt").write_text("x", encoding="utf-8")
    _rule(store, folder, settle=0.0)

    assert watcher.scan_now() == []          # first scan only arms pending
    clock.advance(0.1)
    hits = watcher.scan_now()                # settle=0 → everything fires once
    assert len(hits) == 4500
    assert len(watcher._seen) == 4500
    watcher._save_seen()

    # simulate a restart: drop in-memory state and reload the persisted seen map
    watcher._seen = {}
    watcher._pending = {}
    watcher._load_seen()
    assert len(watcher._seen) == 4500, "persist trim must not drop scanned files"
    assert watcher.scan_now() == [], "no reprocessing of unchanged files after restart"
