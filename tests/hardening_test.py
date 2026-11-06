"""Non-destructive regressions for the post-audit security hardening.

No network other than an ephemeral loopback server, no production profile,
no real credential, no deletion tool execution.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core import api_server, registry
from app.core.privacy import atomic_json_write, sanitize_document, sanitize_values
from app.core.spec import Field, K_PASSWORD, K_TEXT, RunContext, RunResult, ToolSpec


@pytest.fixture
def profile(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    root.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.core import paths
    monkeypatch.setattr(paths, "data_dir", lambda: root)
    return root


@pytest.mark.parametrize("key", [
    "password", "new_password", "confirm", "api_key", "access_token",
    "Authorization", "credentials", "private_key", "رمز", "nested",
])
def test_sensitive_keys_never_persist(key):
    raw = {key: {"password": "fake-secret"} if key == "nested" else "fake-secret", "count": 3}
    clean = sanitize_values("unknown", raw)
    assert "fake-secret" not in json.dumps(clean)
    assert clean["count"] == 3
    assert "fake-secret" in json.dumps(raw)  # runtime input not modified


def test_sensitive_metadata_and_private_plaintext(monkeypatch):
    spec = ToolSpec(id="metadata_probe", category="dev", name="probe", name_en="probe", fields=[
        Field("unusual", K_TEXT, sensitive=True), Field("hidden", K_PASSWORD),
    ])
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    assert sanitize_values(spec.id, {"unusual": "x", "hidden": "y", "count": 4}) == {"count": 4}
    assert sanitize_values("text_encrypt", {"text": "private", "password": "secret"}) == {}
    assert sanitize_values("vault_add", {"note_text": "private"}) == {}


def test_history_scrubs_values_and_echoed_messages(tmp_path, monkeypatch):
    from app.core import history as module
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
    history = module.History()
    history.add(SimpleNamespace(tool_id="vault_create", title="probe", status="done", outputs=[],
                                values={"password": "FAKE_HISTORY_SECRET", "confirm": "FAKE_HISTORY_SECRET"},
                                message="error: FAKE_HISTORY_SECRET"))
    assert "FAKE_HISTORY_SECRET" not in history._path.read_text()
    assert history.entries()[0].values == {}
    assert "FAKE_HISTORY_SECRET" not in history.entries()[0].message


def test_legacy_history_is_scrubbed_on_load(tmp_path, monkeypatch):
    from app.core import history as module
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
    path = tmp_path / "history.json"
    atomic_json_write(path, [{"tool_id": "vault_list", "values": {"password": "FAKE_OLD_SECRET"},
                              "message": "FAKE_OLD_SECRET"}])
    history = module.History()
    assert history.entries()[0].values == {}
    assert "FAKE_OLD_SECRET" not in path.read_text()


def test_workflow_export_and_legacy_load(tmp_path, monkeypatch):
    from app.core import workflows
    monkeypatch.setattr(workflows, "data_dir", lambda: tmp_path)
    store = workflows.WorkflowStore()
    step = workflows.Step("vault_list", {"password": "FAKE_WORKFLOW_SECRET", "vault": "demo.wgv"})
    workflow = store.new("probe", [step])
    store.save(workflow)
    assert "FAKE_WORKFLOW_SECRET" not in store.path.read_text()
    assert "password" not in workflow.as_dict()["steps"][0]["values"]
    assert step.values["password"] == "FAKE_WORKFLOW_SECRET"  # usable for this runtime
    atomic_json_write(store.path, [{"id": "old", "steps": [
        {"tool_id": "vault_list", "values": {"password": "FAKE_LEGACY_SECRET"}}]}])
    assert workflows.WorkflowStore().get("old").steps[0].values == {}
    assert "FAKE_LEGACY_SECRET" not in store.path.read_text()


def test_schedule_and_watcher_exports_drop_credentials():
    from app.core.scheduler import Schedule
    from app.core.watcher import WatchRule
    schedule = Schedule(target_id="text_encrypt", payload={"password": "fake", "text": "private"})
    watch = WatchRule(id="probe", target_id="vault_add", values={"password": "fake", "note_text": "private"})
    assert schedule.as_dict()["payload"] == {}
    assert watch.as_dict()["values"] == {}


def test_migration_scrubs_current_and_backup_profiles(tmp_path):
    from app.core import upgrade
    atomic_json_write(tmp_path / "schema.json", {"version": 2})
    raw = [{"tool_id": "vault_list", "values": {"password": "FAKE_MIGRATION_SECRET"}}]
    atomic_json_write(tmp_path / "history.json", raw)
    atomic_json_write(tmp_path / "backups" / "old" / "history.json", raw)
    (tmp_path / "api_token.txt").write_text("FAKE_API_CREDENTIAL")
    report = upgrade.migrate(tmp_path)
    assert report["ok"] and report["steps"] == ["2→3"]
    assert upgrade.current_version(tmp_path) == 3
    assert not (Path(report["backup"]) / "api_token.txt").exists()
    for path in tmp_path.rglob("*.json"):
        assert "FAKE_MIGRATION_SECRET" not in path.read_text()
    assert upgrade.migrate(tmp_path)["steps"] == []


def test_backup_sanitizes_old_unscrubbed_profiles(tmp_path, monkeypatch):
    from app.core import paths
    root = tmp_path / "profile"
    root.mkdir()
    monkeypatch.setattr(paths, "data_dir", lambda: root)
    atomic_json_write(root / "history.json", [{"tool_id": "vault_list", "values": {"password": "FAKE_ZIP_SECRET"}}])
    result = registry.get("backup_appdata").run(RunContext(lang="en"), {"dest": str(tmp_path / "exports")})
    import zipfile
    with zipfile.ZipFile(result.outputs[0]) as archive:
        assert "FAKE_ZIP_SECRET" not in archive.read("history.json").decode()


def test_atomic_json_failure_keeps_previous_file(tmp_path, monkeypatch):
    import app.core.privacy as privacy
    path = tmp_path / "data.json"
    atomic_json_write(path, {"value": "old"})
    monkeypatch.setattr(privacy.os, "replace", Mock(side_effect=OSError("synthetic failure")))
    with pytest.raises(OSError):
        atomic_json_write(path, {"value": "new"})
    assert json.loads(path.read_text()) == {"value": "old"}
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("token", ["", "   "])
def test_api_refuses_empty_tokens(token):
    server = api_server.ApiServer(port=0, token=token)
    with pytest.raises(RuntimeError, match="token"):
        server.start()
    assert not server.running


def test_api_credential_io_failure_is_not_anonymous(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "token_path", lambda: tmp_path / "token.txt")
    monkeypatch.setattr(api_server.os, "open", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(RuntimeError, match="credential"):
        api_server.ApiServer(port=0)
    assert api_server.api_token(create=False) == ""


def test_api_refuses_non_loopback():
    with pytest.raises(ValueError, match="loopback"):
        api_server.ApiServer(host="0.0.0.0", port=0, token="fake").start()


@pytest.mark.parametrize("body", [[], [1], "text", 1, None])
def test_api_rejects_non_object_json(body):
    server = api_server.ApiServer(port=0, token="test-only")
    server.start()
    try:
        request = urllib.request.Request(server.base_url() + "/run", data=json.dumps(body).encode(),
                                         headers={"X-WinGenius-Token": "test-only"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 400
    finally:
        server.stop()


def test_api_cleared_token_denies_requests():
    server = api_server.ApiServer(port=0, token="test-only")
    server.start()
    try:
        server.token = ""
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(server.base_url() + "/health", timeout=3)
        assert error.value.code == 401
    finally:
        server.stop()


def test_api_destructive_tools_denied_by_default(monkeypatch):
    run = Mock(return_value=RunResult(message="synthetic only"))
    spec = ToolSpec(id="danger_probe", category="dev", name="probe", name_en="probe", danger=True, run=run)
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    server = api_server.ApiServer(token="fake")
    assert server.dispatch("POST", "/run", {}, {"tool": spec.id})[0] == 403
    run.assert_not_called()
    allowed = api_server.ApiServer(token="fake", allow_dangerous=True)
    assert allowed.dispatch("POST", "/run", {}, {"tool": spec.id})[1]["ok"]
    assert run.call_count == 1


def test_api_execution_limit_released_after_errors(monkeypatch):
    spec = ToolSpec(id="error_probe", category="dev", name="probe", name_en="probe",
                    run=Mock(side_effect=ValueError("synthetic")))
    monkeypatch.setitem(registry._TOOLS, spec.id, spec)
    server = api_server.ApiServer(token="fake", max_parallel=1)
    assert server.dispatch("POST", "/run", {}, {"tool": spec.id})[1]["ok"] is False
    assert server._execution_slots.acquire(blocking=False)
    assert server.dispatch("POST", "/run", {}, {"tool": spec.id})[0] == 429
    server._execution_slots.release()


@pytest.fixture(scope="module")
def qapp(qapplication):
    return qapplication


def test_job_execution_context_is_independent(qapp, monkeypatch):
    from app.core import jobs
    manager = jobs.JobManager(max_parallel=1)
    monkeypatch.setattr(manager, "_pump", lambda: None)
    source = {"files": ["one"]}
    a = manager.submit(registry.get("uuid_generate"), source, "fa", "/output/A")
    b = manager.submit(registry.get("uuid_generate"), {}, "en", "/output/B")
    source["files"].append("two")
    manager.configure("en", "/output/C")
    assert (a.lang, a.output_dir, a.values) == ("fa", "/output/A", {"files": ["one"]})
    assert (b.lang, b.output_dir) == ("en", "/output/B")
    fake = Mock()
    factory = Mock(return_value=fake)
    monkeypatch.setattr(jobs, "ToolRunner", factory)
    manager._start(a)
    assert factory.call_args.args[2:4] == ("fa", "/output/A")
    manager._closed = True
    with pytest.raises(RuntimeError):
        manager.submit(registry.get("uuid_generate"), {})


@pytest.mark.parametrize("background", [False, True])
def test_gui_danger_decline_prevents_execution(qapp, monkeypatch, background):
    from app.ui.tool_panel import ToolPanel
    from PySide6.QtWidgets import QMessageBox
    from app.core.settings import settings
    old = settings.get("ui/confirm_danger")
    settings.set("ui/confirm_danger", True)
    panel = ToolPanel()
    panel.spec = ToolSpec(id="fake_danger", category="dev", name="probe", name_en="probe", danger=True)
    panel._jobs = Mock()
    monkeypatch.setattr(panel, "_collect", lambda: {})
    monkeypatch.setattr(panel, "_validate", lambda _: None)
    question = Mock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", question)
    try:
        (panel._run_in_background if background else panel._run)()
        assert question.call_count == 1
        panel._jobs.submit.assert_not_called()
        assert panel._runner is None
    finally:
        settings.set("ui/confirm_danger", old)
        panel.deleteLater()


def test_gui_approved_background_job_is_queued(qapp, monkeypatch):
    from app.ui.tool_panel import ToolPanel
    panel = ToolPanel()
    panel.spec = registry.get("uuid_generate")
    panel._jobs = Mock()
    panel._jobs.submit.return_value.id = "fake-job"
    monkeypatch.setattr(panel, "_collect", lambda: {"count": 2})
    monkeypatch.setattr(panel, "_validate", lambda _: None)
    monkeypatch.setattr(panel, "_confirm_execution", lambda _values: True)
    panel._run_in_background()
    panel._jobs.submit.assert_called_once()
    panel.deleteLater()


def test_generated_cli_does_not_export_password(qapp, monkeypatch):
    from app.ui.tool_panel import ToolPanel
    panel = ToolPanel()
    panel.spec = registry.get("vault_list")
    monkeypatch.setattr(panel, "_collect", lambda: {"password": "FAKE_CLIPBOARD_SECRET", "vault": "demo.wgv"})
    command = panel.cli_command()
    assert "FAKE_CLIPBOARD_SECRET" not in command and "password" not in command
    assert "demo.wgv" in command
    panel.deleteLater()


def test_unapproved_and_changed_plugins_do_not_execute(tmp_path, monkeypatch):
    from app.core import plugins
    monkeypatch.setattr(plugins, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugins, "_LOADED", {})
    plugin = tmp_path / "probe.py"
    marker = tmp_path / "marker.txt"
    plugin.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('approved')\n")
    monkeypatch.setattr(plugins, "discover", lambda: [plugin])
    assert "approval required" in plugins.load_plugins()[0].error
    assert not marker.exists()
    plugins.trust_plugin(plugin)
    plugins.load_plugins()
    assert marker.read_text() == "approved"
    marker.unlink()
    plugin.write_text(plugin.read_text() + "\n# changed\n")
    assert "approval required" in plugins.load_plugins()[0].error
    assert not marker.exists()


def test_plugin_cannot_override_builtin_and_partial_registration_rolls_back(tmp_path, monkeypatch):
    from app.core import plugins
    monkeypatch.setattr(plugins, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugins, "_LOADED", {})
    original = registry.get("uuid_generate")
    plugin = tmp_path / "override.py"
    plugin.write_text("from app.core.registry import tool\n"
                      "@tool(id='temporary_probe',category='dev',name='probe',name_en='probe')\n"
                      "def first(ctx, values): pass\n"
                      "@tool(id='uuid_generate',category='dev',name='replacement',name_en='replacement')\n"
                      "def override(ctx, values): pass\n")
    monkeypatch.setattr(plugins, "discover", lambda: [plugin])
    plugins.trust_plugin(plugin)
    assert "cannot replace" in plugins.load_plugins()[0].error
    assert registry.get("uuid_generate") is original
    assert registry.get("temporary_probe") is None


def test_quality_gate_stops_on_first_failed_native_command(monkeypatch):
    from scripts import quality_gate
    run = Mock(side_effect=[SimpleNamespace(returncode=7), SimpleNamespace(returncode=0)])
    monkeypatch.setattr(quality_gate.subprocess, "run", run)
    assert quality_gate.run_checks([["first"], ["must-not-run"]]) == 7
    assert run.call_count == 1


def test_vault_rejects_hostile_kdf_before_deriving(monkeypatch):
    from app.core import vault
    from cryptography.hazmat.primitives.kdf import scrypt
    derive = Mock()
    monkeypatch.setattr(scrypt, "Scrypt", derive)
    with pytest.raises(vault.VaultError, match="budget"):
        vault.derive_key_scrypt("fake", b"0" * 16, log_n=20, r=16, p=4)
    derive.assert_not_called()


# ========================================================================= 7.1
def test_v71_headless_cli_does_not_import_pyside6():
    """Headless CLI commands must execute with PySide6 blocked."""
    import subprocess
    import sys

    code = (
        "import importlib.abc, sys\n"
        "class _BlockQt(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path, target=None):\n"
        "        if fullname == 'PySide6' or fullname.startswith('PySide6.'):\n"
        "            raise ImportError('PySide6 blocked in headless test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BlockQt())\n"
        "from app.bootstrap import main\n"
        "rc = main(['--version'])\n"
        "assert rc == 0\n"
        "from app.core import registry\n"
        "failures = registry.load_all()\n"
        "assert not failures, failures\n"
        "assert 'PySide6' not in sys.modules\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_v71_dynamic_risk_classification_and_nested_propagation(tmp_path):
    from app.core.execution import OperationRisk, classify_invocation

    registry.load_all()
    assert classify_invocation("base64_tool", {"mode": "encode", "text": "abc"}).risk == OperationRisk.SAFE
    assert classify_invocation("batch_rename", {"preview": False}).risk == OperationRisk.CONFIRM
    assert classify_invocation("junk_scan", {"delete": False}).risk == OperationRisk.SAFE
    assert classify_invocation("junk_scan", {"delete": True}).risk == OperationRisk.DESTRUCTIVE
    assert classify_invocation("ocr_setup", {"action": "status"}).risk == OperationRisk.SAFE
    assert classify_invocation("ocr_setup", {"action": "download"}).risk == OperationRisk.CONFIRM
    assert classify_invocation("ocr_setup", {"action": "set_path"}).risk == OperationRisk.DESTRUCTIVE
    assert classify_invocation("bitlocker_status", {"action": "recovery"}).risk == OperationRisk.ELEVATED

    flow_file = tmp_path / "danger_flow.json"
    flow_file.write_text(json.dumps({
        "name": "nested-danger",
        "steps": [
            {"tool_id": "uuid_generate", "values": {"count": 1}},
            {"tool_id": "junk_scan", "values": {"folder": str(tmp_path), "delete": True}},
        ],
    }), encoding="utf-8")
    flow_risk = classify_invocation("flow_run", {"file": str(flow_file)})
    assert flow_risk.risk == OperationRisk.DESTRUCTIVE

    macro_file = tmp_path / "danger.wgmacro"
    macro_file.write_text(json.dumps({
        "name": "nested-macro",
        "steps": [
            {"type": "if", "cond": "1 == 1", "then": [
                {"type": "run", "tool": "secure_delete", "args": {"src": ["x"]}},
            ]},
        ],
    }), encoding="utf-8")
    macro_risk = classify_invocation("macro_run", {"file": str(macro_file), "dry_run": False})
    assert macro_risk.risk == OperationRisk.DESTRUCTIVE
    assert classify_invocation("macro_run", {"file": str(macro_file), "dry_run": True}).risk == OperationRisk.SAFE


def test_v71_execution_policy_blocks_dynamic_danger_and_macro_continue(tmp_path, profile):
    from app.core.execution import ExecutionPolicy, PolicyDeniedError, execution_service
    from app.core.macro import MacroRunner

    registry.load_all()
    api_policy = ExecutionPolicy.for_api(allow_dangerous=False)
    ctx = RunContext(lang="en", output_dir=str(tmp_path), policy=api_policy)
    with pytest.raises(PolicyDeniedError):
        execution_service.execute("junk_scan", {"folder": str(tmp_path), "delete": True}, ctx, policy=api_policy)

    # MacroRunner with on_error="continue" MUST NOT swallow PolicyDeniedError!
    macro_doc = {
        "name": "no-swallow",
        "on_error": "continue",
        "steps": [
            {"type": "run", "tool": "junk_scan", "args": {"folder": str(tmp_path), "delete": True}},
            {"type": "set", "name": "reached", "value": "yes"},
        ],
    }
    runner = MacroRunner(lang="en", output_dir=str(tmp_path), policy=api_policy)
    with pytest.raises(PolicyDeniedError):
        runner.execute(macro_doc)


def test_v71_cli_policy_yes_dry_run_and_macro_schedule(tmp_path, profile, capsys):
    from app.core import cli
    from app.core.scheduler import Schedule, store as sched_store

    registry.load_all()
    rc_blocked = cli.run_cli([
        "--run", "junk_scan", "--set", f"folder={tmp_path}", "--set", "delete=true", "--lang", "en",
    ])
    capsys.readouterr()
    assert rc_blocked != 0

    rc_dry = cli.run_cli([
        "--run", "junk_scan", "--set", f"folder={tmp_path}", "--set", "delete=true",
        "--dry-run", "--json", "--lang", "en",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc_dry == 0 and "[DRY-RUN]" in payload.get("message", "")

    macro_path = tmp_path / "sched.wgmacro"
    macro_path.write_text(json.dumps({
        "name": "sched-macro",
        "steps": [{"type": "run", "tool": "uuid_generate", "args": {"count": 1}}],
    }), encoding="utf-8")
    sched = Schedule(
        id="sched_macro_71",
        name="Macro Schedule",
        cron="* * * * *",
        kind="macro",
        target_id=str(macro_path),
        payload={"file": str(macro_path)},
    )
    sched_store.save(sched)
    try:
        rc_sched = cli.run_cli(["--run-schedule", "sched_macro_71", "--lang", "en"])
        capsys.readouterr()
        assert rc_sched == 0
    finally:
        sched_store.delete("sched_macro_71")


def test_v71_api_http_framing_origin_and_dynamic_policy(tmp_path):
    import http.client

    registry.load_all()
    srv = api_server.ApiServer(port=0, token="tok-71", lang="en", allow_dangerous=False)
    port = srv.start()
    try:
        # Browser Origin header must be rejected with 403 (CSRF / DNS-rebinding defense)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/health", headers={
            "X-WinGenius-Token": "tok-71",
            "Origin": "https://evil.example",
        })
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403
        conn.close()

        # Unsupported HTTP method returns JSON 405
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("DELETE", "/health", headers={"X-WinGenius-Token": "tok-71"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 405 and "error" in body
        conn.close()

        # Transfer-Encoding: chunked returns JSON 501 (anti-request-smuggling)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("POST", "/run")
        conn.putheader("X-WinGenius-Token", "tok-71")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(b"0\r\n\r\n")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 501 and "Transfer-Encoding" in body.get("error", "")
        conn.close()

        # Dynamic-risk tool (`junk_scan` with `delete=True`) has spec.danger=False,
        # yet ExecutionService blocks it on /run when allow_dangerous=False!
        st, data = srv.dispatch("POST", "/run", {}, {
            "tool": "junk_scan",
            "values": {"folder": str(tmp_path), "delete": True},
        })
        assert st == 403 and data.get("ok") is False
    finally:
        srv.stop()


def test_v71_launcher_blocks_dangerous_uri_schemes(monkeypatch):
    from app.core import launcher

    monkeypatch.delenv("WINGENIUS_NO_LAUNCH", raising=False)
    for bad in ("javascript:alert(1)", "ms-msdt:/id PCWDiagnostic", "search-ms:query=calc", "vbscript:msgbox"):
        with pytest.raises(ValueError):
            launcher.open_target(bad)


def test_v71_network_and_ocr_argument_injections_blocked(tmp_path):
    from app.core.spec import ToolError

    registry.load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path))

    # net_diag option injection
    with pytest.raises(ToolError):
        registry.get("net_diag").run(ctx, {"action": "ping", "host": "-t 127.0.0.1", "count": 1})

    # lan_scan option / non-octet injection
    with pytest.raises(ToolError):
        registry.get("lan_scan").run(ctx, {"subnet": "-w 1 127.0", "timeout": 100})
    with pytest.raises(ToolError):
        registry.get("lan_scan").run(ctx, {"subnet": "999.0.1", "timeout": 100})

    # http_timing CRLF & scheme injection
    with pytest.raises(ToolError):
        registry.get("http_timing").run(ctx, {"url": "file:///etc/passwd"})
    with pytest.raises(ToolError):
        registry.get("http_timing").run(ctx, {"url": "http://example.com/a\r\nX-Evil: 1"})

    # ocr_setup symlink executable refusal
    real_bin = tmp_path / "not_tesseract.sh"
    real_bin.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    sym_tess = tmp_path / "tesseract"
    sym_tess.symlink_to(real_bin)
    with pytest.raises(ToolError):
        registry.get("ocr_setup").run(ctx, {"action": "set_path", "path": str(sym_tess)})
    with pytest.raises(ToolError):
        registry.get("ocr_setup").run(ctx, {"action": "set_path", "path": str(real_bin)})

    # audio_transcribe model allowlist
    dummy_wav = tmp_path / "sample.wav"
    dummy_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    with pytest.raises(ToolError):
        registry.get("audio_transcribe").run(ctx, {
            "src": str(dummy_wav), "model": "evil/repo-exec", "language": "en", "fmt": "txt",
        })


def test_v71_archive_symlink_zipbomb_and_secure_delete_symlink(tmp_path):
    import stat
    import zipfile
    from app.core.spec import ToolError

    registry.load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path))

    # 1. Symlink inside ZIP is skipped by both zip_extract and archive_decrypt
    zpath = tmp_path / "sym.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("safe.txt", "hello")
        sym_info = zipfile.ZipInfo("evil_link")
        sym_info.create_system = 3
        sym_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(sym_info, "/etc/passwd")

    out1 = tmp_path / "ex1"
    res1 = registry.get("zip_extract").run(ctx, {"src": str(zpath), "dest": str(out1)})
    assert (out1 / "safe.txt").is_file()
    assert not (out1 / "evil_link").exists() and not (out1 / "evil_link").is_symlink()
    assert "skipped" in res1.message

    out2 = tmp_path / "ex2"
    res2 = registry.get("archive_decrypt").run(ctx, {"src": str(zpath), "password": "", "out_dir": str(out2)})
    assert (out2 / "safe.txt").is_file()
    assert not (out2 / "evil_link").exists() and not (out2 / "evil_link").is_symlink()

    # 2. High-ratio ZIP bomb entry (>200x and >10 MiB) is rejected
    bomb_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("zeros.bin", b"\x00" * (12 * 1024 * 1024))
    with pytest.raises(ToolError):
        registry.get("zip_extract").run(ctx, {"src": str(bomb_path), "dest": str(tmp_path / "bomb_out")})

    # 3. secure_delete refuses symlinks WITHOUT modifying the symlink target
    precious = tmp_path / "precious.txt"
    precious.write_text("DO-NOT-SHRED", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(precious)
    del_res = registry.get("secure_delete").run(ctx, {"src": [str(link)], "passes": 1})
    assert precious.read_text(encoding="utf-8") == "DO-NOT-SHRED"
    assert "symlink" in del_res.details.lower() or "failed" in del_res.message.lower()


def test_v71_crypto_wga1_enforced_and_secret_redaction(tmp_path, monkeypatch):
    import base64
    from app.core.spec import ToolError

    registry.load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path))

    # 1. text_encrypt uses WGA1 and rejects legacy unauthenticated WGF1 tokens
    enc = registry.get("text_encrypt").run(ctx, {"mode": "encrypt", "text": "hello 7.1", "password": "pw!"})
    raw_blob = base64.urlsafe_b64decode(enc.details.encode())
    assert raw_blob.startswith(b"WGA1")
    dec = registry.get("text_encrypt").run(ctx, {"mode": "decrypt", "text": enc.details, "password": "pw!"})
    assert dec.details == "hello 7.1"

    legacy_wgf1 = base64.urlsafe_b64encode(b"WGF1" + b"\x00" * 32).decode()
    with pytest.raises(ToolError):
        registry.get("text_encrypt").run(ctx, {"mode": "decrypt", "text": legacy_wgf1, "password": "pw!"})

    # 2. secret_scan masks raw secret values while preserving detector labels
    sec_dir = tmp_path / "scan"
    sec_dir.mkdir()
    raw_key = "AIzaSyD-SECRETVALUE1234567890abcdef1234"
    (sec_dir / "app.py").write_text(f'KEY = "{raw_key}"\n', encoding="utf-8")
    scan_res = registry.get("secret_scan").run(ctx, {"folder": str(sec_dir), "max_mb": "1"})
    assert "Google API key" in scan_res.details
    assert raw_key not in scan_res.details

    # 3. env_vars(action="list") redacts sensitive env vars while preserving "=" format
    monkeypatch.setenv("WG_TEST_API_SECRET", "super-secret-token-value-999")
    monkeypatch.setenv("WG_PUBLIC_MODE", "normal_value")
    env_res = registry.get("env_vars").run(ctx, {"action": "list"})
    assert "WG_PUBLIC_MODE=normal_value" in env_res.details
    assert "WG_TEST_API_SECRET=<redacted>" in env_res.details
    assert "super-secret-token-value-999" not in env_res.details

    # 4. checksum_manifest(verify) refuses outside / absolute paths
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    mdir = tmp_path / "mdir"
    mdir.mkdir()
    manifest = mdir / "bad.sha256"
    import hashlib
    h = hashlib.sha256(b"outside").hexdigest()
    manifest.write_text(f"{h}  ../outside.txt\n{h}  {outside}\n", encoding="utf-8")
    ver = registry.get("checksum_manifest").run(ctx, {"action": "verify", "manifest": str(manifest)})
    assert "0 ok" in ver.message and "2 modified" in ver.message


def test_v71_xss_qr_curl_indexer_and_persian_save(tmp_path, monkeypatch):
    from app.core import indexer

    registry.load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path))

    # 1. markdown_to_html escapes <title> derived from file stem
    # Windows forbids angle brackets in filenames. Emulate a hostile stem at
    # the path boundary so the same escaping is exercised on every platform.
    evil_md = tmp_path / "input.md"
    evil_md.write_text("# Safe Heading\n", encoding="utf-8")
    path_type = type(evil_md)
    original_stem = path_type.stem.fget
    with monkeypatch.context() as patch:
        patch.setattr(path_type, "stem", property(
            lambda p: '<img src=x onerror="alert(1)">' if p == evil_md else original_stem(p)))
        html_res = registry.get("markdown_to_html").run(
            ctx, {"file": str(evil_md), "styled": False, "out": str(tmp_path / "out.html")}
        )
    rendered = Path(html_res.outputs[0]).read_text(encoding="utf-8")
    assert "<title>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</title>" in rendered

    # 2. qr_generate sanitizes Wi-Fi SSID filename and marks password as K_PASSWORD
    qr_spec = registry.get("qr_generate")
    pw_field = next(f for f in qr_spec.fields if f.key == "password")
    assert pw_field.kind == K_PASSWORD
    qr_res = qr_spec.run(ctx, {"mode": "wifi", "ssid": "../../Bad;Net", "password": "p;w", "size": 200, "out": ""})
    assert Path(qr_res.outputs[0]).parent.resolve() == tmp_path.resolve()

    # 3. curl_to_code marks command sensitive & safely quotes headers
    curl_spec = registry.get("curl_to_code")
    assert next(f for f in curl_spec.fields if f.key == "command").sensitive is True
    assert sanitize_values("curl_to_code", {"command": "curl -H 'Authorization: Bearer SECRET'", "target": "python"}) == {
        "target": "python"
    }

    # 4. persian_numbers & persian_sort write output files when requested
    num_out = tmp_path / "digits.txt"
    num_res = registry.get("persian_numbers").run(
        ctx, {"source": "text", "text": "1234", "target": "fa", "write_file": True, "out": str(num_out)}
    )
    assert num_out.read_text(encoding="utf-8") == "۱۲۳۴" and str(num_out) in num_res.outputs

    sort_out = tmp_path / "sorted.txt"
    sort_res = registry.get("persian_sort").run(
        ctx, {"source": "text", "text": "ب\nآ\nپ", "write_file": True, "out": str(sort_out)}
    )
    assert sort_out.exists() and str(sort_out) in sort_res.outputs

    # 5. indexer skips .env files and safely escapes hostile FTS5 operator queries
    idx_dir = tmp_path / "docs"
    idx_dir.mkdir()
    (idx_dir / ".env").write_text("SUPER_SECRET_ENV_TOKEN=xyz987", encoding="utf-8")
    (idx_dir / "note.txt").write_text("WinGenius hardening release notes", encoding="utf-8")
    old_idx = indexer.index_path
    indexer.index_path = lambda: tmp_path / "fts.sqlite3"
    try:
        indexer.build([str(idx_dir)])
        assert indexer.search("SUPER_SECRET_ENV_TOKEN") == []
        # Hostile FTS5 syntax must not raise OperationalError
        hits = indexer.search('WinGenius" OR NOT (NEAR/2 * ^ : hardening')
        assert isinstance(hits, list)
    finally:
        indexer.index_path = old_idx


def test_v71_multipage_pdf_form_and_rtl_ltr_theme(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, ArrayObject, FloatObject, TextStringObject
    from app.core import theme

    # 1. Direction-aware stylesheet & light-accent WCAG foreground contrast
    light_pal = theme.build_palette("daylight", "#FFEE88")
    rtl_qss = theme.stylesheet(light_pal, direction="rtl")
    ltr_qss = theme.stylesheet(light_pal, direction="ltr")
    assert "subcontrol-position: center left;" in rtl_qss
    assert "subcontrol-position: center right;" in ltr_qss
    assert "#101626" in ltr_qss  # dark text chosen automatically for high-luminance yellow accent

    # 2. Multi-page PDF AcroForm widget update does not crash and writes valid output
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_blank_page(200, 200)
    field_obj = DictionaryObject({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("City"),
        NameObject("/V"): TextStringObject(""),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Rect"): ArrayObject([FloatObject(10), FloatObject(10), FloatObject(100), FloatObject(30)]),
    })
    ref = writer._add_object(field_obj)
    writer.pages[1][NameObject("/Annots")] = ArrayObject([ref])
    acro = DictionaryObject({NameObject("/Fields"): ArrayObject([ref])})
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(acro)
    src_pdf = tmp_path / "form2p.pdf"
    with src_pdf.open("wb") as fh:
        writer.write(fh)

    registry.load_all()
    ctx = RunContext(lang="en", output_dir=str(tmp_path))
    out_pdf = tmp_path / "filled2p.pdf"
    res = registry.get("pdf_forms_fill").run(
        ctx, {"src": str(src_pdf), "fields": "City=Tehran", "out": str(out_pdf)}
    )
    assert out_pdf.is_file() and str(out_pdf) in res.outputs
    filled_fields = PdfReader(str(out_pdf)).get_fields() or {}
    assert str(filled_fields.get("City", {}).get("/V") or "") == "Tehran"


# ============================================================ 7.1 Auto-Update
def _make_synthetic_pe(payload_tag: bytes = b"WinGenius-7.2.0-build") -> bytes:
    """Construct a minimal syntactically valid Windows PE (`MZ` + `PE\\0\\0`) binary for tests."""
    import struct

    dos = bytearray(128)
    dos[0:2] = b"MZ"
    pe_offset = 128
    dos[0x3C:0x40] = struct.pack("<I", pe_offset)
    pe_hdr = b"PE\x00\x00" + b"\x64\x86" + b"\x00" * 120
    body = payload_tag + (b"\x90" * 2048)
    return bytes(dos) + pe_hdr + body


def test_v71_update_version_detection_and_install_modes(tmp_path):
    from app.core import updates

    assert updates.version_key("7.1.0") == (7, 1, 0)
    assert updates.version_key("v7.2.0") > updates.version_key("7.1.9")
    assert updates.version_key("7.1.0-rc1") == (7, 1, 0)  # prerelease suffix must not inflate patch!

    inst_dir = tmp_path / "installed"
    inst_dir.mkdir()
    (inst_dir / "WinGenius.exe").write_bytes(_make_synthetic_pe())
    (inst_dir / "unins000.exe").write_bytes(_make_synthetic_pe(b"unins"))
    assert updates.detect_install_mode(inst_dir / "WinGenius.exe", portable=False, frozen=True) == "installer"

    port_dir = tmp_path / "portable"
    port_dir.mkdir()
    (port_dir / "WinGenius.exe").write_bytes(_make_synthetic_pe())
    (port_dir / "portable.flag").write_text("", encoding="utf-8")
    assert updates.detect_install_mode(port_dir / "WinGenius.exe", portable=False, frozen=True) == "portable"

    stand_dir = tmp_path / "standalone"
    stand_dir.mkdir()
    (stand_dir / "WinGenius.exe").write_bytes(_make_synthetic_pe())
    assert updates.detect_install_mode(stand_dir / "WinGenius.exe", portable=False, frozen=True) == "standalone_exe"

    rel = updates.ReleaseInfo(
        version="7.2.0",
        tag_name="v7.2.0",
        name="WinGenius 7.2.0",
        body="### Changes\n- Security update",
        html_url="https://github.com/Pillow1243/WinGenius/releases/tag/v7.2.0",
        published_at="2026-11-04T12:00:00Z",
        assets=(
            updates.ReleaseAsset("WinGenius-7.2.0.exe", "https://github.com/Pillow1243/WinGenius/releases/download/v7.2.0/WinGenius-7.2.0.exe", 4096),
            updates.ReleaseAsset("WinGeniusSetup-7.2.0.exe", "https://github.com/Pillow1243/WinGenius/releases/download/v7.2.0/WinGeniusSetup-7.2.0.exe", 8192),
        ),
    )
    assert updates.select_best_asset(rel, "installer").name == "WinGeniusSetup-7.2.0.exe"
    assert updates.select_best_asset(rel, "portable").name == "WinGenius-7.2.0.exe"
    assert updates.select_best_asset(rel, "standalone_exe").name == "WinGenius-7.2.0.exe"


def test_v71_update_url_and_redirect_security_guards():
    from app.core import updates

    # Valid GitHub URLs pass
    assert updates.validate_github_url(updates.RELEASES_URL, kind="api") == updates.RELEASES_URL
    valid_dl = "https://github.com/Pillow1243/WinGenius/releases/download/v7.2.0/WinGeniusSetup-7.2.0.exe"
    assert updates.validate_github_url(valid_dl, kind="download") == valid_dl
    cdn_dl = "https://objects.githubusercontent.com/github-production-release-asset/12345"
    assert updates.validate_github_url(cdn_dl, kind="download") == cdn_dl

    # Plain HTTP, untrusted domains, wrong repo, or embedded credentials fail closed
    for bad_url, kind in [
        ("http://api.github.com/repos/Pillow1243/WinGenius/releases/latest", "api"),
        ("https://evil.example.com/repos/Pillow1243/WinGenius/releases/latest", "api"),
        ("https://api.github.com/repos/Attacker/Malware/releases/latest", "api"),
        ("http://github.com/Pillow1243/WinGenius/releases/download/v7.2.0/a.exe", "download"),
        ("https://github.com/Attacker/Malware/releases/download/v7.2.0/a.exe", "download"),
        ("https://evil-cdn.example.net/WinGeniusSetup.exe", "download"),
        ("https://user:pass@github.com/Pillow1243/WinGenius/releases/download/v7.2.0/a.exe", "download"),
    ]:
        with pytest.raises(updates.UpdateSecurityError):
            updates.validate_github_url(bad_url, kind=kind)

    # Redirect handler blocks cross-origin redirect to untrusted domain
    handler = updates._SafeGitHubRedirectHandler(allow_loopback_test=False)
    req = urllib.request.Request(valid_dl)
    with pytest.raises(updates.UpdateSecurityError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://attacker.invalid/payload.exe")


def test_v71_update_offline_network_drop_and_cancel(tmp_path):
    import http.server
    import socketserver
    from app.core import updates

    # 1. Disconnected / unreachable port never raises in check_latest()
    res = updates.check_latest(
        timeout=1.0,
        url="http://127.0.0.1:9/repos/Pillow1243/WinGenius/releases/latest",
        allow_loopback_test=True,
    )
    assert res["ok"] is False and "error" in res

    # 2. Mid-download connection drop (Content-Length promises 200 KB, server drops after 8 KB)
    pe_bytes = _make_synthetic_pe(b"partial-drop")

    class _DropHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(pe_bytes) * 10))
            self.end_headers()
            self.wfile.write(pe_bytes[:512])
            self.wfile.flush()

        def log_message(self, *args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), _DropHandler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            rel = updates.ReleaseInfo(
                version="9.9.0",
                tag_name="v9.9.0",
                name="Drop Test",
                body="notes",
                html_url="https://github.com/Pillow1243/WinGenius/releases/tag/v9.9.0",
                published_at="",
                assets=(
                    updates.ReleaseAsset(
                        "WinGeniusSetup-9.9.0.exe",
                        f"http://127.0.0.1:{port}/WinGeniusSetup-9.9.0.exe",
                        size=len(pe_bytes) * 10,
                        digest="sha256:" + ("a" * 64),
                    ),
                ),
            )
            stage = tmp_path / "stage_drop"
            with pytest.raises(updates.UpdateError):
                updates.download_and_verify_update(
                    rel,
                    current_version="7.1.0",
                    dest_dir=stage,
                    require_signature=False,
                    allow_loopback_test=True,
                )
            assert list(stage.glob("*")) == []  # zero leftover .part or corrupt .exe files!
        finally:
            httpd.shutdown()

    # 3. User cancellation via cancel_event cleans up .part immediately
    cancel_ev = threading.Event()
    cancel_ev.set()
    with socketserver.TCPServer(("127.0.0.1", 0), _DropHandler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            rel2 = updates.ReleaseInfo(
                version="9.9.0",
                tag_name="v9.9.0",
                name="Cancel Test",
                body="",
                html_url="https://github.com/Pillow1243/WinGenius/releases/tag/v9.9.0",
                published_at="",
                assets=(
                    updates.ReleaseAsset(
                        "WinGeniusSetup-9.9.0.exe",
                        f"http://127.0.0.1:{port}/WinGeniusSetup-9.9.0.exe",
                        size=4096,
                        digest="sha256:" + ("b" * 64),
                    ),
                ),
            )
            stage_cancel = tmp_path / "stage_cancel"
            with pytest.raises(updates.UpdateCancelledError):
                updates.download_and_verify_update(
                    rel2,
                    current_version="7.1.0",
                    dest_dir=stage_cancel,
                    cancel_event=cancel_ev,
                    require_signature=False,
                    allow_loopback_test=True,
                )
            assert list(stage_cancel.glob("*")) == []
        finally:
            httpd.shutdown()


def test_v71_update_cryptographic_manifest_and_tamper_detection(tmp_path, monkeypatch):
    import hashlib
    import http.server
    import socketserver
    from app.core import updates

    # Synthetic PE is not a real signed Windows executable; test Ed25519
    # verification independently, and inject a separate bad signature below.
    monkeypatch.setattr(updates, "verify_authenticode", lambda _path: updates.AuthenticodeResult(
        checked=True, signed=False, valid=False, status="NotSigned"))

    kp = updates.generate_signing_keypair("test-key-2026")
    trusted = {"test-key-2026": kp["public_key_b64"]}

    exe_bytes = _make_synthetic_pe(b"WinGeniusSetup-7.2.0-authentic")
    exe_sha = hashlib.sha256(exe_bytes).hexdigest()

    exe_file = tmp_path / "WinGeniusSetup-7.2.0.exe"
    exe_file.write_bytes(exe_bytes)
    sums_text = f"{exe_sha}  WinGeniusSetup-7.2.0.exe\n"

    signed_manifest = updates.build_release_manifest(
        version="7.2.0",
        tag="v7.2.0",
        files=[exe_file],
        private_key_b64=kp["private_key_b64"],
        key_id="test-key-2026",
    )

    served_files: dict[str, bytes] = {
        "/WinGeniusSetup-7.2.0.exe": exe_bytes,
        "/SHA256SUMS.txt": sums_text.encode("utf-8"),
        "/update-manifest.json": json.dumps(signed_manifest).encode("utf-8"),
    }

    class _ReleaseServer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            srv_port = self.server.server_address[1]
            if self.path == "/repos/Pillow1243/WinGenius/releases/latest":
                payload = json.dumps({
                    "tag_name": "v7.2.0",
                    "name": "WinGenius 7.2.0",
                    "body": "## Highlights\n- Signed Auto-Update",
                    "html_url": "https://github.com/Pillow1243/WinGenius/releases/tag/v7.2.0",
                    "published_at": "2026-11-04T15:00:00Z",
                    "draft": False,
                    "prerelease": False,
                    "assets": [
                        {
                            "name": "WinGeniusSetup-7.2.0.exe",
                            "browser_download_url": f"http://127.0.0.1:{srv_port}/WinGeniusSetup-7.2.0.exe",
                            "size": len(served_files["/WinGeniusSetup-7.2.0.exe"]),
                            "digest": f"sha256:{hashlib.sha256(served_files['/WinGeniusSetup-7.2.0.exe']).hexdigest()}",
                        },
                        {
                            "name": "SHA256SUMS.txt",
                            "browser_download_url": f"http://127.0.0.1:{srv_port}/SHA256SUMS.txt",
                            "size": len(served_files["/SHA256SUMS.txt"]),
                        },
                        {
                            "name": "update-manifest.json",
                            "browser_download_url": f"http://127.0.0.1:{srv_port}/update-manifest.json",
                            "size": len(served_files["/update-manifest.json"]),
                        },
                    ],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            blob = served_files.get(self.path)
            if blob is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, *args):
            return

    with socketserver.TCPServer(("127.0.0.1", 0), _ReleaseServer) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            api_url = f"http://127.0.0.1:{port}/repos/Pillow1243/WinGenius/releases/latest"
            chk = updates.check_latest(url=api_url, current_version="7.1.0", install_mode="installer", allow_loopback_test=True)
            assert chk["ok"] is True and chk["update_available"] is True
            assert "Signed Auto-Update" in chk["body"]

            rel = updates.release_info_from_check(chk)
            stage_ok = tmp_path / "stage_ok"
            pkg = updates.download_and_verify_update(
                rel,
                install_mode="installer",
                current_version="7.1.0",
                dest_dir=stage_ok,
                require_signature=True,
                trusted_pubkeys=trusted,
                allow_loopback_test=True,
            )
            assert pkg.verification.cryptographically_verified is True
            assert pkg.verification.manifest_verified is True
            assert pkg.verification.sha256 == exe_sha
            assert set(pkg.verification.sources_checked) == {
                "github_api_digest", "sha256sums_txt", "update_manifest"
            }

            # Case A: Attacker modifies BOTH the binary AND SHA256SUMS.txt on GitHub Release!
            # Because Ed25519 private key is offline, signed manifest catches tampering immediately!
            tampered_exe = _make_synthetic_pe(b"MALICIOUS-PAYLOAD-INJECTED")
            tampered_sha = hashlib.sha256(tampered_exe).hexdigest()
            served_files["/WinGeniusSetup-7.2.0.exe"] = tampered_exe
            served_files["/SHA256SUMS.txt"] = f"{tampered_sha}  WinGeniusSetup-7.2.0.exe\n".encode("utf-8")
            chk_tampered = updates.check_latest(url=api_url, current_version="7.1.0", install_mode="installer", allow_loopback_test=True)
            with pytest.raises(updates.UpdateTamperedError):
                updates.download_and_verify_update(
                    updates.release_info_from_check(chk_tampered),
                    install_mode="installer",
                    current_version="7.1.0",
                    dest_dir=tmp_path / "stage_tampered",
                    require_signature=True,
                    trusted_pubkeys=trusted,
                    allow_loopback_test=True,
                )

            # Case B: Attacker strips signature from update-manifest.json and updates all hashes ->
            # Default `require_signature=True` STILL blocks installation (`UpdateSignatureRequiredError`)!
            unsigned_manifest = {
                "schema": 1,
                "repo": updates.REPO_SLUG,
                "tag": "v7.2.0",
                "version": "7.2.0",
                "assets": [{"name": "WinGeniusSetup-7.2.0.exe", "sha256": tampered_sha, "size": len(tampered_exe)}],
                "signed": False,
                "key_id": "",
                "signature": "",
            }
            served_files["/update-manifest.json"] = json.dumps(unsigned_manifest).encode("utf-8")
            chk_unsigned = updates.check_latest(url=api_url, current_version="7.1.0", install_mode="installer", allow_loopback_test=True)
            with pytest.raises(updates.UpdateSignatureRequiredError):
                updates.download_and_verify_update(
                    updates.release_info_from_check(chk_unsigned),
                    install_mode="installer",
                    current_version="7.1.0",
                    dest_dir=tmp_path / "stage_unsigned",
                    require_signature=True,
                    trusted_pubkeys=trusted,
                    allow_loopback_test=True,
                )

            # Case C: Version replay attack (valid signature from v7.1.0 replayed on v7.2.0 release)
            replay_manifest = updates.build_release_manifest(
                version="7.1.0",
                tag="v7.1.0",
                files=[exe_file],
                private_key_b64=kp["private_key_b64"],
                key_id="test-key-2026",
            )
            with pytest.raises(updates.UpdateTamperedError, match="replay|mismatch"):
                updates.verify_release_manifest(
                    replay_manifest, expected_version="7.2.0", trusted_pubkeys=trusted
                )

            # Case D: Tampered Windows Authenticode (`HashMismatch`) is unconditionally rejected
            served_files["/WinGeniusSetup-7.2.0.exe"] = exe_bytes
            served_files["/SHA256SUMS.txt"] = sums_text.encode("utf-8")
            served_files["/update-manifest.json"] = json.dumps(signed_manifest).encode("utf-8")
            chk_auth_bad = updates.check_latest(url=api_url, current_version="7.1.0", install_mode="installer", allow_loopback_test=True)
            with pytest.raises(updates.UpdateTamperedError):
                updates.download_and_verify_update(
                    updates.release_info_from_check(chk_auth_bad),
                    install_mode="installer",
                    current_version="7.1.0",
                    dest_dir=tmp_path / "stage_auth_bad",
                    require_signature=True,
                    trusted_pubkeys=trusted,
                    authenticode_checker=lambda _p: updates.AuthenticodeResult(
                        checked=True, signed=True, valid=False, status="HashMismatch", detail="Bad PE digest"
                    ),
                    allow_loopback_test=True,
                )
        finally:
            httpd.shutdown()

    # Case E: Non-PE file (e.g. HTML error page) with `.exe` extension is rejected immediately
    fake_exe = tmp_path / "fake.exe"
    fake_exe.write_bytes(b"<html>502 Bad Gateway</html>" * 100)
    with pytest.raises(updates.UpdateTamperedError, match="MZ"):
        updates.verify_pe_executable(fake_exe)


def test_v71_update_upgrade_data_vault_preservation_and_rollback(tmp_path):
    import sqlite3
    from app.core import updates, vault

    profile_dir = tmp_path / "user_data"
    profile_dir.mkdir()
    (profile_dir / "settings.ini").write_text("[ui]\ntheme=midnight\nlanguage=fa\n", encoding="utf-8")
    (profile_dir / "schedules.json").write_text('[{"id":"s1","name":"daily"}]', encoding="utf-8")
    (profile_dir / "macros").mkdir()
    (profile_dir / "macros" / "my_flow.wgmacro").write_text('{"name":"keep-me","steps":[]}', encoding="utf-8")

    # Live SQLite undo journal
    jdb = profile_dir / "journal.sqlite3"
    with sqlite3.connect(str(jdb)) as conn:
        conn.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO entries (note) VALUES ('important-undo-record')")
        conn.commit()

    # User's encrypted WGV2 Vault + personal files
    personal_dir = tmp_path / "Documents"
    personal_dir.mkdir()
    secret_doc = personal_dir / "contract.txt"
    secret_doc.write_text("Top secret user document — must survive every update!", encoding="utf-8")
    vault_file = personal_dir / "personal.wgv"
    v_obj = vault.Vault(vault_file, "VaultPass!71")
    v_obj.create()
    v_obj.add([secret_doc])

    # Simulated installed/portable executable v7.1.0 -> v7.2.0
    app_dir = tmp_path / "app_bin"
    app_dir.mkdir()
    exe_v71 = _make_synthetic_pe(b"WinGenius-7.1.0-orig")
    exe_v72 = _make_synthetic_pe(b"WinGenius-7.2.0-new")
    target_exe = app_dir / "WinGenius.exe"
    target_exe.write_bytes(exe_v71)

    staged_v72 = tmp_path / "staged" / "WinGenius-7.2.0.exe"
    staged_v72.parent.mkdir()
    staged_v72.write_bytes(exe_v72)
    v72_sha = updates.sha256_file(staged_v72)

    # 1. Prepare pre-update rollback snapshot & apply v7.2.0 swap
    rb_state = updates.prepare_rollback_snapshot(
        target_version="7.2.0",
        install_mode="portable",
        current_version="7.1.0",
        current_exe=target_exe,
        root=profile_dir,
    )
    assert rb_state["status"] == "pending"
    assert Path(rb_state["backup_exe_path"]).is_file()
    assert Path(rb_state["data_backup_dir"]).is_dir()

    swap_res = updates.apply_portable_swap(
        staged_v72,
        target_exe,
        expected_sha256=v72_sha,
        health_check=lambda p: b"WinGenius-7.2.0-new" in p.read_bytes(),
        root=profile_dir,
    )
    assert swap_res["ok"] is True
    assert target_exe.read_bytes() == exe_v72

    # 2. Confirm post-update health on v7.2.0 launch
    verified_state = updates.confirm_post_update_health(running_version="7.2.0", root=profile_dir)
    assert verified_state["status"] == "verified"

    # 3. Verify ALL user settings, SQLite journal, macros, Vault, and personal files survived intact!
    assert "midnight" in (profile_dir / "settings.ini").read_text(encoding="utf-8")
    assert (profile_dir / "macros" / "my_flow.wgmacro").is_file()
    with sqlite3.connect(str(jdb)) as conn:
        row = conn.execute("SELECT note FROM entries").fetchone()
        assert row == ("important-undo-record",)
    extracted_dir = tmp_path / "vault_verify"
    vault.Vault(vault_file, "VaultPass!71").extract_all(extracted_dir)
    assert (extracted_dir / "contract.txt").read_text(encoding="utf-8") == (
        "Top secret user document — must survive every update!"
    )

    # 4. Simulate a BROKEN v7.3.0 update whose health check fails -> automatic rollback to v7.2.0!
    bad_v73 = _make_synthetic_pe(b"WinGenius-7.3.0-corrupt-runtime")
    staged_v73 = tmp_path / "staged" / "WinGenius-7.3.0.exe"
    staged_v73.write_bytes(bad_v73)
    with pytest.raises(updates.UpdateError, match="restored previous executable"):
        updates.apply_portable_swap(
            staged_v73,
            target_exe,
            expected_sha256=updates.sha256_file(staged_v73),
            health_check=lambda _p: False,  # simulates broken startup
            root=profile_dir,
        )
    assert target_exe.read_bytes() == exe_v72  # automatically restored v7.2.0!

    # 5. Explicit manual rollback restores backed-up v7.1.0 executable cleanly
    updates.prepare_rollback_snapshot(
        target_version="7.2.0",
        install_mode="portable",
        current_version="7.1.0",
        current_exe=Path(rb_state["backup_exe_path"]),
        root=profile_dir,
    )
    # Point target_exe_path to target_exe in rollback state and run rollback_previous_version
    st = updates.get_rollback_state(profile_dir)
    st["target_exe_path"] = str(target_exe)
    atomic_json_write(updates.rollback_state_path(profile_dir), st)
    rb_done = updates.rollback_previous_version(target_exe=target_exe, restore_data=True, root=profile_dir)
    assert rb_done["ok"] is True and rb_done["previous_version"] == "7.1.0"
    assert target_exe.read_bytes() == exe_v71


def test_v71_update_non_blocking_gui_banner_changelog_and_confirmation(tmp_path, monkeypatch):
    import hashlib
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.core import updates
    from app.ui.update_dialog import UpdateBanner, UpdateDialog

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("WINGENIUS_NO_LAUNCH", "1")

    sample_check = {
        "ok": True,
        "current": "7.1.0",
        "latest": "7.2.0",
        "tag_name": "v7.2.0",
        "name": "WinGenius 7.2.0",
        "body": "### Release Notes 7.2.0\n- Non-blocking QThread downloader\n- Ed25519 manifest check",
        "published_at": "2026-11-04",
        "url": "https://github.com/Pillow1243/WinGenius/releases/tag/v7.2.0",
        "install_mode": "installer",
        "update_available": True,
        "asset": {
            "name": "WinGeniusSetup-7.2.0.exe",
            "url": "https://github.com/Pillow1243/WinGenius/releases/download/v7.2.0/WinGeniusSetup-7.2.0.exe",
            "size": 4096,
            "digest": "",
            "content_type": "application/octet-stream",
        },
        "assets": [],
    }

    banner = UpdateBanner(lang="fa")
    banner.show_release(sample_check)
    assert not banner.isHidden()
    assert "7.2.0" in banner._text.text()
    banner._dismiss()
    assert banner.isHidden()

    dlg = UpdateDialog(lang="en", initial_check=sample_check)
    assert "Release Notes 7.2.0" in dlg._changelog.toPlainText()
    assert dlg._download_btn.isEnabled() is True
    assert dlg._install_btn.isEnabled() is False

    # Simulate verified package arrival from UpdateDownloadWorker without blocking event loop
    exe_path = tmp_path / "WinGeniusSetup-7.2.0.exe"
    exe_bytes = _make_synthetic_pe(b"gui-test")
    exe_path.write_bytes(exe_bytes)
    exe_sha = hashlib.sha256(exe_bytes).hexdigest()
    verified_pkg = updates.VerifiedUpdatePackage(
        version="7.2.0",
        tag_name="v7.2.0",
        asset_name="WinGeniusSetup-7.2.0.exe",
        file_path=exe_path,
        install_mode="installer",
        verification=updates.VerificationReport(
            ok=True,
            cryptographically_verified=True,
            sha256=exe_sha,
            size=len(exe_bytes),
            sources_checked=("update_manifest", "sha256sums_txt"),
            manifest_verified=True,
            manifest_key_id="wingenius-release-2026-01",
            pe_valid=True,
            trust_summary_fa="تأیید شد",
            trust_summary_en="Cryptographically verified",
        ),
    )
    # This is a direct synchronous slot call, not a queued worker signal.
    dlg._on_download_verified(verified_pkg)
    assert dlg._install_btn.isEnabled() is True
    assert exe_sha in dlg._trust_lbl.text()

    # User declines confirmation -> apply_verified_update MUST NOT be called!
    launched_events = []
    dlg.updateLaunched.connect(launched_events.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dlg.confirm_and_install()
    assert launched_events == []

    # User approves confirmation -> apply_verified_update runs and emits updateLaunched!
    monkeypatch.setattr(updates, "data_dir", lambda: tmp_path / "gui_profile")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg.confirm_and_install()
    assert len(launched_events) == 1 and launched_events[0]["ok"] is True
    dlg.deleteLater()


