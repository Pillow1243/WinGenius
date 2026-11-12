"""Real-process regression coverage for timeout, cancellation and lock semantics."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.processes import ProcessCancelled, run_process  # noqa: E402
from app.core.resource_lock import LockTimeoutError, resource_lock  # noqa: E402
from app.core.utils import run_cmd  # noqa: E402


def test_timeout_kills_direct_child_and_reports_timeout():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.25)
    assert time.monotonic() - started < 4


def test_cancellation_stops_process_promptly():
    cancelled = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=20, cancel_event=cancelled
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.2)
    cancelled.set()
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ProcessCancelled)


def test_timeout_terminates_descendant_process_tree(tmp_path: Path):
    heartbeat = tmp_path / "child-heartbeat.txt"
    child_code = (
        "import pathlib,time; p=pathlib.Path(%r); "
        "f=p.open('a',encoding='utf-8'); "
        "exec('while True:\\n f.write(\"x\\\\n\"); f.flush(); time.sleep(.03)')"
    ) % str(heartbeat)
    parent_code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child_code!r}]); time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", parent_code], timeout=0.6, terminate_grace=0.2)
    deadline = time.monotonic() + 2
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert heartbeat.exists()
    stopped_size = heartbeat.stat().st_size
    time.sleep(0.3)
    assert heartbeat.stat().st_size == stopped_size, "descendant kept running after its owner timed out"


def test_successful_root_does_not_leave_background_descendant(tmp_path: Path):
    heartbeat = tmp_path / "orphan-heartbeat.txt"
    child_code = (
        "import pathlib,time; f=pathlib.Path(%r).open('a',encoding='utf-8'); "
        "exec('while True:\\n f.write(\"x\\\\n\"); f.flush(); time.sleep(.03)')"
    ) % str(heartbeat)
    parent_code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child_code!r}], "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "time.sleep(.12)"
    )
    result = run_process([sys.executable, "-c", parent_code], timeout=3, terminate_grace=0.2)
    assert result.returncode == 0
    deadline = time.monotonic() + 2
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert heartbeat.exists()
    stopped_size = heartbeat.stat().st_size
    time.sleep(0.3)
    assert heartbeat.stat().st_size == stopped_size, "background descendant survived a successful owner"


def test_shell_commands_are_refused():
    assert run_cmd("echo unsafe", shell=True)[0] == 2
    with pytest.raises(TypeError, match="argument sequence"):
        run_process("echo unsafe")


def test_resource_lock_enforces_timeout_and_releases_after_exception(tmp_path: Path):
    target = tmp_path / "shared.json"
    with resource_lock(target):
        with pytest.raises(LockTimeoutError):
            with resource_lock(target, timeout=0):
                pass
    with resource_lock(target, timeout=0.1):
        pass


def test_cross_process_lock_prevents_lost_updates(tmp_path: Path):
    target = tmp_path / "counter.json"
    target.write_text('{"value": 0}', encoding="utf-8")
    source = (
        "import json,sys; from pathlib import Path; "
        "from app.core.resource_lock import resource_lock; "
        "p=Path(sys.argv[1]); "
        "exec('for _ in range(30):\\n with resource_lock(p, timeout=10):\\n  d=json.loads(p.read_text()); d[\"value\"]+=1; p.write_text(json.dumps(d))')"
    )
    children = [subprocess.Popen([sys.executable, "-c", source, str(target)], cwd=ROOT) for _ in range(4)]
    results = [child.wait(timeout=15) for child in children]
    assert results == [0, 0, 0, 0]
    assert json.loads(target.read_text(encoding="utf-8"))["value"] == 120
