#!/usr/bin/env python3
"""Fail-fast source gate shared by release builds and local validation.

Requires runtime dependencies plus pytest and coverage. All native processes
are checked individually, including on PowerShell where an earlier non-zero
exit code can otherwise be masked by a later successful process.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]


def run_checks(commands: Sequence[Sequence[str]]) -> int:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", WINGENIUS_NO_LAUNCH="1",
               PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode:
            print(f"QUALITY GATE FAILED (exit {result.returncode})", file=sys.stderr)
            return result.returncode if result.returncode > 0 else 1
    return 0


def main() -> int:
    python = sys.executable
    commands = [[python, script] for script in (
        "scripts/check_deps.py", "scripts/check_release.py", "scripts/check_docs.py",
        "packaging/verify_package.py",
    )]
    commands.extend([
        [python, "main.py", "--doctor"],
        [python, "-m", "compileall", "-q", "app", "tests", "packaging", "scripts", "main.py"],
        [python, "-m", "coverage", "erase"],
    ])
    for suite in ("smoke", "functional", "output", "ui", "security"):
        commands.append([python, "-m", "coverage", "run", "--parallel-mode", "--source=app",
                         f"tests/{suite}_test.py"])
    commands.extend([
        [python, "-m", "coverage", "run", "--parallel-mode", "--source=app", "-m", "pytest",
         "tests/hardening_test.py", "-q"],
        [python, "-m", "coverage", "combine"],
        [python, "-m", "coverage", "report", "--skip-covered", "--skip-empty"],
    ])
    return run_checks(commands)


if __name__ == "__main__":
    raise SystemExit(main())
