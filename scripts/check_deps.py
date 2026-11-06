#!/usr/bin/env python3
"""Fail when requirements.txt, constraints.txt and [project.dependencies] drift apart.

The wheel used to omit mss, opencv and pywin32, and the frozen exe shipped
without pyHanko even though the UI advertised PDF signing.  One list is now
canonical; this check keeps the other honest.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"
CONSTRAINTS = ROOT / "constraints.txt"

# Installed by CI next to the runtime set, so they are pinned here too but are
# not part of [project.dependencies].
TOOLING = {"pytest", "coverage"}

NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def canonical(name: str) -> str:
    """PEP 503: case, dots, hyphens and underscores are equivalent."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def names(lines: list[str]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = line.split(";", 1)[0].strip()   # drop environment markers
        match = NAME.match(line)
        if match:
            found.add(canonical(match.group(1)))
    return found


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = names(project["project"]["dependencies"])
    pinned = names(REQUIREMENTS.read_text(encoding="utf-8").splitlines())

    missing = sorted(declared - pinned)     # in the wheel but not the dev install
    extra = sorted(pinned - declared)       # in the dev install but not the wheel
    print(f"pyproject.toml : {len(declared)} runtime dependencies")
    print(f"requirements.txt: {len(pinned)} runtime dependencies")
    if missing:
        print(f"  MISSING from requirements.txt: {', '.join(missing)}")
    if extra:
        print(f"  MISSING from pyproject.toml  : {', '.join(extra)}")
    if missing or extra:
        return 1
    if missing or extra:
        pass
    else:
        print("dependency lists are in sync ✓")

    # audit F-12: every requirement needs an explicit pin, otherwise CI keeps
    # re-resolving the dependency graph on every run.
    problems = list(missing) and ["requirements/pyproject drift"] or []
    if CONSTRAINTS.is_file():
        pins = {}
        for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            line = line.split(";", 1)[0].strip()
            name, _, version = line.partition("==")
            match = NAME.match(name)
            if match and version.strip():
                pins[canonical(match.group(1))] = version.strip()
        expected = (declared | TOOLING)
        unpinned = sorted(expected - set(pins))
        unknown = sorted(set(pins) - expected)
        print(f"constraints.txt : {len(pins)} pinned versions")
        if unpinned:
            print(f"  NOT pinned in constraints.txt: {', '.join(unpinned)}")
        if unknown:
            print(f"  pinned but not required      : {', '.join(unknown)}")
        problems = bool(unpinned or unknown)
    else:
        print("constraints.txt : MISSING")
        problems = True

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
