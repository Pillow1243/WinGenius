"""Watch folders and run a tool / workflow whenever new files appear.

Rules are stored in the app data folder, so they survive restarts.  A rule says:

    "when a *.pdf appears in C:\\Inbox  ->  run workflow pdf_to_images_zip"

The watcher keeps a fingerprint of everything it has already handled, which
means restarting the app never re-processes old files.
"""
from __future__ import annotations

import fnmatch
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .logger import get_logger
from .privacy import sanitize_values
from .paths import data_dir

log = get_logger("watcher")
DEFAULT_PATTERNS = "*.*"


# ---------------------------------------------------------------------- rule
@dataclass
class WatchRule:
    id: str
    name: str = ""
    folder: str = ""
    pattern: str = "*.pdf"
    action: str = "workflow"          # workflow | tool
    target_id: str = ""
    values: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    recursive: bool = False
    settle: float = 1.5               # seconds a file must stop growing
    created: float = field(default_factory=time.time)
    last_run: float = 0.0
    runs: int = 0
    last_file: str = ""
    last_status: str = ""

    def title(self, lang: str = "fa") -> str:
        if self.name:
            return self.name
        return Path(self.folder).name or self.folder or ("قانون" if lang == "fa" else "Rule")

    def summary(self, lang: str = "fa") -> str:
        target = self.target_id or "-"
        if lang == "en":
            return f"{self.pattern} in {self.folder} → {target}"
        return f"{self.pattern} در {self.folder} ← {target}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "folder": self.folder, "pattern": self.pattern,
            "action": self.action, "target_id": self.target_id, "values": sanitize_values(self.target_id, self.values),
            "enabled": self.enabled, "recursive": self.recursive, "settle": self.settle,
            "created": self.created, "last_run": self.last_run, "runs": self.runs,
            "last_file": self.last_file, "last_status": self.last_status,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "WatchRule":
        return WatchRule(
            id=str(data.get("id") or uuid.uuid4().hex[:10]),
            name=str(data.get("name") or ""),
            folder=str(data.get("folder") or ""),
            pattern=str(data.get("pattern") or "*.pdf"),
            action=str(data.get("action") or "workflow"),
            target_id=str(data.get("target_id") or ""),
            values=dict(data.get("values") or {}),
            enabled=bool(data.get("enabled", True)),
            recursive=bool(data.get("recursive", False)),
            settle=float(data.get("settle") or 1.5),
            created=float(data.get("created") or time.time()),
            last_run=float(data.get("last_run") or 0),
            runs=int(data.get("runs") or 0),
            last_file=str(data.get("last_file") or ""),
            last_status=str(data.get("last_status") or ""),
        )

    def matches(self, path: Path) -> bool:
        name = path.name.lower()
        for part in (self.pattern or DEFAULT_PATTERNS).replace(";", ",").split(","):
            part = part.strip().lower()
            if not part:
                continue
            if fnmatch.fnmatch(name, part) or fnmatch.fnmatch(name, part.lstrip("*.")):
                return True
        return False


