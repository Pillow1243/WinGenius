#!/usr/bin/env python3
"""Regression guard for the tagged Windows release build.

Two real failures used to make *every* ``v*`` build impossible:

1. ``r'^(?m)version = ...'`` — an inline global flag after ``^`` raises
   ``re.PatternError: global flags not at the start of the expression`` on
   Python 3.11+, so the "Sync version constants" step crashed immediately.
2. The step rewrote ``paths.py``/``pyproject.toml``/``installer.iss`` but not
   ``docs/stats.json``, which is ``check_docs.py``'s canonical version. The very
   next quality-gate step therefore failed three version checks.

This script runs the *exact* Python block from ``build-windows.yml`` against a
temporary tree with a fake tag, then re-runs ``check_docs.py`` on that tree.
No PyYAML, no network, no Qt: stdlib only, so the lint job can run it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows.yml"
TAG = "v5.9.9"
VERSION = "5.9.9"

# Files the sync step touches.
SYNCED = [
    "app/core/paths.py",
    "pyproject.toml",
    "docs/stats.json",
    "packaging/version_info.txt",
    "packaging/installer.iss",
]
# Everything check_docs.py reads (it resolves ROOT from its own __file__).
DOCS = [
    "scripts/check_docs.py",
    "README.md",
    "docs/README.en.md",
    "docs/showcase.html",
    "docs/install_fa.md",
    "docs/run_fa.md",
    "docs/summary_v5_fa.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "THIRD-PARTY-NOTICES.md",
    "CONTRIBUTING.md",
    "packaging/make_showcase.py",
]


def sync_code() -> str:
    """Extract the workflow's `run: |` python block and bind fake tag values."""
    body = WORKFLOW.read_text(encoding="utf-8")
    start = body.index("name: Sync version constants")
    end = body.index("name: Install runtime", start)
    lines: list[str] = []
    collecting = False
    for line in body[start:end].splitlines():
        if not collecting:
            if line.strip() == "run: |":
                collecting = True
            continue
        if not line.strip():
            lines.append("")
            continue
        if len(line) - len(line.lstrip(" ")) < 10:
            break
        lines.append(line[10:])
    if not lines:
        raise SystemExit("FAIL  could not find the version-sync python block")
    code = "\n".join(lines)
    return (code.replace("${{ steps.meta.outputs.tag }}", TAG)
                .replace("${{ steps.meta.outputs.version }}", VERSION))


def check_exe_paths() -> list[str]:
    """The onefile spec emits dist/WinGenius.exe, not dist/WinGenius/WinGenius.exe.

    A previous workflow expected the onedir layout and threw
    ``onefile exe missing`` *after* PyInstaller had already built the exe.
    """
    failures: list[str] = []
    workflow = WORKFLOW.read_text(encoding="utf-8")
    spec = (ROOT / "packaging" / "WinGenius.spec").read_text(encoding="utf-8")
    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    pairs = [
        ("workflow points at the onefile exe", "dist/WinGenius.exe" in workflow),
        ("workflow has no stale onedir exe path", "dist/WinGenius/WinGenius.exe" not in workflow),
        ("spec names the exe WinGenius", 'name="WinGenius"' in spec),
        ("installer reads the exe from ..\\dist", '..\\dist\\' in installer),
    ]
    for label, ok in pairs:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)
    return failures


def check_frozen_tools() -> list[str]:
    """A onefile exe once shipped with 0 tools.

    ``collect_submodules("app.tools")`` silently returned [] because the spec
    ran from <repo>/packaging and ``app`` was not on sys.path. The spec now
    derives the hidden imports from ``app.tools._MODULES``; keep that link.
    """
    failures: list[str] = []
    init = (ROOT / "app" / "tools" / "__init__.py").read_text(encoding="utf-8")
    block = re.search(r"_MODULES\s*=\s*\((.*?)\)", init, re.S)
    names = re.findall(r'"([A-Za-z0-9_]+)"', block.group(1)) if block else []
    spec = (ROOT / "packaging" / "WinGenius.spec").read_text(encoding="utf-8")
    pairs = [
        ("tool module list is readable", len(names) >= 30),
        ("every tool module exists",
         all((ROOT / "app" / "tools" / f"{n}.py").is_file() for n in names)),
        ("spec derives hidden imports from _MODULES", 'f"app.tools.{name}"' in spec),
        ("spec puts the repo root on sys.path", "sys.path.insert(0, str(ROOT))" in spec),
    ]
    for label, ok in pairs:
        print(f"{'PASS' if ok else 'FAIL'}  {label} ({len(names)} modules)")
        if not ok:
            failures.append(label)
    return failures


def main() -> int:
    code = sync_code()
    failures: list[str] = check_exe_paths() + check_frozen_tools()
    with tempfile.TemporaryDirectory(prefix="wg-release-") as tmp:
        root = Path(tmp)
        for rel in dict.fromkeys(SYNCED + DOCS):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, target)

        proc = subprocess.run([sys.executable, "-c", code], cwd=root,
                              capture_output=True, text=True)
        if proc.returncode:
            print(f"FAIL  version-sync block exited {proc.returncode}")
            print((proc.stdout + proc.stderr)[-1200:])
            return 1
        print(f"PASS  version-sync block runs on {TAG}")

        expectations = {
            "app/core/paths.py": f'APP_VERSION = "{VERSION}"',
            "pyproject.toml": f'version = "{VERSION}"',
            "packaging/installer.iss": f'#define AppVersion "{VERSION}"',
            "packaging/version_info.txt": f"'FileVersion', '{VERSION}.0'",
        }
        for rel, needle in expectations.items():
            ok = needle in (root / rel).read_text(encoding="utf-8")
            print(f"{'PASS' if ok else 'FAIL'}  {rel} carries {VERSION}")
            if not ok:
                failures.append(rel)
        stats = json.loads((root / "docs/stats.json").read_text(encoding="utf-8"))
        ok = stats["version"] == VERSION
        print(f"{'PASS' if ok else 'FAIL'}  stats.json version is {VERSION}")
        if not ok:
            failures.append("docs/stats.json")

        docs = subprocess.run([sys.executable, str(root / "scripts/check_docs.py")],
                              cwd=root, capture_output=True, text=True)
        ok = docs.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  check_docs.py on the tagged tree")
        if not ok:
            print((docs.stdout + docs.stderr)[-1200:])
            failures.append("check_docs.py")

    if failures:
        print(f"\n{len(failures)} release-build check(s) failed: {', '.join(failures)}")
        return 1
    print(f"\nOK  tagged release build is consistent ({TAG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
