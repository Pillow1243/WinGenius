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

# (key, script, PASS-count regex, SKIP-count regex) — audit F-16: the published
# numbers count every check the suite *reaches* (PASS + SKIP, where a SKIP is a
# platform-gated check such as tesseract or an opt-in live network probe).
# Counting PASS only made the published totals impossible to reproduce on any
# machine that lacks one of those tools.
SUITES = [
    ("smoke", "tests/smoke_test.py",
     re.compile(r"PASS\s+(\d+)\s+FAIL"), re.compile(r"PASS\s+\d+\s+FAIL\s+\d+\s+SKIP\s+(\d+)")),
    ("functional", "tests/functional_test.py",
     re.compile(r"RESULT:\s*(\d+)\s+passed"), re.compile(r"RESULT:\s*\d+\s+passed,\s*\d+\s+failed,\s*(\d+)\s+skipped")),
    ("output", "tests/output_test.py",
     re.compile(r"OUTPUT CHECK\s+PASS\s+(\d+)"), re.compile(r"OUTPUT CHECK\s+PASS\s+\d+\s+FAIL\s+\d+\s+SKIP\s+(\d+)")),
    ("ui", "tests/ui_test.py",
     re.compile(r"(\d+)\s+UI checks executed"), re.compile(r"(\d+)\s+UI checks skipped")),
    ("security", "tests/security_test.py",
     re.compile(r"SECURITY CHECK\s+PASS\s+(\d+)"), re.compile(r"SECURITY CHECK\s+PASS\s+\d+\s+FAIL\s+\d+\s+SKIP\s+(\d+)")),
    ("hardening", "tests/hardening_test.py", re.compile(r"(\d+) passed"), None),
    ("persian", "tests/persian_pdf_test.py", re.compile(r"(\d+) passed"), None),
    ("packaging", "packaging/verify_package.py",
     re.compile(r"(\d+)\s*/\s*\d+[^\n]*checks passed"), None),
]

_TOOL_DECORATOR = re.compile(r"^\s*@tool\(", re.M)
_APP_VERSION = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.M)


