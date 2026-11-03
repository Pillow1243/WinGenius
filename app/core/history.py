"""Persistent run history (JSON in the app data folder)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .logger import get_logger
from .paths import data_dir
from .privacy import atomic_json_write, sanitize_document, sanitize_values, redact_known, secret_strings

log = get_logger("history")
MAX_ENTRIES = 150


@dataclass
class HistoryEntry:
    tool_id: str
    title: str
    status: str
    when: float
    duration: float = 0.0
    outputs: List[str] = field(default_factory=list)
    message: str = ""
    values: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["outputs"] = list(self.outputs or [])[:20]
        # only keep json-friendly values for re-running
        clean: Dict[str, Any] = {}
        for key, value in (self.values or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                clean[key] = value
            elif isinstance(value, (list, tuple)):
                clean[key] = [str(item) for item in value][:50]
        data["values"] = sanitize_values(self.tool_id, clean)
        data["message"] = redact_known(data["message"], secret_strings(self.tool_id, self.values))
        return data


class History:
    def __init__(self) -> None:
        self._path = data_dir() / "history.json"
        self._entries: List[HistoryEntry] = []
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            original = json.loads(self._path.read_text(encoding="utf-8"))
            raw = sanitize_document(original)
            if raw != original:
                atomic_json_write(self._path, raw)
            for item in raw:
                self._entries.append(HistoryEntry(
                    tool_id=item.get("tool_id", ""),
                    title=item.get("title", ""),
                    status=item.get("status", "done"),
                    when=float(item.get("when") or 0),
                    duration=float(item.get("duration") or 0),
                    outputs=[str(p) for p in item.get("outputs", [])],
                    message=item.get("message", ""),
                    values=item.get("values", {}) or {},
                ))
        except Exception as exc:  # pragma: no cover - corrupt file should never crash us
            log.warning("cannot read history: %s", exc)
            self._entries = []

    def _save(self) -> None:
        try:
            payload = [entry.as_dict() for entry in self._entries[:MAX_ENTRIES]]
            atomic_json_write(self._path, payload)
        except Exception as exc:  # pragma: no cover
            log.warning("cannot write history: %s", exc)

    # --------------------------------------------------------------- public api
    def add(self, job) -> None:
        entry = HistoryEntry(
            tool_id=getattr(job, "tool_id", ""),
            title=getattr(job, "title", ""),
            status=getattr(job, "status", ""),
            when=time.time(),
            duration=job.elapsed if hasattr(job, "elapsed") else 0.0,
            outputs=list(getattr(job, "outputs", []) or []),
            message=(getattr(job, "message", "") or getattr(job, "error", ""))[:400],
            values=dict(getattr(job, "values", {}) or {}),
        )
        entry.message = redact_known(entry.message, secret_strings(entry.tool_id, entry.values))
        entry.values = sanitize_values(entry.tool_id, entry.values)
        self._entries.insert(0, entry)
        del self._entries[MAX_ENTRIES:]
        self._save()

    def entries(self, limit: int = 20) -> List[HistoryEntry]:
        return self._entries[:limit]

    def for_tool(self, tool_id: str, limit: int = 5) -> List[HistoryEntry]:
        return [entry for entry in self._entries if entry.tool_id == tool_id][:limit]

    def recent_outputs(self, limit: int = 12) -> List[str]:
        seen: List[str] = []
        for entry in self._entries:
            for output in entry.outputs:
                if output not in seen and Path(output).exists():
                    seen.append(output)
                if len(seen) >= limit:
                    return seen
        return seen

    def clear(self) -> None:
        self._entries.clear()
        self._save()


history = History()
