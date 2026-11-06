"""Job engine: queue, parallel execution, progress, cancellation, history.

The UI never blocks: every tool run becomes a `Job` handled here.  Up to
`max_parallel` jobs run at once; the rest wait in the queue.

The queue survives a restart: queued/running jobs are written to
`queue.json` in the data folder and come back *paused* on the next launch, so
closing the app never throws work away and nothing starts in the background
without the user pressing "resume".
"""
from __future__ import annotations

import json
import time
from copy import deepcopy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .execution import ExecutionPolicy
from .history import history
from .logger import get_logger
from .paths import data_dir
from .privacy import atomic_json_write, is_sensitive
from .registry import get as get_tool
from .spec import RunResult, ToolSpec
from .worker import ToolRunner

log = get_logger("jobs")

QUEUE_FILE = "queue.json"
#: A restored queue longer than this is truncated to the newest entries.
PERSIST_LIMIT = 50

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
    #: restored from disk but deliberately not started yet (user must resume)
    paused: bool = False
    #: restored without its secret inputs (passwords are never written to disk)
    needs_secrets: bool = False

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


# --------------------------------------------------------------- persistence
def queue_path() -> Path:
    """Location of the persisted queue."""
    return data_dir() / QUEUE_FILE


def _jsonable(value: Any) -> Any:
    """Convert tool values into something json.dump can always write."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=str)]
    if isinstance(value, bytes):
        return ""                                  # never persist raw bytes
    return str(value)


def job_to_dict(job: "Job") -> Dict[str, Any]:
    """Serialise one active job for `queue.json`.

    Secret fields (passwords, tokens, API keys) are *dropped*, not masked: the
    user has to type them again after a restart, which is why such a job comes
    back flagged `needs_secrets`.
    """
    values: Dict[str, Any] = {}
    needs_secrets = False
    for key, value in (job.values or {}).items():
        if is_sensitive(str(key), job.tool_id):
            needs_secrets = True
            continue
        values[str(key)] = _jsonable(value)
    return {
        "id": job.id,
        "tool_id": job.tool_id,
        "title": job.title,
        "values": values,
        "needs_secrets": needs_secrets,
        "lang": job.lang,
        "output_dir": job.output_dir,
        "status": job.status,
        "progress": int(job.progress),
        "total": int(job.total),
        "message": job.message[:400],
        "created": float(job.created),
    }


def job_from_dict(item: Dict[str, Any]) -> Optional["Job"]:
    """Rebuild a paused job from disk; None when the entry is unusable."""
    tool_id = str(item.get("tool_id") or "").strip()
    if not tool_id:
        return None
    spec = get_tool(tool_id)
    if spec is None:                               # tool removed/renamed since
        log.info("dropping queued job for unknown tool %s", tool_id)
        return None
    values = item.get("values")
    if not isinstance(values, dict):
        values = {}
    return Job(
        id=str(item.get("id") or uuid.uuid4().hex[:12]),
        tool_id=tool_id,
        title=str(item.get("title") or spec.name_for(str(item.get("lang") or "fa"))),
        values=values,
        lang="en" if item.get("lang") == "en" else "fa",
        output_dir=str(item.get("output_dir") or ""),
        status=QUEUED,                             # a run that was interrupted restarts
        progress=int(item.get("progress") or 0),
        total=int(item.get("total") or 0),
        message=str(item.get("message") or ""),
        created=float(item.get("created") or time.time()),
        paused=True,
        needs_secrets=bool(item.get("needs_secrets")),
        policy=ExecutionPolicy.for_gui(
            confirmed=not spec.danger, record_history=False, source="queue-restored"
        ),
    )


class JobManager(QObject):
    """Owns every running tool; exposes signals the shell listens to."""

    jobAdded = Signal(object)
    jobUpdated = Signal(object)
    jobFinished = Signal(object)
    changed = Signal()

    def __init__(self, max_parallel: int = 3, parent=None, *,
                 path: Optional[Path] = None, restore: bool = True) -> None:
        super().__init__(parent)
        self.max_parallel = max(1, int(max_parallel))
        self._jobs: Dict[str, Job] = {}
        self._runners: Dict[str, ToolRunner] = {}
        self._running: List[str] = []
        self._lang = "fa"
        self._output_dir = ""
        self._closed = False
        self._path = Path(path) if path is not None else queue_path()
        if restore:
            self._load()

    # ------------------------------------------------------------- persistence
    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        """Bring the queue back from the previous session (paused, never started)."""
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:                  # corrupt file must not block startup
            log.warning("cannot read the saved queue: %s", exc)
            return
        if not isinstance(raw, list):
            log.warning("saved queue is not a list; ignoring it")
            return
        restored = 0
        for item in raw[:PERSIST_LIMIT]:
            if not isinstance(item, dict):
                continue
            job = job_from_dict(item)
            if job is None or job.id in self._jobs:
                continue
            self._jobs[job.id] = job
            restored += 1
        if restored:
            log.info("restored %d queued job(s) from the previous session", restored)

    def _save(self) -> None:
        """Write the active jobs to `queue.json` (atomic, best effort)."""
        if self._closed and not self._runners:
            return
        active = [job for job in self.jobs() if job.active][:PERSIST_LIMIT]
        try:
            atomic_json_write(self._path, [job_to_dict(job) for job in active])
        except Exception as exc:                  # pragma: no cover - disk problems
            log.warning("cannot write the queue: %s", exc)

    def _persist_pending(self, jobs: List["Job"]) -> None:
        """Persist a snapshot of jobs that were still active at shutdown."""
        payload = [dict(job_to_dict(job), status=QUEUED) for job in jobs[:PERSIST_LIMIT]]
        try:
            atomic_json_write(self._path, payload)
        except Exception as exc:                  # pragma: no cover
            log.warning("cannot write the queue: %s", exc)

    def has_resumable(self) -> bool:
        """True when jobs came back from a previous session and wait for a resume."""
        return any(job.status == QUEUED and job.paused for job in self._jobs.values())

    def resumable(self) -> List["Job"]:
        return [job for job in self.jobs() if job.status == QUEUED and job.paused]

    def resume_pending(self) -> int:
        """Start the jobs restored from disk. Returns how many were resumed."""
        resumed = 0
        for job in self.resumable():
            job.paused = False
            resumed += 1
            self.jobUpdated.emit(job)
        if resumed:
            log.info("resumed %d job(s)", resumed)
            self._save()
            self.changed.emit()
            self._pump()
        return resumed

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
        self._save()
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
        attempted: set[str] = set()
        while len(self._running) < self.max_parallel:
            pending = [job for job in self.jobs()
                       if job.status == QUEUED and not job.paused and job.id not in attempted]
            if not pending:
                return
            job = sorted(pending, key=lambda item: item.created)[0]
            # Guard against a job that never leaves QUEUED (unknown tool, a
            # runner that refuses to start): each job is tried once per pump.
            attempted.add(job.id)
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
        self._save()
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
        self._save()
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
        self._save()
        self.jobFinished.emit(job)
        self.jobUpdated.emit(job)
        self.changed.emit()

    def _on_cancelled(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = CANCELLED
        job.finished = time.time()
        self._save()
        self.jobFinished.emit(job)
        self.jobUpdated.emit(job)
        self.changed.emit()

    def _on_thread_finished(self, job_id: str) -> None:
        runner = self._runners.pop(job_id, None)
        if runner is not None:
            runner.deleteLater()
        if job_id in self._running:
            self._running.remove(job_id)
        self._save()
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
            self._save()
            self.jobFinished.emit(job)
            self.changed.emit()

    def cancel_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.active:
                self.cancel(job.id)

    def clear_finished(self) -> None:
        for job_id in [job.id for job in self._jobs.values() if not job.active]:
            self._jobs.pop(job_id, None)
        self._save()
        self.changed.emit()

    def shutdown(self, timeout_ms: int = 4000) -> bool:
        # Jobs that are still queued or running keep their place in the saved
        # queue: closing the app must not silently drop them.
        pending = [job for job in self.jobs() if job.active]
        self._closed = True
        self.cancel_all()
        self._persist_pending(pending)
        stopped = True
        for runner in list(self._runners.values()):
            try:
                runner.cancel()
                if not runner.wait(timeout_ms):
                    stopped = False
            except Exception:
                stopped = False
        # Keep live QThreads owned until their finished signals are delivered.
        if not stopped:
            self._closed = False
        return stopped
