"""Job engine: queue, parallel execution, progress, cancellation, history.

The UI never blocks: every tool run becomes a `Job` handled here.  Up to
`max_parallel` jobs run at once; the rest wait in the queue.
"""
from __future__ import annotations

import time
from copy import deepcopy
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .execution import ExecutionPolicy
from .history import history
from .logger import get_logger
from .registry import get as get_tool
from .spec import RunResult, ToolSpec
from .worker import ToolRunner

log = get_logger("jobs")

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    tool_id: str
    title: str
    values: Dict[str, Any] = field(default_factory=dict)
    lang: str = "fa"
    output_dir: str = ""
    status: str = QUEUED
    progress: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    outputs: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    result: Optional[RunResult] = None
    policy: Optional[ExecutionPolicy] = None

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        end = self.finished or time.time()
        return max(0.0, end - self.started)

    @property
    def active(self) -> bool:
        return self.status in (QUEUED, RUNNING)

    def percent(self) -> int:
        if self.status == DONE:
            return 100
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.progress * 100 / self.total)))


class JobManager(QObject):
    """Owns every running tool; exposes signals the shell listens to."""

    jobAdded = Signal(object)
    jobUpdated = Signal(object)
    jobFinished = Signal(object)
    changed = Signal()

    def __init__(self, max_parallel: int = 3, parent=None) -> None:
        super().__init__(parent)
        self.max_parallel = max(1, int(max_parallel))
        self._jobs: Dict[str, Job] = {}
        self._runners: Dict[str, ToolRunner] = {}
        self._running: List[str] = []
        self._lang = "fa"
        self._output_dir = ""
        self._closed = False

    # ------------------------------------------------------------------ config
    def configure(self, lang: str, output_dir: str) -> None:
        self._lang = lang
        self._output_dir = output_dir

    # ------------------------------------------------------------------- query
    def jobs(self) -> List[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created, reverse=True)

    def active_jobs(self) -> List[Job]:
        return [job for job in self.jobs() if job.active]

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def has_active(self) -> bool:
        return any(job.active for job in self._jobs.values())

    def stats(self) -> Dict[str, int]:
        """Small counter dict used by the shell's status bar."""
        counts: Dict[str, int] = {QUEUED: 0, RUNNING: 0, DONE: 0, FAILED: 0, CANCELLED: 0}
        for job in self._jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        counts["total"] = len(self._jobs)
        counts["active"] = len(self.active_jobs())
        return counts

    def recent_outputs(self, limit: int = 10) -> List[str]:
        out: List[str] = []
        for job in sorted(self._jobs.values(), key=lambda item: -item.created):
            for path in job.outputs:
                if path not in out:
                    out.append(path)
            if len(out) >= limit:
                break
        return out[:limit]

    # ------------------------------------------------------------------ submit
    def submit(self, spec: ToolSpec, values: Dict[str, Any], lang: str = "",
               output_dir: str = "", title: str = "",
               policy: Optional[ExecutionPolicy] = None) -> Job:
        if self._closed:
            raise RuntimeError("job manager is shut down")
        base_policy = policy or ExecutionPolicy.for_gui(
            confirmed=True, record_history=False, source="queue"
        )
        if base_policy.record_history:
            base_policy = ExecutionPolicy(
                source=base_policy.source,
                max_risk=base_policy.max_risk,
                confirmed=base_policy.confirmed,
                dry_run=base_policy.dry_run,
                record_history=False,
            )
        job = Job(
            id=uuid.uuid4().hex[:12],
            tool_id=spec.id,
            title=title or spec.name_for(lang or self._lang),
            values=deepcopy(values or {}),
            lang=lang or self._lang,
            output_dir=output_dir or self._output_dir,
            policy=base_policy,
        )
        self._jobs[job.id] = job
        self.jobAdded.emit(job)
        self.changed.emit()
        self._pump()
        return job

    def submit_by_id(self, tool_id: str, values: Dict[str, Any],
                     policy: Optional[ExecutionPolicy] = None) -> Optional[Job]:
        spec = get_tool(tool_id)
        if spec is None:
            return None
        return self.submit(spec, values, policy=policy)

    def _pump(self) -> None:
        if self._closed:
            return
        while len(self._running) < self.max_parallel:
            pending = [job for job in self.jobs() if job.status == QUEUED]
            if not pending:
                return
            job = sorted(pending, key=lambda item: item.created)[0]
            self._start(job)

    def _start(self, job: Job) -> None:
        spec = get_tool(job.tool_id)
        if spec is None:
            job.status = FAILED
            job.error = f"unknown tool: {job.tool_id}"
            self.jobFinished.emit(job)
            return
        runner = ToolRunner(spec, job.values, job.lang, job.output_dir, self, policy=job.policy)
        job.status = RUNNING
        job.started = time.time()
        job.message = ""
        self._runners[job.id] = runner
        self._running.append(job.id)
        runner.progressed.connect(lambda current, total, message, jid=job.id:
                                  self._on_progress(jid, current, total, message))
        runner.messaged.connect(lambda message, jid=job.id: self._on_message(jid, message))
        runner.succeeded.connect(lambda result, jid=job.id: self._on_success(jid, result))
        runner.failed.connect(lambda message, trace, jid=job.id: self._on_failed(jid, message, trace))
        runner.cancelled.connect(lambda jid=job.id: self._on_cancelled(jid))
        runner.finished.connect(lambda jid=job.id: self._on_thread_finished(jid))
        self.jobUpdated.emit(job)
        runner.start()

    # ---------------------------------------------------------------- callbacks
    def _on_progress(self, job_id: str, current: int, total: int, message: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.progress = current
        job.total = total
        if message:
            job.message = message
        self.jobUpdated.emit(job)

    def _on_message(self, job_id: str, message: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.message = message
            self.jobUpdated.emit(job)

    def _on_success(self, job_id: str, result: RunResult) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = DONE
        job.result = result
        job.outputs = list(result.outputs or [])
        job.message = result.message
        job.finished = time.time()
        history.add(job)
        self.jobFinished.emit(job)
        self.jobUpdated.emit(job)
        self.changed.emit()

    def _on_failed(self, job_id: str, message: str, trace: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = FAILED
        job.error = message
        job.finished = time.time()
        log.warning("job %s failed: %s", job.tool_id, message)
        history.add(job)
        self.jobFinished.emit(job)
        self.jobUpdated.emit(job)
        self.changed.emit()

    def _on_cancelled(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = CANCELLED
        job.finished = time.time()
        self.jobFinished.emit(job)
        self.jobUpdated.emit(job)
        self.changed.emit()

    def _on_thread_finished(self, job_id: str) -> None:
        runner = self._runners.pop(job_id, None)
        if runner is not None:
            runner.deleteLater()
        if job_id in self._running:
            self._running.remove(job_id)
        self._pump()

    # ----------------------------------------------------------------- control
    def cancel(self, job_id: str) -> None:
        runner = self._runners.get(job_id)
        job = self._jobs.get(job_id)
        if runner is not None:
            runner.cancel()
        elif job is not None and job.status == QUEUED:
            job.status = CANCELLED
            job.finished = time.time()
            self.jobFinished.emit(job)
            self.changed.emit()

    def cancel_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.active:
                self.cancel(job.id)

    def clear_finished(self) -> None:
        for job_id in [job.id for job in self._jobs.values() if not job.active]:
            self._jobs.pop(job_id, None)
        self.changed.emit()

    def shutdown(self, timeout_ms: int = 4000) -> None:
        self._closed = True
        for runner in list(self._runners.values()):
            try:
                runner.cancel()
                runner.wait(timeout_ms)
            except Exception:
                pass
        self._runners.clear()
        self._running.clear()
