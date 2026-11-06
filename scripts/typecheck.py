#!/usr/bin/env python3
"""Type-check the app against a recorded mypy baseline (audit F-13).

`[tool.mypy]` existed but nothing ever ran it, and `check_untyped_defs = false`
means the config alone proves nothing. Turning strict mode on today would report
hundreds of legacy errors and get switched off within a week, so instead:

  * every file's current error count is recorded in `docs/mypy_baseline.json`,
  * the gate fails when a file gains errors, when a *new* file has errors, or
    when the baseline entry is stale (fewer errors than reality),
  * `--update` rewrites the baseline after a deliberate pass.

That makes "no new type errors" an enforced rule while the legacy backlog can be
burned down file by file.

    python scripts/typecheck.py                  # check against the baseline
    python scripts/typecheck.py --update         # re-record the baseline
    python scripts/typecheck.py --strict-baseline  # also fail on stale entries
    python scripts/typecheck.py --paths app/core # scope a check

Only *new* errors fail by default. mypy reports fewer errors when an optional
dependency is missing (its imports degrade to `Any`), so "the baseline records
more than reality" is informational unless `--strict-baseline` asks for a tidy
pass — otherwise the gate would depend on what happens to be installed.
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
BASELINE = ROOT / "docs" / "mypy_baseline.json"
TARGETS = ["app"]
LINE = re.compile(r"^(?P<file>[^:\n]+\.py):(?P<line>\d+): (?P<severity>error|note|warning): ")
SUMMARY = re.compile(r"Found (?P<count>\d+) errors? in (?P<files>\d+) files?")


def run_mypy(paths: list[str]) -> tuple[dict[str, int], str, int]:
    env = dict(os.environ)
    env.setdefault("MYPY_CACHE_DIR", str(ROOT / ".mypy_cache"))
    proc = subprocess.run([sys.executable, "-m", "mypy", *paths], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=1800)
    output = (proc.stdout or "") + (proc.stderr or "")
    counts: dict[str, int] = {}
    for line in output.splitlines():
        match = LINE.match(line)
        if not match or match.group("severity") != "error":
            continue
        # normalise to a repo-relative POSIX path so the baseline is portable
        path = Path(match.group("file"))
        try:
            path = path.resolve().relative_to(ROOT)
        except ValueError:
            pass
        key = str(path).replace("\\", "/")
        counts[key] = counts.get(key, 0) + 1
    return counts, output, proc.returncode


def load() -> dict:
    if not BASELINE.is_file():
        return {"total": 0, "files": {}}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def check(counts: dict[str, int], baseline: dict, strict: bool) -> list[str]:
    """Return the problems that must fail the gate.

    Only growth is a regression. A file with *fewer* errors than recorded (or no
    errors at all) is reported only when `strict` is set: mypy legitimately finds
    less to complain about when an optional dependency is not installed.
    """
    stored: dict[str, int] = dict(baseline.get("files") or {})
    problems: list[str] = []
    for path, count in sorted(counts.items()):
        allowed = int(stored.get(path, 0))
        if count > allowed:
            problems.append(f"{path}: {count} type errors, baseline allows {allowed}")
        elif strict and count < allowed:
            problems.append(f"{path}: {count} type errors, baseline records {allowed} "
                            "— lower it with --update")
    if strict:
        for path, allowed in sorted(stored.items()):
            if allowed and path not in counts:
                problems.append(f"{path}: baseline records {allowed} type errors but the "
                                "file is clean now — run --update")
    return problems


def improvements(counts: dict[str, int], baseline: dict) -> list[str]:
    """Files that are better than the baseline records (never a failure)."""
    stored: dict[str, int] = dict(baseline.get("files") or {})
    notes: list[str] = []
    for path, allowed in sorted(stored.items()):
        count = int(counts.get(path, 0))
        if count < allowed:
            notes.append(f"{path}: {count} (baseline {allowed})")
    return notes


def update(counts: dict[str, int], paths: list[str]) -> None:
    payload = {
        "targets": paths,
        "total": sum(counts.values()),
        "files": dict(sorted(counts.items())),
        "notes": ("Audit F-13: per-file mypy error counts. The gate fails when a file "
                  "gains errors or a new file starts failing. Regenerate with: "
                  "python scripts/typecheck.py --update"),
    }
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="re-record the baseline")
    parser.add_argument("--paths", nargs="+", default=TARGETS)
    parser.add_argument("--strict-baseline", action="store_true",
                        help="also fail when the baseline is out of date (needs the full "
                             "runtime dependencies installed)")
    args = parser.parse_args(argv)

    counts, output, returncode = run_mypy(args.paths)
    if returncode not in (0, 1):                 # 2 = mypy itself failed
        print(output[-2000:])
        print("typecheck: mypy could not run (install it with `pip install mypy`)")
        return 2
    total = sum(counts.values())
    if args.update:
        update(counts, args.paths)
        print(f"baseline updated: {total} type errors in {len(counts)} files -> {BASELINE}")
        return 0
    baseline = load()
    problems = check(counts, baseline, strict=args.strict_baseline)
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
            print(f"::error title=type regression::{problem}")
        print(f"\n{total} type errors in {len(counts)} files "
              "(no new ones are allowed; `--update` only after fixing or accepting)")
        return 1
    print(f"OK  {total} type errors in {len(counts)} files, none new against the baseline")
    better = improvements(counts, baseline)
    if better:
        print(f"     {len(better)} file(s) are cleaner than the baseline records "
              f"(run --update to tidy): {', '.join(better[:3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
