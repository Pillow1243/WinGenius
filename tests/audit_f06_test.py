"""F-06 regression test — 2026-11 independent audit.

Restored jobs whose secrets were dropped must stay paused until the
user re-supplies them, and secrets must be scrubbed once the job finishes.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-06
def _queue_manager(tmp_path, restore=False):
    from app.core.jobs import JobManager

    manager = JobManager(path=tmp_path / "queue.json", restore=restore)
    manager._start = lambda job: None          # never actually run tools
    return manager


def test_f06_secret_job_stays_paused_and_runs_after_resupply(tmp_path):
    from app.core.jobs import JobManager
    from app.core.registry import get, load_all

    load_all()
    queue = tmp_path / "queue.json"
    first = JobManager(path=queue, restore=False)
    first._start = lambda job: None
    first.submit(get("archive_decrypt"),
                 {"src": str(tmp_path / "a.zip"), "password": "super-secret-pw", "out_dir": str(tmp_path)})

    raw = queue.read_text(encoding="utf-8")
    assert "super-secret-pw" not in raw
    assert json.loads(raw)[0]["needs_secrets"] is True

    second = JobManager(path=queue)
    second._start = lambda job: None
    job = second.jobs()[0]
    assert job.paused and job.needs_secrets
    # resume must NOT start a job whose secrets are missing
    assert second.resume_pending() == 0
    assert job.paused and job.needs_secrets and job.status == "queued"

    # supplying the secret clears the flag and resumes the job
    assert second.provide_secrets(job.id, {"password": "super-secret-pw"}) is True
    assert job.needs_secrets is False
    assert job.paused is False
    assert job.values.get("password") == "super-secret-pw"      # in memory only
    raw = queue.read_text(encoding="utf-8")
    assert "super-secret-pw" not in raw, "secret must never be persisted"
    # non-sensitive keys are rejected by provide_secrets
    before = dict(job.values)
    assert second.provide_secrets(job.id, {"src": "/etc/passwd"}) is False
    assert job.values == before


def test_f06_plain_job_resumes_normally(tmp_path):
    from app.core.jobs import JobManager
    from app.core.registry import get, load_all

    load_all()
    queue = tmp_path / "queue.json"
    first = JobManager(path=queue, restore=False)
    first._start = lambda job: None
    first.submit(get("pdf_merge"), {"src": ["x.pdf"]})
    second = JobManager(path=queue)
    second._start = lambda job: None
    job = second.jobs()[0]
    assert second.resume_pending() == 1
    assert job.paused is False and not job.needs_secrets


def test_f06_secrets_scrubbed_from_memory_after_finish(tmp_path):
    from app.core.jobs import JobManager
    from app.core.registry import get, load_all
    from app.core.spec import RunResult

    load_all()
    manager = JobManager(path=tmp_path / "queue.json", restore=False)
    manager._start = lambda job: None          # no worker thread in this test
    job = manager.submit(get("archive_decrypt"),
                         {"src": str(tmp_path / "a.zip"), "password": "super-secret-pw"})
    manager._on_success(job.id, RunResult(message="ok"))
    assert job.status == "done"
    assert "super-secret-pw" not in json.dumps(job.values), \
        "sensitive values must not linger in the in-memory job after finish"
