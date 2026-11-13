"""Data-folder versioning and migrations.

Everything WinGenius keeps between runs lives in one folder
(`%APPDATA%\\WinGenius` or `~/.config/WinGenius`): settings, history, the journal
database, macros, schedules, trash and snapshots.  As soon as one release changes
the shape of that data, the next release can silently corrupt a user's profile —
so the folder carries an explicit version stamp and every upgrade takes a backup
*before* touching anything.

    schema.json   {"version": 2, "updated": 1699999999, "history": [...]}

`migrate()` is called once at startup (and from `--doctor`), is safe to call
repeatedly, and never raises: a failed migration logs, restores nothing (the
backup is still there) and leaves the version stamp untouched so the same step
is retried next launch instead of being skipped.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import data_dir
from .privacy import PROFILE_JSON, atomic_json_write, sanitize_document, scrub_profile_file

DATA_VERSION = 3

STAMP_NAME = "schema.json"
BACKUP_DIR = "backups"
MAX_BACKUPS = 5

#: files/folders that must exist for the current version to work
REQUIRED_DIRS = ("logs", "trash", "journal_snapshots", "macros", "backups")

#: keys that older releases wrote and that are no longer read by anything
_OBSOLETE_SETTINGS = ("ui/compact", "ui/show_badges", "files/last_dir")


def stamp_path(root: Optional[Path] = None) -> Path:
    return (root or data_dir()) / STAMP_NAME


def read_stamp(root: Optional[Path] = None) -> Dict[str, Any]:
    path = stamp_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def current_version(root: Optional[Path] = None) -> int:
    """Version of the data folder. Fresh installs report 0, not 1."""
    stamp = read_stamp(root)
    try:
        return int(stamp.get("version") or 0)
    except (TypeError, ValueError):
        return 0


def looks_populated(root: Optional[Path] = None) -> bool:
    """True when there is data from an older release (settings/journal/etc.)."""
    base = root or data_dir()
    markers = ("settings.ini", "history.json", "journal.sqlite3", "schedules.json")
    return any((base / name).exists() for name in markers)


def needs_upgrade(root: Optional[Path] = None) -> bool:
    return current_version(root) < DATA_VERSION


def safe_copy_sqlite(src: Path, dst: Path) -> None:
    """Snapshot a database with SQLite, including committed WAL contents.

    A raw file copy is not a safe fallback for a live WAL database. Propagate
    errors so callers cannot mistake an incomplete snapshot for a backup.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    uri = src.resolve().as_uri() + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as src_conn:
        with contextlib.closing(sqlite3.connect(str(dst))) as dst_conn:
            src_conn.backup(dst_conn)


def backup_data_dir(root: Optional[Path] = None, reason: str = "upgrade") -> Optional[Path]:
    """Create a completed profile snapshot, or return None on failure.

    Staging directories are never returned as backups. Other files are copied
    individually; this is not a globally atomic snapshot of concurrent writers.
    """
    base = root or data_dir()
    target_root = base / BACKUP_DIR
    staging: Optional[Path] = None
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=target_root))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = target_root / f"{reason}-v{current_version(base)}-{stamp}-{uuid.uuid4().hex[:12]}"
        for item in base.iterdir():
            if item.name in {BACKUP_DIR, "api_token.txt"} or item.is_symlink():
                continue
            if item.name.endswith((".sqlite3-wal", ".sqlite3-shm")):
                continue
            if item.is_dir():
                # Do not follow nested links outside the profile.
                def ignore_links(folder, names):
                    return [name for name in names if (Path(folder) / name).is_symlink()]

                shutil.copytree(item, staging / item.name, ignore=ignore_links)
            elif item.name in PROFILE_JSON:
                # Refuse malformed profiles rather than claiming an empty backup.
                raw = json.loads(item.read_text(encoding="utf-8"))
                atomic_json_write(staging / item.name, sanitize_document(raw))
            elif item.suffix == ".sqlite3":
                safe_copy_sqlite(item, staging / item.name)
            else:
                shutil.copy2(item, staging / item.name)
        os.rename(staging, target)
        staging = None
    except (OSError, sqlite3.Error, ValueError):
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        return None
    _prune_backups(base)
    return target


def _prune_backups(root: Path) -> None:
    folder = root / BACKUP_DIR
    try:
        entries = sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".incomplete-")),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for stale in entries[MAX_BACKUPS:]:
        shutil.rmtree(stale, ignore_errors=True)


# ------------------------------------------------------------------- migrate
def _v0_to_v1(root: Path, report: Dict[str, Any]) -> None:
    """A folder with data but no stamp predates versioning — just count it."""
    report["notes"].append("legacy profile detected")


