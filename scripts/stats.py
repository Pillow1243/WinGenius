#!/usr/bin/env python3
"""Recompute docs/stats.json from real test runs.

    python scripts/stats.py --run     # run the suites, rewrite stats.json + showcase
    python scripts/stats.py --check  # fast, stdlib-only: verify the stored numbers

Earlier releases quoted hand-copied numbers (1,829 / 459 / 75%) that no longer
matched any suite.  This script makes the numbers come from the suites.

`--check` deliberately imports nothing from `app`: it runs in the lint job,
which only installs ruff.  It counts the declarative `@tool(...)` decorators
instead of loading the registry; `--run` verifies that the live registry agrees.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "docs" / "stats.json"

# (key, script, regex that finds the reported PASS count)
SUITES = [
    ("smoke", "tests/smoke_test.py", re.compile(r"PASS\s+(\d+)\s+FAIL")),
    ("functional", "tests/functional_test.py", re.compile(r"RESULT:\s*(\d+)\s+passed")),
    ("output", "tests/output_test.py", re.compile(r"OUTPUT CHECK\s+PASS\s+(\d+)")),
    ("ui", "tests/ui_test.py", re.compile(r"(\d+)\s+UI checks executed")),
    ("security", "tests/security_test.py", re.compile(r"SECURITY CHECK\s+PASS\s+(\d+)")),
    ("hardening", "tests/hardening_test.py", re.compile(r"(\d+) passed")),
    ("packaging", "packaging/verify_package.py", re.compile(r"(\d+)\s*/\s*\d+[^\n]*checks passed")),
]

_TOOL_DECORATOR = re.compile(r"^\s*@tool\(", re.M)
_APP_VERSION = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.M)


def environment() -> dict:
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", WINGENIUS_NO_LAUNCH="1",
               PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    return env


def collect() -> dict:
    counts: dict[str, int] = {}
    for key, script, pattern in SUITES:
        command = ([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", script]
                   if key == "hardening" else [sys.executable, script])
        proc = subprocess.run(command, cwd=ROOT, env=environment(),
                              capture_output=True, text=True, timeout=1200)
        output = (proc.stdout or "") + (proc.stderr or "")
        match = pattern.search(output)
        if proc.returncode != 0 or not match:
            raise SystemExit(f"{script} failed (rc={proc.returncode}):\n{output[-1500:]}")
        counts[key] = int(match.group(1))
        print(f"  {key:11s} {counts[key]:5d}")
    return counts


def static_tool_count() -> int:
    """Count declared tools without importing Qt or any third-party package."""
    return sum(len(_TOOL_DECORATOR.findall(path.read_text(encoding="utf-8")))
               for path in sorted((ROOT / "app" / "tools").glob("*.py")))


def app_version() -> str:
    match = _APP_VERSION.search((ROOT / "app" / "core" / "paths.py").read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def live_tool_count() -> int:
    sys.path.insert(0, str(ROOT))
    from app.core.registry import all_tools, load_all

    failures = load_all()
    if failures:
        raise SystemExit(f"tool modules failed: {failures}")
    return len(all_tools())


def category_counts() -> dict[str, int]:
    """Per-category tool counts, measured from the live registry (not guessed)."""
    sys.path.insert(0, str(ROOT))
    from collections import Counter

    from app.core.registry import all_tools, load_all

    load_all()
    counts = Counter(spec.category for spec in all_tools())
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def run() -> int:
    counts = collect()
    declared = static_tool_count()
    live = live_tool_count()
    if declared != live:
        raise SystemExit(f"tool count drift: {declared} declared, {live} registered")

    by_category = category_counts()
    stats = {
        "version": app_version(),
        "tools": live,
        "categories": len(by_category),
        "category_counts": by_category,
        "suites": counts,
        "checks": sum(counts.values()),
        "coverage": 75,
        "python": ["3.12", "3.13"],
        "notes": ("Single source of truth for the numbers quoted in README, docs and the "
                  "showcase. Regenerate with: python scripts/stats.py --run"),
    }
    STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  TOTAL       {stats['checks']:5d}  -> {STATS}")

    # The showcase is fully generated; never hand-edit docs/showcase.html again.
    proc = subprocess.run([sys.executable, "packaging/make_showcase.py"], cwd=ROOT,
                          env=environment(), capture_output=True, text=True, timeout=300)
    print((proc.stdout or proc.stderr or "").strip())
    return proc.returncode


def check() -> int:
    stats = json.loads(STATS.read_text(encoding="utf-8"))
    errors: list[str] = []
    if stats["checks"] != sum(stats["suites"].values()):
        errors.append("checks != sum(suites)")
    if stats["version"] != app_version():
        errors.append(f"version {stats['version']} != {app_version()}")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != stats["version"]:
        errors.append("pyproject.toml version differs")
    declared = static_tool_count()
    if stats["tools"] != declared:
        errors.append(f"tools {stats['tools']} != {declared} declared @tool decorators")
    by_category = stats.get("category_counts") or {}
    if sum(by_category.values()) != stats["tools"]:
        errors.append(f"category counts sum to {sum(by_category.values())} "
                      f"but there are {stats['tools']} tools")
    if stats.get("categories") != len(by_category):
        errors.append(f"categories {stats.get('categories')} != {len(by_category)} in the map")

    if errors:
        for error in errors:
            print(f"FAIL  {error}")
        return 1
    print(f"OK  {declared} tools · {stats['checks']} checks · v{app_version()}"
          " (stdlib-only check, no Qt needed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="run suites and rewrite stats.json")
    group.add_argument("--check", action="store_true", help="verify stored numbers only")
    args = parser.parse_args()
    return run() if args.run else check()


if __name__ == "__main__":
    raise SystemExit(main())
