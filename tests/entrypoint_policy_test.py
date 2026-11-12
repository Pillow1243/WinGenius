"""Execution entry points default to the shared, fail-closed policy."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import registry  # noqa: E402
from app.core.execution import PolicyDeniedError  # noqa: E402
from app.core.spec import RunContext, RunResult, ToolSpec  # noqa: E402


def test_registry_invoke_routes_through_execution_service_and_denies_risk(monkeypatch):
    calls = []
    safe = ToolSpec(
        id="entrypoint_probe",
        category="dev",
        name="probe",
        name_en="probe",
        run=lambda _ctx, values: calls.append(dict(values)) or RunResult(message="executed"),
    )
    dangerous = ToolSpec(
        id="entrypoint_danger_probe",
        category="dev",
        name="danger",
        name_en="danger",
        danger=True,
        run=lambda _ctx, _values: calls.append("danger-executed") or RunResult(),
    )
    monkeypatch.setitem(registry._TOOLS, safe.id, safe)
    monkeypatch.setitem(registry._TOOLS, dangerous.id, dangerous)

    result = registry.invoke(safe.id, RunContext(lang="en"), {"value": 7})
    assert result.message == "executed"
    assert calls == [{"value": 7}]

    with pytest.raises(PolicyDeniedError):
        registry.invoke(dangerous.id, RunContext(lang="en"), {})
    assert "danger-executed" not in calls
