#!/usr/bin/env python3
"""Fail when requirements.txt and [project.dependencies] drift apart.

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
    print("dependency lists are in sync ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