# --------------------------------------------------------------------- store
class WatchStore:
    def __init__(self) -> None:
        self._path: Optional[Path] = None
        self._rules: List[WatchRule] = []
        self._loaded = False

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = data_dir() / "watch_rules.json"
        return self._path

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._rules = [WatchRule.from_dict(item) for item in raw]
        except Exception as exc:  # pragma: no cover - corrupt file must not crash
            log.warning("cannot read watch rules: %s", exc)
            self._rules = []

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps([rule.as_dict() for rule in self._rules],
                                            ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            log.warning("cannot write watch rules: %s", exc)

    # ------------------------------------------------------------------ api
    def rules(self) -> List[WatchRule]:
        self._load()
        return list(self._rules)

    def get(self, rule_id: str) -> Optional[WatchRule]:
        for rule in self.rules():
            if rule.id == rule_id:
                return rule
        return None

    def save(self, rule: WatchRule) -> WatchRule:
        self._load()
        for index, existing in enumerate(self._rules):
            if existing.id == rule.id:
                self._rules[index] = rule
                break
        else:
            self._rules.insert(0, rule)
        self._save()
        return rule

    def delete(self, rule_id: str) -> bool:
        self._load()
        before = len(self._rules)
        self._rules = [rule for rule in self._rules if rule.id != rule_id]
        if len(self._rules) != before:
            self._save()
            return True
        return False

    def new(self) -> WatchRule:
        return WatchRule(id=uuid.uuid4().hex[:10], folder=str(Path.home()))


store = WatchStore()


def read_folder(folder: str, pattern: str = "*.*", recursive: bool = False,
                limit: int = 5000) -> List[Path]:
    """List files matching a rule (never raises)."""
    root = Path(folder)
    if not folder or not root.exists():
        return []
    found: List[Path] = []
    probe = WatchRule(id="probe", pattern=pattern or DEFAULT_PATTERNS)
    try:
        iterator = root.rglob("*") if recursive else root.glob("*")
        for path in iterator:
            if len(found) >= limit:
                break
            try:
                if not path.is_file() or not probe.matches(path):
                    continue
                found.append(path)
            except OSError:
                continue
    except OSError as exc:
        log.warning("cannot read %s: %s", folder, exc)
    return found


# ------------------------------------------------------------------- watcher
class Watcher(QObject):
    """Polls the folders of every enabled rule and reports new files."""

    triggered = Signal(object, str)      # (rule, path)
    changed = Signal()

    def __init__(self, interval_ms: int = 4000, parent=None) -> None:
        super().__init__(parent)
        self._seen: Dict[str, float] = {}
        self._pending: Dict[str, float] = {}
        self._seen_path = data_dir() / "watch_seen.json"
        self._load_seen()
        self._timer = QTimer(self)
        self._timer.setInterval(max(1000, int(interval_ms)))
        self._timer.timeout.connect(self.scan_now)
        self.interval_ms = max(1000, int(interval_ms))

    # ------------------------------------------------------------------- io
    def _load_seen(self) -> None:
        try:
            if self._seen_path.exists():
                raw = json.loads(self._seen_path.read_text(encoding="utf-8"))
                self._seen = {str(key): float(value) for key, value in raw.items()}
        except Exception:  # pragma: no cover
            self._seen = {}

    def _save_seen(self) -> None:
        try:
            trimmed = dict(sorted(self._seen.items(), key=lambda item: -item[1])[:4000])
            self._seen = trimmed
            self._seen_path.write_text(json.dumps(trimmed), encoding="utf-8")
        except Exception:  # pragma: no cover
            pass

    # ---------------------------------------------------------------- control
    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self, interval_ms: Optional[int] = None) -> None:
        if interval_ms:
            self.interval_ms = max(1000, int(interval_ms))
            self._timer.setInterval(self.interval_ms)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def prime(self, rule: WatchRule) -> int:
        """Remember the current files of a rule so only *new* ones fire."""
        count = 0
        for path in read_folder(rule.folder, rule.pattern, rule.recursive):
            key = str(path)
            try:
                self._seen[key] = path.stat().st_mtime
            except OSError:
                self._seen[key] = time.time()
            count += 1
        self._save_seen()
        return count

    # ------------------------------------------------------------------ scan
    def scan_now(self) -> List[tuple]:
        """Check every rule once; returns [(rule, path), ...] for new files."""
        hits: List[tuple] = []
        for rule in store.rules():
            if not rule.enabled or not rule.folder:
                continue
            for path in read_folder(rule.folder, rule.pattern, rule.recursive):
                key = str(path)
                try:
                    mtime = path.stat().st_mtime
                    size = path.stat().st_size
                except OSError:
                    continue
                if size == 0:
                    continue
                known = self._seen.get(key)
                if known == mtime:
                    continue
                # let the file stop growing before acting on it
                pending_since = self._pending.get(key)
                if pending_since is None:
                    self._pending[key] = time.time()
                elif time.time() - pending_since < max(0.0, rule.settle):
                    continue
                self._pending.pop(key, None)
                self._seen[key] = mtime
                hits.append((rule, key))
                self.triggered.emit(rule, key)
        if hits:
            self._save_seen()
        return hits

    def forget(self, path: str) -> None:
        self._seen.pop(str(path), None)
        self._save_seen()

    def reset(self) -> None:
        self._seen = {}
        self._pending = {}
        self._save_seen()


def describe_hits(hits: List[tuple], lang: str = "fa") -> str:
    if not hits:
        return "هیچ فایل تازه‌ای پیدا نشد." if lang != "en" else "No new files."
    lines = []
    for rule, path in hits:
        lines.append(f"• {rule.title(lang)}: {Path(path).name}")
    return "\n".join(lines)


def mark_run(rule: WatchRule, path: str, status: str) -> None:
    """Remember that a rule handled a file (shown in the automation sheet)."""
    rule.runs += 1
    rule.last_run = time.time()
    rule.last_file = str(path)
    rule.last_status = status
    store.save(rule)