def environment() -> dict:
    env = dict(os.environ)
    env.update(QT_QPA_PLATFORM="offscreen", WINGENIUS_NO_LAUNCH="1",
               PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1")
    return env


def collect(diagnostics: dict | None = None, skips: dict | None = None,
            only: set | None = None) -> dict:
    """Run every suite and return {key: total checks reached}.

    "Total" is PASS + SKIP (see `SUITES`), which is the number the docs quote.
    `skips`, when given, receives {key: skipped count}. `only`, when given,
    restricts the run to those suite keys.

    `diagnostics`, when given, receives each suite's raw output so a mismatch can
    explain itself (which checks were skipped, what failed) instead of only saying
    "642 != 641".
    """
    counts: dict[str, int] = {}
    for key, script, pattern, skip_pattern in SUITES:
        if only is not None and key not in only:
            continue
        command = ([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", script]
                   if key in ("hardening", "persian") else [sys.executable, script])
        proc = subprocess.run(command, cwd=ROOT, env=environment(),
                              capture_output=True, text=True, timeout=1200)
        output = (proc.stdout or "") + (proc.stderr or "")
        if diagnostics is not None:
            diagnostics[key] = output
        match = pattern.search(output)
        if proc.returncode != 0 or not match:
            raise SystemExit(f"{script} failed (rc={proc.returncode}):\n{output[-1500:]}")
        passed = int(match.group(1))
        skipped = 0
        if skip_pattern is not None:
            skip_match = skip_pattern.search(output)
            skipped = int(skip_match.group(1)) if skip_match else 0
        counts[key] = passed + skipped
        if skips is not None:
            skips[key] = skipped
        print(f"  {key:11s} {counts[key]:5d}" + (f"  (PASS {passed} + SKIP {skipped})"
                                                  if skipped else ""))
    return counts


def measured_coverage():
    """Return the real line-coverage percent from coverage.py data, or None.

    `scripts/quality_gate.py` runs the suites under `coverage run`; when that
    data exists we report it.  When it does not (lint-only job, fresh clone)
    we report null instead of inventing a number.
    """
    try:
        import coverage

        cov = coverage.Coverage(data_file=str(ROOT / ".coverage"))
        cov.load()
        files = list(cov.get_data().measured_files())
        if not files:
            return None
        total = covered = 0
        for fname in files:
            try:
                _f, statements, _excl, missing, _fmt = cov.analysis2(fname)
            except Exception:
                continue
            total += len(statements)
            covered += len(statements) - len(missing)
        return int(round(100.0 * covered / total)) if total else None
    except Exception:
        return None


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


def run(only: set | None = None) -> int:
    """Re-measure the suites and rewrite docs/stats.json.

    `only` limits the run to the named suites; the stored value of every other
    suite is kept and listed under "unmeasured" so a machine that cannot run
    one suite (e.g. a Qt-less box) can still refresh the rest without pretending
    it measured everything.
    """
    previous = {}
    if STATS.is_file():
        try:
            previous = json.loads(STATS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    skips: dict[str, int] = {}
    counts = collect(skips=skips, only=only)
    unmeasured: list[str] = []
    for key, _script, _pattern, _skip in SUITES:
        if key in counts:
            continue
        stored = (previous.get("suites") or {}).get(key)
        if stored is None:
            raise SystemExit(f"no stored value for suite {key!r}; run it before publishing")
        counts[key] = int(stored)
        skips[key] = int((previous.get("skips") or {}).get(key, 0))
        unmeasured.append(key)
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
        "suites": dict(sorted(counts.items())),
        "skips": dict(sorted(skips.items())),
        "checks": sum(counts.values()),
        # Never a hand-copied constant: read the real coverage.py data when the
        # quality gate produced it, otherwise report null ("not measured").
        "coverage": measured_coverage(),
        "python": ["3.12", "3.13"],
        "notes": ("Single source of truth for the numbers quoted in README, docs and the "
                  "showcase. Regenerate with: python scripts/stats.py --run. "
                  "A suite's number counts every check it reaches: PASS + SKIP."),
    }
    if unmeasured:
        # audit F-16: say out loud what was not re-measured on this machine
        stats["unmeasured"] = sorted(unmeasured)
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
    skips = stats.get("skips") or {}
    for key, skipped in skips.items():
        if key not in stats["suites"]:
            errors.append(f"skips mentions unknown suite {key!r}")
        elif int(skipped) > int(stats["suites"][key]):
            errors.append(f"{key}: {skipped} skips exceed its {stats['suites'][key]} checks")
    for key in stats.get("unmeasured") or []:
        if key not in stats["suites"]:
            errors.append(f"unmeasured mentions unknown suite {key!r}")
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


def verify() -> int:
    """Re-run the suites and fail if the stored numbers no longer reproduce.

    `--check` only proves the file is internally consistent; this proves the
    published "2,336 checks" is what the suites actually report today.
    """
    declared = json.loads(STATS.read_text(encoding="utf-8"))
    diagnostics: dict[str, str] = {}
    counts = collect(diagnostics)
    errors: list[str] = []
    for key, value in counts.items():
        stored = (declared.get("suites") or {}).get(key)
        if stored != value:
            errors.append(f"{key}: stats.json says {stored}, the suite reports {value}")
    if declared.get("checks") != sum(counts.values()):
        errors.append(f"checks: stats.json says {declared.get('checks')}, "
                      f"the suites report {sum(counts.values())}")
    try:
        live = live_tool_count()
    except SystemExit as exc:
        errors.append(str(exc))
        live = 0
    if live and declared.get("tools") != live:
        errors.append(f"tools: stats.json says {declared.get('tools')}, the registry has {live}")
    if errors:
        for error in errors:
            print(f"STALE  {error}")
            # GitHub turns this into a check-run annotation, i.e. the reason a red
            # "stats" job failed is visible without downloading the job log.
            print(f"::error title=docs/stats.json is stale::{error}")
        print("\nRun `python scripts/stats.py --run` and commit docs/stats.json.")
        _explain_mismatches(errors, counts, declared, diagnostics)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            try:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write("### docs/stats.json is stale\n\n")
                    for error in errors:
                        handle.write(f"- {error}\n")
                    handle.write("\nFix with `python scripts/stats.py --run`.\n")
            except OSError:
                pass
        return 1
    print(f"OK  {live} tools · {sum(counts.values())} checks reproduced by the suites")
    return 0


def _explain_mismatches(errors: list[str], counts: dict, declared: dict,
                        diagnostics: dict) -> None:
    """Attach the skipped/failed lines of every mismatching suite to the report."""
    for key, value in counts.items():
        if (declared.get("suites") or {}).get(key) == value:
            continue
        output = diagnostics.get(key, "")
        interesting = [line.strip() for line in output.splitlines()
                       if line.strip().startswith(("SKIP", "FAILED", "RESULT", "OUTPUT CHECK",
                                                   "SECURITY CHECK", "PASS  ") )
                       or " skipped" in line or " passed" in line]
        detail = " | ".join(interesting[-10:])[:900]
        if not detail:                      # nothing familiar: show the tail
            detail = " ".join(output.strip().splitlines()[-3:])[:900]
        print(f"::error title={key} count changed::{detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="run suites and rewrite stats.json")
    group.add_argument("--check", action="store_true", help="verify stored numbers only")
    group.add_argument("--verify", action="store_true",
                       help="run the suites and verify the stored numbers still reproduce")
    parser.add_argument("--only", default="",
                        help="comma-separated suite keys to re-measure with --run")
    args = parser.parse_args()
    only = {part.strip() for part in (args.only or "").split(",") if part.strip()} or None
    if args.run:
        return run(only)
    return verify() if args.verify else check()


if __name__ == "__main__":
    raise SystemExit(main())
