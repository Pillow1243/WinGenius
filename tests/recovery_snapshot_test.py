"""Regression tests for failed profile snapshots and migration."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from app.core import upgrade, updates


def test_snapshot_includes_committed_wal(tmp_path):
    source = tmp_path / "journal.sqlite3"
    conn = sqlite3.connect(source)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE entries(value TEXT)")
        conn.execute("INSERT INTO entries VALUES ('committed')")
        conn.commit()
        assert source.with_name(source.name + "-wal").exists()
        snapshot = tmp_path / "snapshot.db"
        upgrade.safe_copy_sqlite(source, snapshot)
        with sqlite3.connect(snapshot) as restored:
            assert restored.execute("SELECT value FROM entries").fetchall() == [("committed",)]
    finally:
        conn.close()


def test_snapshot_failure_does_not_fall_back_to_raw_copy(tmp_path, monkeypatch):
    source = tmp_path / "journal.sqlite3"
    source.write_bytes(b"not a database")
    snapshot = tmp_path / "snapshot.sqlite3"
    with pytest.raises(sqlite3.DatabaseError):
        upgrade.safe_copy_sqlite(source, snapshot)
    assert not snapshot.exists() or snapshot.read_bytes() != source.read_bytes()


def test_failed_backup_is_not_published_or_used_for_migration(tmp_path, monkeypatch):
    (tmp_path / "journal.sqlite3").write_bytes(b"broken")
    report = upgrade.migrate(tmp_path)
    assert not report["ok"]
    assert not report["backup"]
    assert upgrade.current_version(tmp_path) == 0
    assert list((tmp_path / "backups").iterdir()) == []
    assert "failed" in upgrade.summary(report, "en").lower()


def test_malformed_profile_is_preserved_in_backup_and_rescue(tmp_path):
    history = tmp_path / "history.json"
    history.write_text("{broken", encoding="utf-8")
    report = upgrade.migrate(tmp_path)
    assert report["ok"]
    assert (Path(report["backup"]) / "history.json").read_text(encoding="utf-8") == "{broken"
    assert (tmp_path / "history.json.broken").read_text(encoding="utf-8") == "{broken"
    assert json.loads(history.read_text(encoding="utf-8")) == []


def test_consecutive_backups_have_distinct_names(tmp_path):
    (tmp_path / "settings.ini").write_text("data", encoding="utf-8")
    first = upgrade.backup_data_dir(tmp_path)
    second = upgrade.backup_data_dir(tmp_path)
    assert first and second and first != second
    assert (first / "settings.ini").read_text(encoding="utf-8") == "data"
    assert (second / "settings.ini").read_text(encoding="utf-8") == "data"


def test_migration_step_problem_does_not_advance_stamp(tmp_path, monkeypatch):
    (tmp_path / "schema.json").write_text(json.dumps({"version": 2}), encoding="utf-8")
    def fail_step(root, report):
        report["problems"].append("write failed")
    monkeypatch.setitem(upgrade.MIGRATIONS, 2, fail_step)
    report = upgrade.migrate(tmp_path)
    assert not report["ok"] and report["steps"] == []
    assert upgrade.current_version(tmp_path) == 2


def test_migration_stamp_failure_is_reported(tmp_path, monkeypatch):
    def fail_stamp(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(upgrade, "write_stamp", fail_stamp)
    report = upgrade.migrate(tmp_path)
    assert not report["ok"]
    assert "stamp" in report["problems"][-1]
    assert upgrade.current_version(tmp_path) == 0


def test_update_preparation_aborts_if_backup_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade, "backup_data_dir", lambda *args, **kwargs: None)
    with pytest.raises(updates.UpdateError, match="profile backup"):
        updates.prepare_rollback_snapshot(target_version="8.0.0", install_mode="portable", root=tmp_path)
    assert not updates.rollback_state_path(tmp_path).exists()
