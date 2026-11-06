"""F-13 regression test — 2026-11 independent audit.

`[tool.mypy]` was configured but no CI job ever ran mypy, and the recorded
config (`check_untyped_defs = false`) proves nothing on its own. The gate now
runs against a per-file baseline: no new type errors, no new failing file.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "mypy_baseline.json"

needs_mypy = pytest.mark.skipif(importlib.util.find_spec("mypy") is None,
                                reason="mypy is not installed in this environment")


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_f13_baseline_exists_and_is_consistent():
    data = _baseline()
    files = data.get("files") or {}
    assert files, "the baseline must record at least one file"
    assert data.get("total") == sum(files.values()), "total must equal the per-file sum"
    for path, count in files.items():
        assert (ROOT / path).is_file(), f"baseline mentions a missing file: {path}"
        assert int(count) > 0, f"{path} is recorded with {count} errors; drop the entry"


def test_f13_ci_runs_the_type_gate():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/typecheck.py" in workflow, "CI never runs the type gate"
    assert "mypy" in workflow
    # the baseline was recorded with the runtime dependencies installed; a bare
    # interpreter degrades imports to Any and changes every count
    job = workflow.split("types:", 1)[1].split("\n  tests:", 1)[0]
    assert "requirements.txt" in job, "the type job must install the pinned runtime deps"
    assert "constraints.txt" in job, "the type job must resolve the pinned graph"


def test_f13_counts_are_environment_sensitive_so_only_growth_fails():
    """Fewer errors than recorded is not a regression (missing optional deps)."""
    spec = importlib.util.spec_from_file_location("typecheck", ROOT / "scripts" / "typecheck.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    baseline = {"files": {"app/core/spec.py": 5, "app/ui/widgets.py": 3}}
    # fewer errors / a clean file: fine in the default mode, flagged as "better"
    counts = {"app/core/spec.py": 2}
    assert module.check(counts, baseline, strict=False) == []
    assert module.improvements(counts, baseline), "the drop should be reported as a note"
    # …but the same situation is a stale baseline in strict mode
    assert module.check(counts, baseline, strict=True)


def test_f13_script_ships_with_the_repo():
    script = ROOT / "scripts" / "typecheck.py"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "--update" in text and "baseline" in text.lower()


@needs_mypy
def test_f13_no_new_type_errors(tmp_path):
    """The real gate: mypy must not report more errors than the baseline records."""
    result = subprocess.run([sys.executable, "scripts/typecheck.py"],
                            cwd=ROOT, capture_output=True, text=True, timeout=1800)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "none new" in result.stdout


def test_f13_gate_detects_a_regression(tmp_path):
    """A made-up file with a type error must be rejected by the checker logic."""
    spec = importlib.util.spec_from_file_location("typecheck", ROOT / "scripts" / "typecheck.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    counts = {"app/core/spec.py": 3, "app/new_module.py": 1}
    problems = module.check(counts, {"files": {"app/core/spec.py": 2}}, strict=False)
    assert any("app/new_module.py" in problem for problem in problems), problems
    assert any("app/core/spec.py" in problem and "baseline allows 2" in problem
               for problem in problems), problems

    # a clean run against an exact baseline reports nothing
    assert module.check({"app/core/spec.py": 2}, {"files": {"app/core/spec.py": 2}},
                        strict=True) == []
