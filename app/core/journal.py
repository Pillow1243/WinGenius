"""Undo journal — every destructive operation becomes reversible.

The journal is a small SQLite database (`journal.sqlite3` in the app data
folder).  Tools record what they are about to change, and `undo` replays the
change backwards.  Supporting pieces:

* `Journal.record(...)`  — append an operation with a machine-readable payload
* `Journal.undo_last()`  — reverse the most recent operation (optionally N)
* `send_to_trash(paths)` — the "safe delete" primitive: files move into the
  app trash folder (not the OS one) so they can always be restored
* `snapshot(path)`       — keep a copy of a file's previous bytes before a
  tool overwrites it, so "undo" can bring the old content back
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .paths import data_dir
from .utils import human_size, unique_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    tool_id     TEXT    NOT NULL DEFAULT '',
    kind        TEXT    NOT NULL,
    title_fa    TEXT    NOT NULL DEFAULT '',
    title_en    TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL DEFAULT '{}',
    undone      INTEGER NOT NULL DEFAULT 0,
    undone_ts   REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_operations_ts ON operations(ts DESC);
"""

SCHEMA_VERSION = 2

#: SQL applied when an older database is opened. Each entry upgrades *to* its key.
MIGRATIONS = {
    1: ("ALTER TABLE operations ADD COLUMN undone INTEGER NOT NULL DEFAULT 0",),
    2: ("ALTER TABLE operations ADD COLUMN undone_ts REAL NOT NULL DEFAULT 0",),
}

KIND_RENAME = "rename"
KIND_COPY = "copy"
KIND_WRITE = "write"
KIND_TRASH = "trash"
KIND_MKDIR = "mkdir"
KIND_CUSTOM = "custom"

UNDOABLE_KINDS = {KIND_RENAME, KIND_COPY, KIND_WRITE, KIND_TRASH, KIND_MKDIR}


def journal_path() -> Path:
    return data_dir() / "journal.sqlite3"


def trash_dir() -> Path:
    path = data_dir() / "trash"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshots_dir() -> Path:
    path = data_dir() / "journal_snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Operation:
    id: int
    uid: str
    ts: float
    tool_id: str
    kind: str
    title_fa: str
    title_en: str
    payload: Dict[str, Any]
    undone: bool

    def title(self, lang: str = "fa") -> str:
        if lang == "en":
            return self.title_en or self.title_fa or f"operation #{self.id}"
        return self.title_fa or self.title_en or f"عملیات #{self.id}"

    @property
    def undoable(self) -> bool:
        return self.kind in UNDOABLE_KINDS and not self.undone

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "uid": self.uid, "ts": self.ts, "tool_id": self.tool_id,
            "kind": self.kind, "title_fa": self.title_fa, "title_en": self.title_en,
            "undone": self.undone, "payload": self.payload,
        }