def _v1_to_v2(root: Path, report: Dict[str, Any]) -> None:
    """v2: required folders exist, obsolete settings dropped, history JSON checked."""
    for name in REQUIRED_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)

    settings_path = root / "settings.ini"
    if settings_path.is_file():
        try:
            lines = settings_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        kept = [line for line in lines
                if not any(line.strip().startswith(key.replace("/", "\\") + "=")
                           or line.strip().startswith(key + "=") for key in _OBSOLETE_SETTINGS)]
        if len(kept) != len(lines):
            report["notes"].append(f"dropped {len(lines) - len(kept)} obsolete setting(s)")
            try:
                settings_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
            except OSError:
                report["problems"].append("could not rewrite settings.ini")

    history_path = root / "history.json"
    if history_path.is_file():
        try:
            json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rescue = history_path.with_suffix(".json.broken")
            try:
                history_path.replace(rescue)
                history_path.write_text("[]", encoding="utf-8")
                report["notes"].append("history.json was unreadable — reset (old copy kept)")
            except OSError:
                report["problems"].append("could not repair history.json")


def _v2_to_v3(root: Path, report: Dict[str, Any]) -> None:
    """Remove saved credentials without keeping a new unsanitised backup."""
    for name in PROFILE_JSON:
        path = root / name
        if path.is_file() and scrub_profile_file(path):
            report["notes"].append(f"removed sensitive inputs from {name}; re-enter at runtime")
    # Previous local migration snapshots may also contain the old profile JSON.
    folder = root / BACKUP_DIR
    if folder.is_dir():
        for path in folder.rglob("*.json"):
            if path.name not in PROFILE_JSON or path.is_symlink():
                continue
            try:
                scrub_profile_file(path)
            except (OSError, ValueError):
                report["notes"].append(f"review an unreadable old backup manually: {path.name}")
    report["notes"].append("External backups, macros and arbitrary text are not scrubbed; review them manually")


MIGRATIONS = {0: _v0_to_v1, 1: _v1_to_v2, 2: _v2_to_v3}


def write_stamp(root: Optional[Path] = None, history: Optional[List[Dict[str, Any]]] = None) -> None:
    base = root or data_dir()
    previous = read_stamp(base)
    payload = {
        "version": DATA_VERSION,
        "updated": time.time(),
        "history": (previous.get("history") or []) + (history or []),
    }
    atomic_json_write(stamp_path(base), payload)


def migrate(root: Optional[Path] = None, logger=None) -> Dict[str, Any]:
    """Bring the data folder up to DATA_VERSION. Never raises."""
    base = root or data_dir()
    report: Dict[str, Any] = {"from": current_version(base), "to": DATA_VERSION,
                              "steps": [], "notes": [], "problems": [],
                              "backup": "", "ok": True}
    if not needs_upgrade(base):
        report["from"] = DATA_VERSION
        return report

    if looks_populated(base):
        backup = backup_data_dir(base, reason="before-upgrade")
        report["backup"] = str(backup) if backup else ""
        if backup is None:
            report["problems"].append("could not create a backup before upgrading")
            report["ok"] = False
            if logger:
                logger.warning("upgrade aborted: no backup")
            return report

    version = current_version(base)
    while version < DATA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            report["problems"].append(f"no migration defined from version {version}")
            report["ok"] = False
            break
        try:
            step(base, report)
        except Exception as exc:                    # a migration must never kill startup
            report["problems"].append(f"migration {version}→{version + 1} failed: {exc}")
            report["ok"] = False
            if logger:
                logger.error("migration %s→%s failed: %s", version, version + 1, exc)
            break
        if report["problems"]:
            report["ok"] = False
            break
        report["steps"].append(f"{version}→{version + 1}")
        version += 1

    if report["ok"]:
        try:
            write_stamp(base, [{"from": report["from"], "to": DATA_VERSION, "at": time.time(),
                                "steps": report["steps"]}])
        except OSError as exc:
            report["problems"].append(f"could not write migration stamp: {exc}")
            report["ok"] = False
    if logger and report["ok"] and report["steps"]:
        logger.info("data migrated %s (backup: %s)", ", ".join(report["steps"]),
                    report["backup"] or "none")
    return report


def summary(report: Dict[str, Any], lang: str = "fa") -> str:
    """One-paragraph human summary, used by --doctor and the settings panel."""
    fa = lang != "en"
    if not report.get("ok", True):
        prefix = "ارتقای داده‌ها ناموفق بود: " if fa else "Data upgrade failed: "
        return prefix + "; ".join(report.get("problems") or [])
    if not report.get("steps"):
        return ("پرونده‌ی داده‌ها به‌روز است (نسخه‌ی " + str(DATA_VERSION) + ")." if fa
                else f"Data folder is up to date (version {DATA_VERSION}).")
    text = ("داده‌ها از نسخه‌ی {} به {} ارتقا یافت؛ پشتیبان: {}" if fa
            else "Data upgraded from {} to {}; backup: {}")
    text = text.format(report.get("from"), DATA_VERSION, report.get("backup") or "—")
    if report.get("problems"):
        text += (" · هشدار: " if fa else " · warning: ") + "; ".join(report["problems"])
    return text
