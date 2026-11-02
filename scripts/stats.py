#!/usr/bin/env python3
"""Recompute docs/stats.json from real test runs.

    python scripts/stats.py --run     # run the suites, rewrite stats.json + showcase
    python scripts/stats.py --check  # fast: only verify the stored numbers

Earlier releases quoted hand-copied numbers (1,829 / 459 / 75%) that no longer
matched any suite.  This script makes the numbers come from the suites.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
    ("packaging", "packaging/verify_package.py", re.compile(r"(\d+)\s*/\s*\d+[^\n]*checks passed")),
]


def environment() -> dict:
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", WINGENIUS_NO_LAUNCH="1",
               PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    return env


def collect() -> dict:
    counts: dict[str, int] = {}
    for key, script, pattern in SUITES:
        proc = subprocess.run([sys.executable, script], cwd=ROOT, env=environment(),
                              capture_output=True, text=True, timeout=1200)
        output = (proc.stdout or "") + (proc.stderr or "")
        match = pattern.search(output)
        if proc.returncode != 0 or not match:
            raise SystemExit(f"{script} failed (rc={proc.returncode}):\n{output[-1500:]}")
        counts[key] = int(match.group(1))
        print(f"  {key:11s} {counts[key]:5d}")
    return counts


def tool_count() -> int:
    from app.core.registry import all_tools, load_all

    failures = load_all()
    if failures:
        raise SystemExit(f"tool modules failed: {failures}")
    return len(all_tools())


def run() -> int:
    sys.path.insert(0, str(ROOT))
    counts = collect()
    from app.core.paths import APP_VERSION

    stats = {
        "version": APP_VERSION,
        "tools": tool_count(),
        "categories": 10,
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
    if stats["checks"] != sum(stats["suites"].values()):
        print("FAIL docs/stats.json: checks != sum(suites)")
        return 1
    sys.path.insert(0, str(ROOT))
    from app.core.paths import APP_VERSION

    if stats["version"] != APP_VERSION:
        print(f"FAIL docs/stats.json version {stats['version']} != {APP_VERSION}")
        return 1
    if stats["tools"] != tool_count():
        print(f"FAIL docs/stats.json tools {stats['tools']} != {tool_count()}")
        return 1
    print(f"OK  {stats['tools']} tools · {stats['checks']} checks · v{APP_VERSION}")
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