class Journal:
    """Process-wide undo journal."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else journal_path()
        self._lock = threading.Lock()
        self._ensure()

    # ------------------------------------------------------------------ storage
    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.makedirs(self._path.parent, exist_ok=True)
        existed = self._path.exists()
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        if not existed:
            # Somebody removed the journal while we were running (a cleanup tool, a
            # user emptying the data folder, a half-restored backup). Rebuild the
            # schema instead of failing with "no such table: operations".
            try:
                conn.executescript(SCHEMA)
                self._migrate(conn)
                conn.commit()
            except sqlite3.DatabaseError:
                pass
        return conn

    def _ensure(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                self._migrate(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> List[str]:
        """Bring an old journal database up to SCHEMA_VERSION.

        `PRAGMA user_version` rides along in the file, so a database written by an
        older release can be upgraded in place instead of throwing "no such column"
        the next time the user presses Ctrl+Z.
        """
        applied: List[str] = []
        version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        if version >= SCHEMA_VERSION:
            return applied
        for target in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS.get(target, ()):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            applied.append(f"{target - 1}→{target}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._schema_version = SCHEMA_VERSION
        return applied

    def schema_version(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                return int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            finally:
                conn.close()

    def clear(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM operations")
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ writing
    def record(self, kind: str, title_fa: str, title_en: str = "", *,
               tool_id: str = "", payload: Optional[Dict[str, Any]] = None) -> int:
        """Append an operation. Returns its row id (0 when it was a no-op)."""
        payload = payload or {}
        if kind == KIND_CUSTOM and not payload:
            return 0
        if kind in (KIND_RENAME, KIND_COPY, KIND_WRITE, KIND_TRASH, KIND_MKDIR) and not payload:
            return 0
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "INSERT INTO operations (uid, ts, tool_id, kind, title_fa, title_en, payload)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], time.time(), tool_id, kind,
                     title_fa, title_en, json.dumps(payload, ensure_ascii=False)))
                conn.commit()
                return int(cursor.lastrowid or 0)
            finally:
                conn.close()

    # --- convenience recorders (used by the tools) ---------------------------
    def record_moves(self, moves: Iterable[tuple], title_fa: str, title_en: str = "",
                     tool_id: str = "", kind: str = KIND_RENAME) -> int:
        pairs = [[str(src), str(dst)] for src, dst in moves]
        if not pairs:
            return 0
        return self.record(kind, title_fa, title_en, tool_id=tool_id, payload={"moves": pairs})

    def record_created(self, paths: Iterable[str], title_fa: str, title_en: str = "",
                       tool_id: str = "") -> int:
        files = [str(p) for p in paths]
        if not files:
            return 0
        return self.record(KIND_COPY, title_fa, title_en, tool_id=tool_id,
                           payload={"created": files})

    def record_write(self, path: str, backup: Optional[str], existed: bool,
                     title_fa: str, title_en: str = "", tool_id: str = "") -> int:
        return self.record(KIND_WRITE, title_fa, title_en, tool_id=tool_id,
                           payload={"path": str(path), "backup": backup or "", "existed": existed})

    def record_file_backup(self, path: Path, title_fa: str, title_en: str = "",
                           tool_id: str = "", limit_mb: int = 512) -> int:
        """Copy `path` aside *now* so the change a tool is about to make is undoable.

        Returns the new operation id, or 0 when no copy could be taken (missing
        file, larger than `limit_mb`, I/O error).  Callers use the 0 to tell the
        user that this particular change will not be reversible.
        """
        backup = self.snapshot(Path(path), limit_mb=limit_mb)
        if backup is None:
            return 0
        return self.record_write(str(path), backup, True, title_fa, title_en, tool_id)

    def record_trash(self, items: Iterable[tuple], title_fa: str, title_en: str = "",
                     tool_id: str = "") -> int:
        pairs = [[str(orig), str(stored)] for orig, stored in items]
        if not pairs:
            return 0
        return self.record(KIND_TRASH, title_fa, title_en, tool_id=tool_id,
                           payload={"items": pairs})

    def record_mkdirs(self, dirs: Iterable[str], title_fa: str, title_en: str = "",
                      tool_id: str = "") -> int:
        paths = [str(d) for d in dirs]
        if not paths:
            return 0
        return self.record(KIND_MKDIR, title_fa, title_en, tool_id=tool_id, payload={"dirs": paths})

    def snapshot(self, path: Path, limit_mb: int = 32) -> Optional[str]:
        """Copy a file's current bytes aside before overwriting it."""
        path = Path(path)
        try:
            if not path.is_file():
                return None
            if path.stat().st_size > limit_mb * 1024 * 1024:
                return None
            target = snapshots_dir() / f"{uuid.uuid4().hex[:12]}__{path.name}"
            shutil.copy2(path, target)
            return str(target)
        except OSError:
            return None

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _row_to_op(row: sqlite3.Row) -> Operation:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return Operation(
            id=int(row["id"]), uid=row["uid"], ts=float(row["ts"]), tool_id=row["tool_id"],
            kind=row["kind"], title_fa=row["title_fa"], title_en=row["title_en"],
            payload=payload, undone=bool(row["undone"]))

    def entries(self, limit: int = 60, include_undone: bool = True) -> List[Operation]:
        query = "SELECT * FROM operations"
        if not include_undone:
            query += " WHERE undone = 0"
        query += " ORDER BY id DESC LIMIT ?"
        conn = self._connect()
        try:
            return [self._row_to_op(row) for row in conn.execute(query, (int(limit),))]
        finally:
            conn.close()

    def last_undoable(self) -> Optional[Operation]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM operations WHERE undone = 0 ORDER BY id DESC LIMIT 1").fetchone()
            return self._row_to_op(row) if row else None
        finally:
            conn.close()

    def get(self, op_id: int) -> Optional[Operation]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM operations WHERE id = ?", (int(op_id),)).fetchone()
            return self._row_to_op(row) if row else None
        finally:
            conn.close()

    def stats(self) -> Dict[str, Any]:
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) AS n FROM operations").fetchone()["n"]
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM operations WHERE undone = 0").fetchone()["n"]
            kinds = {row["kind"]: row["n"] for row in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM operations GROUP BY kind")}
        finally:
            conn.close()
        return {"total": int(total), "pending": int(pending),
                "undone": int(total) - int(pending), "kinds": kinds}

    # ------------------------------------------------------------------- undo
    def _mark_undone(self, op_id: int) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE operations SET undone = 1, undone_ts = ? WHERE id = ?",
                             (time.time(), int(op_id)))
                conn.commit()
            finally:
                conn.close()

    def undo_one(self, op: Operation) -> Dict[str, Any]:
        """Reverse a single operation. Never raises."""
        restored: List[str] = []
        problems: List[str] = []
        try:
            if op.kind == KIND_RENAME:
                for src, dst in op.payload.get("moves", []):
                    src_path, dst_path = Path(src), Path(dst)
                    if not dst_path.exists():
                        problems.append(f"missing: {dst}")
                        continue
                    try:
                        src_path.parent.mkdir(parents=True, exist_ok=True)
                        target = src_path if not src_path.exists() else unique_path(src_path)
                        shutil.move(str(dst_path), str(target))
                        restored.append(str(target))
                    except OSError as exc:
                        problems.append(f"{dst}: {exc}")
            elif op.kind == KIND_COPY:
                for created in op.payload.get("created", []):
                    path = Path(created)
                    try:
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        elif path.exists():
                            path.unlink()
                        restored.append(str(path))
                    except OSError as exc:
                        problems.append(f"{created}: {exc}")
            elif op.kind == KIND_WRITE:
                path = Path(op.payload.get("path", ""))
                backup = op.payload.get("backup") or ""
                existed = bool(op.payload.get("existed"))
                try:
                    if backup and Path(backup).is_file():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup, path)
                        restored.append(str(path))
                    elif not existed and path.exists():
                        path.unlink()
                        restored.append(str(path))
                    else:
                        problems.append(f"no backup for {path}")
                except OSError as exc:
                    problems.append(f"{path}: {exc}")
            elif op.kind == KIND_TRASH:
                for orig, stored in op.payload.get("items", []):
                    stored_path = Path(stored)
                    if not stored_path.exists():
                        problems.append(f"missing in trash: {stored}")
                        continue
                    try:
                        target = unique_path(Path(orig))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(stored_path), str(target))
                        restored.append(str(target))
                    except OSError as exc:
                        problems.append(f"{orig}: {exc}")
            elif op.kind == KIND_MKDIR:
                for folder in sorted(op.payload.get("dirs", []), key=len, reverse=True):
                    path = Path(folder)
                    try:
                        if path.is_dir() and not any(path.iterdir()):
                            path.rmdir()
                            restored.append(str(path))
                    except OSError as exc:
                        problems.append(f"{folder}: {exc}")
            else:
                problems.append(f"{op.kind}: not undoable")
        except Exception as exc:                     # never let undo crash the app
            problems.append(str(exc))

        if restored and not problems:
            self._mark_undone(op.id)
            status = "done"
        elif restored:
            self._mark_undone(op.id)
            status = "partial"
        else:
            status = "failed"
        return {"id": op.id, "status": status, "restored": restored, "problems": problems,
                "operation": op}

    def undo_last(self, count: int = 1) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for _ in range(max(1, int(count))):
            op = self.last_undoable()
            if op is None:
                break
            results.append(self.undo_one(op))
        return results

    def purge_snapshots(self, older_than_days: int = 14) -> int:
        cutoff = time.time() - older_than_days * 86400
        removed = 0
        for item in snapshots_dir().glob("*"):
            try:
                if item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
            except OSError:
                pass
        return removed


