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
    monkeypatch.setattr(panel, "_confirm_execution", lambda: True)
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
