"""Background execution of a tool with progress / cancel support."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict

from PySide6.QtCore import QThread, Signal

from .logger import get_logger
from .spec import Cancelled, RunContext, RunResult, ToolError, ToolSpec

log = get_logger("worker")


class ToolRunner(QThread):
    """Runs `spec.run(ctx, values)` off the GUI thread."""

    progressed = Signal(int, int, str)
    messaged = Signal(str)
    succeeded = Signal(object)          # RunResult
    failed = Signal(str, str)           # (message, traceback)
    cancelled = Signal()

    def __init__(self, spec: ToolSpec, values: Dict[str, Any], lang: str, output_dir: str, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.values = dict(values)
        self.lang = lang
        self.output_dir = output_dir
        self._event = threading.Event()

    # ------------------------------------------------------------------ api
    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    # ---------------------------------------------------------------- thread
    def run(self) -> None:  # noqa: D401 (Qt entry point)
        ctx = RunContext(
            lang=self.lang,
            output_dir=self.output_dir,
            on_progress=self._on_progress,
            on_message=self.messaged.emit,
            cancel_event=self._event,
        )
        try:
            if self.spec.run is None:
                raise ToolError("tool has no implementation")
            result = self.spec.run(ctx, self.values)
            if result is None:
                result = RunResult()
            if not isinstance(result, RunResult):
                result = RunResult(message=str(result))
            if self._event.is_set():
                self.cancelled.emit()
                return
            self.succeeded.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except ToolError as exc:
            log.info("tool %s failed: %s", self.spec.id, exc)
            self.failed.emit(str(exc), "")
        except Exception as exc:  # pragma: no cover - defensive
            tb = traceback.format_exc()
            log.error("tool %s crashed: %s\n%s", self.spec.id, exc, tb)
            self.failed.emit(f"{type(exc).__name__}: {exc}", tb)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progressed.emit(current, total, message)