# --------------------------------------------------------------------- helpers
_JOURNAL: Optional[Journal] = None


def journal() -> Journal:
    """Shared journal instance."""
    global _JOURNAL
    if _JOURNAL is None:
        _JOURNAL = Journal()
    return _JOURNAL


def reset_journal(path: Optional[Path] = None) -> Journal:
    """Used by tests to point the journal at a throw-away database."""
    global _JOURNAL
    _JOURNAL = Journal(path)
    return _JOURNAL


def send_to_trash(paths: Iterable[Path], storage: Optional[Path] = None) -> List[tuple]:
    """Move files into the app trash. Returns [(original, stored)] pairs."""
    root = Path(storage) if storage else trash_dir()
    root.mkdir(parents=True, exist_ok=True)
    batch = root / uuid.uuid4().hex[:10]
    batch.mkdir(parents=True, exist_ok=True)
    pairs: List[tuple] = []
    for item in paths:
        path = Path(item)
        if not path.exists():
            continue
        target = unique_path(batch / path.name)
        shutil.move(str(path), str(target))
        pairs.append((path, target))
    return pairs


def trash_size() -> int:
    total = 0
    for item in trash_dir().rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def describe(operation: Operation, lang: str = "fa") -> str:
    """Human sentence for the journal list."""
    kind_labels = {
        KIND_RENAME: ("جابه‌جایی/تغییر نام", "move/rename"),
        KIND_COPY: ("فایل‌های ساخته‌شده", "created files"),
        KIND_WRITE: ("نوشتن فایل", "file write"),
        KIND_TRASH: ("انتقال به سطل", "moved to trash"),
        KIND_MKDIR: ("پوشه‌های ساخته‌شده", "created folders"),
        KIND_CUSTOM: ("یادداشت", "note"),
    }
    label = kind_labels.get(operation.kind, (operation.kind, operation.kind))
    payload_size = 0
    for key in ("moves", "created", "items", "dirs"):
        payload_size += len(operation.payload.get(key, []) or [])
    suffix = f" · {payload_size}" if payload_size else ""
    stamp = time.strftime("%H:%M", time.localtime(operation.ts))
    return f"{operation.title(lang)} ({label[1] if lang == 'en' else label[0]}{suffix}) · {stamp}"


def human_trash() -> str:
    return human_size(trash_size())
