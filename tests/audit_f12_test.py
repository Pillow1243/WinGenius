"""F-12 regression test — 2026-11 independent audit.

requirements.txt only carried ranges, so every CI run re-resolved the whole
dependency graph: one upstream breaking release could turn a green build red
with no commit of ours, and two jobs could test different versions.
constraints.txt now pins the known-good set and CI installs with `-c`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "constraints.txt"
NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _pins() -> dict:
    pins = {}
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = line.split(";", 1)[0].strip()
        name, _, version = line.partition("==")
        match = NAME.match(name)
        if match:
            pins[canonical(match.group(1))] = version.strip()
    return pins


def _requirements() -> set:
    names = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = line.split(";", 1)[0].strip()
        match = NAME.match(line)
        if match:
            names.add(canonical(match.group(1)))
    return names


def test_f12_constraints_file_exists():
    assert CONSTRAINTS.is_file(), "constraints.txt is required"


def test_f12_every_requirement_is_pinned_to_an_exact_version():
    pins = _pins()
    missing = sorted(_requirements() - set(pins))
    assert not missing, f"unpinned requirements: {missing}"
    weak = sorted(name for name, version in pins.items()
                  if not re.fullmatch(r"\d+(\.\d+)*([.-]\w+)?", version))
    assert not weak, f"these pins are not exact versions: {weak}"


def test_f12_pins_are_inside_the_declared_ranges():
    """A pin outside requirements.txt's range would make the install unusable."""
    from packaging.requirements import Requirement

    pins = _pins()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        requirement = Requirement(line.split(";", 1)[0].strip())
        pinned = pins.get(canonical(requirement.name))
        assert pinned, f"{requirement.name} is not pinned"
        assert requirement.specifier.contains(pinned, prereleases=True), (
            f"{requirement.name}=={pinned} is outside {requirement.specifier}")


def test_f12_ci_installs_with_the_constraints():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installs = [line for line in workflow.splitlines()
                if "pip install" in line and "requirements.txt" in line]
    assert installs, "ci.yml does not install requirements.txt"
    for line in installs:
        assert "-c constraints.txt" in line, f"CI install without constraints: {line.strip()}"


def test_f12_check_deps_covers_constraints():
    result = subprocess.run([sys.executable, "scripts/check_deps.py"],
                            cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "constraints.txt" in result.stdout
