"""Regressions for execution safety, scheduling, indexing and packaging."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest


def test_plan_parser_requires_explicit_run_and_preserves_flags():
    from app.core.cli import _parse_args
    preview = _parse_args(["--plan", "make pdf", "--file", "a.png"])
    assert preview["mode"] == "plan" and not preview["run_flag"]
    run = _parse_args(["--plan", "make pdf", "--run", "--dry-run"])
    assert run["mode"] == "plan" and run["run_flag"] and run["dry_run"]
    reversed_order = _parse_args(["--run", "--plan", "make pdf"])
    assert reversed_order["mode"] == "plan" and reversed_order["run_flag"]
    missing = _parse_args(["--run", "--dry-run"])
    assert missing["run"] == "" and missing["dry_run"]


def test_schedule_dry_run_does_not_run_or_change_schedule(tmp_path, monkeypatch):
    from app.core import cli, registry, scheduler, workflows
    from app.core.spec import RunResult, ToolSpec

    calls = []
    spec = ToolSpec(id="audit_probe", category="dev", name="probe", name_en="probe",
                    run=lambda _ctx, _values: calls.append(1) or RunResult(message="executed"))
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    monkeypatch.setattr(cli, "load_all", lambda: None)
    store = scheduler.ScheduleStore(tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "store", store)
    workflow = workflows.Workflow(id="probe_flow", name="probe", steps=[workflows.Step(spec.id, {})])
    monkeypatch.setattr(workflows.store, "get", lambda _id: workflow)
    tool = scheduler.Schedule(id="tool", kind="tool", target_id=spec.id)
    flow = scheduler.Schedule(id="flow", kind="workflow", target_id=workflow.id)
    macro_path = tmp_path / "test.wgmacro"
    macro_path.write_text(json.dumps({"name": "preview", "steps": [
        {"type": "write", "path": str(tmp_path / "danger"), "text": "changed"}]}))
    macro = scheduler.Schedule(id="macro", kind="macro", target_id=str(macro_path))
    for item in (tool, flow, macro):
        store.save(item)
    before = store._path.read_bytes()
    for item in (tool, flow, macro):
        assert cli.run_cli(["--run-schedule", item.id, "--dry-run", "--lang", "en"]) == 0
    assert calls == []
    assert not (tmp_path / "danger").exists()
    assert store._path.read_bytes() == before
    assert all(item.runs == 0 for item in store.all())


def test_macro_file_mutation_is_blocked_by_safe_policy(tmp_path):
    from app.core.execution import ExecutionPolicy, PolicyDeniedError
    from app.core.macro import MacroRunner
    target = tmp_path / "file.txt"
    target.write_text("original")
    runner = MacroRunner(policy=ExecutionPolicy.for_api())
    with pytest.raises(PolicyDeniedError):
        runner.execute({"name": "overwrite", "steps": [
            {"type": "write", "path": str(target), "text": "changed"}]})
    assert target.read_text() == "original"
    assert not ExecutionPolicy.for_scheduler().confirmed


def test_cron_sunday_ranges_and_first_catchup():
    from app.core.scheduler import Schedule, parse_cron
    assert parse_cron("0 9 * * 0-7")["weekday"] == set(range(7))
    assert parse_cron("0 9 * * 1-7")["weekday"] == set(range(7))
    assert parse_cron("0 9 * * 7")["weekday"] == {0}
    schedule = Schedule(cron="0 9 * * *", created=datetime(2026, 1, 1, 8).timestamp())
    assert schedule.catch_up_due(datetime(2026, 1, 1, 10))


def test_schedule_save_failure_keeps_previous_file(tmp_path, monkeypatch):
    from app.core import scheduler
    store = scheduler.ScheduleStore(tmp_path / "schedules.json")
    store.save(scheduler.Schedule(id="one"))
    before = store._path.read_bytes()
    def fail(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(scheduler, "atomic_json_write", fail)
    with pytest.raises(OSError):
        store.save(scheduler.Schedule(id="two"))
    assert store._path.read_bytes() == before


def test_index_removes_stale_entries_and_matches_phrases(tmp_path, monkeypatch):
    from app.core import indexer
    monkeypatch.setattr(indexer, "index_path", lambda: tmp_path / "index.sqlite3")
    monkeypatch.setattr(indexer, "excluded_dirs", lambda: set())
    folder = tmp_path / "documents"
    folder.mkdir()
    file = folder / "file.txt"
    file.write_text("alpha intervening gamma")
    indexer.build([str(folder)])
    assert indexer.search("alpha")
    assert not indexer.search('"alpha gamma"')
    file.write_text("")
    indexer.build([str(folder)])
    assert not indexer.search("alpha")
    file.write_text("alpha again")
    indexer.build([str(folder)])
    file.unlink()
    indexer.build([str(folder)])
    assert not indexer.search("alpha")


def test_live_instance_does_not_expire_after_twelve_hours(monkeypatch):
    from app.core import single_instance
    class Memory:
        def attach(self): return True
        def lock(self): pass
        def unlock(self): pass
        def detach(self): pass
        def data(self): return f"4321 {time.time() - 13 * 3600}".encode()
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: pid == 4321)
    assert single_instance.InstanceGuard("test")._looks_alive(Memory())


def test_release_signing_requires_configured_key(tmp_path, monkeypatch):
    from scripts import sign_release
    (tmp_path / "sample.exe").write_bytes(b"sample")
    monkeypatch.delenv("WINGENIUS_UPDATE_SIGNING_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["sign_release", "--build", "--dir", str(tmp_path),
                                     "--version", "9.0.0", "--require-signature"])
    assert sign_release.main() != 0
    assert not (tmp_path / "update-manifest.json").exists()


def test_zero_undo_is_not_misclassified_as_safe(monkeypatch):
    from app.core import registry
    from app.core.execution import OperationRisk, classify_invocation
    from app.core.spec import Field, K_INT, ToolSpec
    spec = ToolSpec(id="undo_last", category="files", name="undo", name_en="undo",
                    fields=[Field("count", K_INT, default=1)])
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    assert classify_invocation("undo_last", {"count": 0}).risk >= OperationRisk.CONFIRM


def test_instance_ipc_forwards_file_and_pick(qapplication):
    import os
    from app.core.single_instance import InstanceGuard
    profile = f"audit-ipc-{os.getpid()}-{time.time_ns()}"
    guard = InstanceGuard(profile)
    assert guard.acquire()
    received = []
    guard.listen(lambda: received.append("ping"), received.append)
    payload = {"pick": True, "files": ["/path/example.pdf"]}
    script = ("from app.core.single_instance import InstanceGuard; import sys; "
              "print(InstanceGuard(sys.argv[1]).announce(sys.argv[2]))")
    proc = subprocess.Popen([sys.executable, "-c", script, profile, json.dumps(payload)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (proc.poll() is None or not received):
            qapplication.processEvents()
            time.sleep(0.01)
        out, err = proc.communicate(timeout=5)
        assert proc.returncode == 0 and "True" in out, err
        assert received == [payload]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        guard.release()


def test_late_cancellation_does_not_hide_completed_result(tmp_path, qapplication):
    from app.core.spec import RunResult, ToolSpec
    from app.core.worker import ToolRunner
    finished = []
    marker = tmp_path / "written"
    runner = None
    def run(_ctx, _values):
        marker.write_text("completed")
        runner.cancel()
        return RunResult(outputs=[str(marker)])
    spec = ToolSpec(id="late_cancel_probe", category="dev", name="probe", name_en="probe", run=run)
    runner = ToolRunner(spec, {}, "en", str(tmp_path))
    runner.succeeded.connect(lambda result: finished.append(("done", result.outputs)))
    runner.cancelled.connect(lambda: finished.append(("cancelled", [])))
    runner.run()  # synchronous: tests the race deterministically
    assert finished == [("done", [str(marker)])]


def test_os_trusted_but_unpinned_publisher_is_not_update_identity(tmp_path, monkeypatch):
    import hashlib
    from app.core import updates
    file = tmp_path / "app.exe"
    file.write_bytes(b"sample executable")
    monkeypatch.setattr(updates, "verify_pe_executable", lambda _path: True)
    monkeypatch.setattr(updates, "TRUSTED_AUTHENTICODE_THUMBPRINTS", frozenset())
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    asset = updates.ReleaseAsset(name=file.name, url="https://github.com/example", size=file.stat().st_size,
                                 digest="sha256:" + digest)
    check = lambda _path: updates.AuthenticodeResult(checked=True, signed=True, valid=True,
                                                      status="Valid", thumbprint="OTHER")
    with pytest.raises(updates.UpdateSignatureRequiredError):
        updates.verify_downloaded_package(file, asset=asset, expected_version="9.0.0",
                                          authenticode_checker=check)
    monkeypatch.setattr(updates, "TRUSTED_AUTHENTICODE_THUMBPRINTS", frozenset({"OTHER"}))
    report = updates.verify_downloaded_package(file, asset=asset, expected_version="9.0.0",
                                                authenticode_checker=check)
    assert report.cryptographically_verified


def test_arbitrary_dry_run_field_cannot_lower_risk():
    from app.core.execution import OperationRisk, classify_invocation
    from app.core.spec import ToolSpec
    spec = ToolSpec(id="danger_probe", category="files", name="danger", name_en="danger", danger=True)
    assert classify_invocation(spec, {"dry_run": True}).risk >= OperationRisk.CONFIRM


def test_folder_sync_preview_does_not_create_target(tmp_path):
    from app.core.spec import RunContext
    from app.tools.files_extra import folder_sync
    source = tmp_path / "source"
    source.mkdir()
    (source / "example.txt").write_text("example")
    target = tmp_path / "missing"
    folder_sync(RunContext(lang="en"), {"source": str(source), "target": str(target),
                                         "dry_run": True, "mode": "mirror"})
    assert not target.exists()


def test_macro_tool_dry_run_does_not_create_output_directory(tmp_path):
    from app.core.spec import RunContext
    from app.tools.auto_tools import macro_run
    path = tmp_path / "preview.json"
    path.write_text(json.dumps({"name": "preview", "steps": [
        {"type": "write", "path": "{out}/file", "text": "changed"}]}))
    output = tmp_path / "missing-output"
    macro_run(RunContext(lang="en", output_dir=str(output)),
              {"file": str(path), "folder": str(tmp_path), "dry_run": True})
    assert not output.exists()


def test_api_rejects_injected_dry_run_value(monkeypatch):
    from app.core import api_server, registry
    from app.core.spec import RunResult, ToolSpec
    calls = []
    spec = ToolSpec(id="api_danger_probe", category="dev", name="probe", name_en="probe",
                    danger=True, run=lambda _ctx, _values: calls.append(1) or RunResult())
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    server = api_server.ApiServer(token="regression-token")
    status, _ = server.dispatch("POST", "/run", {}, {"tool": spec.id, "values": {"dry_run": True}})
    assert status == 403
    assert not calls


def test_instance_permission_error_is_not_a_dead_process(monkeypatch):
    import os
    from app.core import single_instance
    if os.name == "nt":
        pytest.skip("POSIX process permission semantics")
    def denied(_pid, _signal):
        raise PermissionError("not permitted")
    monkeypatch.setattr(single_instance.os, "kill", denied)
    assert single_instance._pid_alive(1234)


def test_macro_execution_uses_approved_snapshot_when_file_changes(tmp_path):
    from app.core import registry
    from app.core.execution import ExecutionPolicy, execute_tool, snapshot_invocation
    from app.core.spec import RunContext
    from app.tools import auto_tools  # noqa: F401

    macro_file = tmp_path / "macro.json"
    target = tmp_path / "target.txt"
    macro_file.write_text(json.dumps({"steps": [{"type": "log", "message": "safe"}]}))
    values = snapshot_invocation("macro_run", {"file": str(macro_file)})
    macro_file.write_text(json.dumps({"steps": [{"type": "write", "path": str(target), "text": "changed"}]}))
    execute_tool(registry.get("macro_run"), values, RunContext(lang="en", output_dir=str(tmp_path)),
                 policy=ExecutionPolicy.for_api())
    assert not target.exists()


def test_ffmpeg_cancellation_interrupts_silent_child(monkeypatch):
    import threading
    from app.core.spec import Cancelled, RunContext
    from app.tools import media_tools

    popen = subprocess.Popen
    children = []
    def silent(*_args, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(media_tools, "_ffmpeg", lambda _ctx: "unused")
    monkeypatch.setattr(media_tools.subprocess, "Popen", silent)
    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            media_tools._run_ffmpeg(RunContext(cancel_event=event), [])
    finally:
        timer.cancel()
    assert time.monotonic() - started < 4
    assert children[0].poll() is not None


def test_job_shutdown_retains_live_threads():
    from app.core.jobs import JobManager

    class SlowRunner:
        def cancel(self):
            pass
        def wait(self, _timeout):
            return False

    manager = JobManager()
    runner = SlowRunner()
    manager._runners["busy"] = runner
    assert not manager.shutdown(1)
    assert manager._runners["busy"] is runner


def test_vault_rejects_stale_concurrent_writes(tmp_path):
    from app.core.vault import Vault, VaultError

    path = tmp_path / "secrets.wgv"
    first = Vault(path, "password", log_n=12)
    first.create()
    second = Vault(path, "password")
    second.unlock()
    first.add_text("one", "first")
    with pytest.raises(VaultError, match="changed since"):
        second.add_text("two", "second")
    current = Vault(path, "password")
    assert current.names() == ["one"]
    second.unlock()
    second.add_text("two", "second")
    assert Vault(path, "password").names() == ["one", "two"]


def test_vault_cross_process_stale_writes_are_rejected(tmp_path):
    from app.core.vault import Vault

    path = tmp_path / "parallel.wgv"
    Vault(path, "password", log_n=12).create()
    script = r'''
import sys
from pathlib import Path
from app.core.vault import Vault, VaultError
vault = Vault(Path(sys.argv[1]), "password")
vault.unlock()
print("ready", flush=True)
sys.stdin.readline()
try:
    vault.add_text(sys.argv[2], sys.argv[2])
    print("wrote", flush=True)
except VaultError:
    print("stale", flush=True)
'''
    children = [subprocess.Popen([sys.executable, "-c", script, str(path), str(i)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                for i in range(2)]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = [child.communicate(timeout=15)[0].strip() for child in children]
        assert sorted(results) == ["stale", "wrote"]
        assert len(Vault(path, "password").names()) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
