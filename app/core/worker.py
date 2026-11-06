"""Background execution of a tool with progress / cancel / ExecutionService support."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal

from .execution import ExecutionPolicy, execution_service
from .logger import get_logger
from .privacy import redact_known, scrub_text, secret_strings
from .spec import Cancelled, RunContext, RunResult, ToolError, ToolSpec

log = get_logger("worker")


class ToolRunner(QThread):
    """Runs `ExecutionService.execute(spec, values, ctx)` off the GUI thread."""

    progressed = Signal(int, int, str)
    messaged = Signal(str)
    succeeded = Signal(object)          # RunResult
    failed = Signal(str, str)           # (message, traceback)
    cancelled = Signal()

    def __init__(
        self,
        spec: ToolSpec,
        values: Dict[str, Any],
        lang: str,
        output_dir: str,
        parent=None,
        *,
        policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec
        self.values = dict(values)
        self.lang = lang
        self.output_dir = output_dir
        self.policy = policy or ExecutionPolicy.for_gui(confirmed=True, record_history=True)
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
            policy=self.policy,
        )
        secrets_list = secret_strings(self.spec.id, self.values)
        try:
            if self.spec.run is None:
                raise ToolError("tool has no implementation")
            result = execution_service.execute(
                self.spec, self.values, ctx, policy=self.policy
            )
            if result is None:
                result = RunResult()
            if not isinstance(result, RunResult):
                result = RunResult(message=str(result))
            # A late cancel request does not undo side effects already committed.
            # Only the cooperative Cancelled exception means the tool stopped.
            self.succeeded.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except ToolError as exc:
            msg = scrub_text(redact_known(str(exc), secrets_list))
            log.info("tool %s failed: %s", self.spec.id, msg)
            self.failed.emit(msg, "")
        except Exception as exc:  # pragma: no cover - defensive
            tb = scrub_text(redact_known(traceback.format_exc(), secrets_list))
            msg = scrub_text(redact_known(f"{type(exc).__name__}: {exc}", secrets_list))
            log.error("tool %s crashed: %s\n%s", self.spec.id, msg, tb)
            self.failed.emit(msg, tb)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progressed.emit(current, total, message)
