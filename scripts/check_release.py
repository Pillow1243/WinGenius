#!/usr/bin/env python3
"""Regression guard for the tagged Windows release build.

Release-build regressions this script guards against:

1. ``r'^(?m)version = ...'`` — an inline global flag after ``^`` raises
   ``re.PatternError: global flags not at the start of the expression`` on
   Python 3.11+, so the "Sync version constants" step crashed immediately.
2. The tagged sync step must update ``docs/stats.json`` along with the app,
   project and installer versions, or the quality gate rejects a release.
3. Manual builds must derive their version from ``pyproject.toml``. The old
   hard-coded ``5.0.1-dev`` metadata disagreed with the 6.0.0 executable and
   made the frozen ``--version`` smoke test fail after packaging completed.
4. Workflow actions must use their Node 24-compatible major versions rather
   than actions that emit GitHub's Node.js 20 deprecation warning.

This script runs the *exact* Python block from ``build-windows.yml`` against a
temporary tree with a fake tag, then re-runs ``check_docs.py`` on that tree.
No PyYAML, no network, no Qt: stdlib only, so the lint job can run it.
"""
from __future__ import annotations

import json
import os
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
    "README.md",
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
    "docs/summary_v6_fa.md",
    "docs/v6_flow_fa.md",
    "ARCHITECTURE.md",
    "app/core/registry.py",
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


def check_release_metadata() -> list[str]:
    """Keep branch builds, tag builds and the frozen version check in sync."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    meta_start = workflow.index("name: Resolve release metadata")
    meta_end = workflow.index("name: Sync version constants", meta_start)
    metadata = workflow[meta_start:meta_end]
    smoke_start = workflow.index("name: Smoke-test the frozen exe")
    smoke_end = workflow.index("name: Verify signing engine", smoke_start)
    smoke = workflow[smoke_start:smoke_end]

    checks = [
        ("manual builds resolve the source version", "pyproject.toml" in metadata
         and "Groups['version'].Value" in metadata),
        ("workflow_dispatch distinguishes branches from release tags",
         '$env:GITHUB_EVENT_NAME -eq "workflow_dispatch"' in metadata
         and "RELEASE_TAG_INPUT: ${{ inputs.release_tag }}" in metadata
         and '$env:GITHUB_REF -like "refs/tags/*"' in metadata),
        ("frozen version smoke test checks resolved metadata",
         'expected_version = "${{ steps.meta.outputs.version }}"' in smoke
         and 'assert f"WinGenius {expected_version}" in out' in smoke),
        ("no stale 5.0.1 fallback remains", "5.0.1" not in metadata + smoke),
    ]
    failures: list[str] = []
    for label, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures.append(label)
    return failures


def check_node24_actions() -> list[str]:
    """Fail CI if a workflow regresses to an action major that runs Node 20."""
    expected = {
        "actions/checkout": "v7",
        "actions/setup-python": "v7",
        "actions/upload-artifact": "v7",
        "actions/download-artifact": "v8",
        "actions/configure-pages": "v6",
        "actions/upload-pages-artifact": "v5",
        "actions/deploy-pages": "v5",
        "github/codeql-action/init": "v4",
        "github/codeql-action/autobuild": "v4",
        "github/codeql-action/analyze": "v4",
        "softprops/action-gh-release": "v3",
    }
    used: set[str] = set()
    failures: list[str] = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = re.match(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", line)
            if not match:
                continue
            action, separator, ref = match.group(1).partition("@")
            if action not in expected:
                continue
            used.add(action)
            ok = bool(separator) and ref == expected[action]
            label = f"{workflow.name}:{line_number} uses {action}@{expected[action]}"
            print(f"{'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                failures.append(label)
    for action in sorted(expected.keys() - used):
        failures.append(f"no workflow uses expected Node 24 action {action}@{expected[action]}")
        print(f"FAIL  {failures[-1]}")
    return failures


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
        # The onedir command writes its spec into build/, so relative data/icon
        # paths resolve against build/ and PyInstaller aborts before building.
        ("onedir resolves assets absolutely", 'Resolve-Path "app/assets"' in workflow),
        ("onedir uses absolute --add-data source", '"${assets};app/assets"' in workflow),
        ("onedir keeps the repo root importable", "--paths ." in workflow),
        # Piping Get-FileHash straight into Out-File opens SHA256SUMS.txt while
        # Get-ChildItem is still enumerating release/, so the hasher tries to
        # read its own open output file on Windows.
        ("checksums are buffered before writing", "$sums | Set-Content" in workflow),
        ("checksum output is excluded from itself",
         "Where-Object { $_.Name -ne 'SHA256SUMS.txt' }" in workflow),
        ("no direct hash-to-Out-File pipeline",
         "} | Out-File -FilePath release/SHA256SUMS.txt" not in workflow),
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


def check_python_snippets() -> list[str]:
    """Lint every inline ``shell: python`` step in the workflows.

    ``canvas.Canvas(...).drawString(...).save()`` passed YAML parsing, passed
    ``compile()`` and passed every static path check — then crashed on the
    Windows runner with ``AttributeError: 'NoneType' object has no attribute
    'save'`` because ``drawString`` returns ``None``.  Parsing the snippet with
    ``ast`` catches a call chained onto the result of another call.
    """
    import ast

    failures: list[str] = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        lines = wf.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.strip() != "shell: python":
                continue
            j = i + 1
            while j < len(lines) and lines[j].strip() != "run: |":
                if lines[j].strip().startswith("- name:") or (
                        lines[j].strip() and not lines[j].startswith(" ")):
                    break
                j += 1
            if j >= len(lines) or lines[j].strip() != "run: |":
                continue
            base = len(lines[j]) - len(lines[j].lstrip(" "))
            snippet: list[str] = []
            for k in range(j + 1, len(lines)):
                raw = lines[k]
                if not raw.strip():
                    snippet.append("")
                    continue
                if len(raw) - len(raw.lstrip(" ")) <= base:
                    break
                snippet.append(raw[base + 2:])
            code = re.sub(r"\$\{\{.*?\}\}", "'GH_EXPR'", "\n".join(snippet))
            label = f"{wf.name}:{i + 1}"
            try:
                tree = ast.parse(code, filename=label)
            except SyntaxError as exc:
                failures.append(f"{label}: python snippet does not parse ({exc.msg})")
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "save"
                        and isinstance(node.func.value, ast.Call)):
                    failures.append(
                        f"{label}: .save() is chained onto a call result "
                        "(that method returns None)")
    print(f"{'FAIL' if failures else 'PASS'}  inline python snippets are safe "
          f"({len(failures)} problem(s))")
    return failures


def check_trust_store() -> list[str]:
    """Return owner-dependent blockers for tagged-release authorization.

    Ordinary validation reports these as warnings; `--owner-release-auth`
    makes them fatal. A release remains blocked until a real public key is
    pinned and the matching private key is supplied from a protected secret.
    """
    sys.path.insert(0, str(ROOT))
    try:
        from app.core.updates import keyring_status
    except Exception as exc:
        return [f"update trust store unreadable: {exc}"]
    status = keyring_status()
    signing_key = os.environ.get("WINGENIUS_UPDATE_SIGNING_KEY", "").strip()
    key_id = os.environ.get("WINGENIUS_UPDATE_KEY_ID", "").strip()
    problems: list[str] = []
    if not signing_key:
        problems.append("WINGENIUS_UPDATE_SIGNING_KEY is not available")
    else:
        # Prove the provided private key corresponds to the public key embedded
        # in this build; a syntactically valid but unrelated secret is not enough.
        from app.core.updates import TRUSTED_RELEASE_PUBKEYS, signing_key_matches_keyring

        pairing_ok, why = signing_key_matches_keyring(signing_key)
        if not pairing_ok:
            problems.append(f"update signing key cannot be verified by any shipped build ({why})")
        if key_id and key_id not in TRUSTED_RELEASE_PUBKEYS:
            problems.append(f"update key id {key_id!r} is not pinned in the client trust store")
    if not status["ready"]:
        problems.append("release trust store is not ready (" + "; ".join(status["problems"])
                        + "); pin the real public key in app/core/updates.py")
    if not problems:
        return []
    return ["BLOCKED-NEEDS-OWNER: " + "; ".join(problems)
            + ". Keep the private key only in GitHub Secrets/HSM — never commit a key."]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner-release-auth", action="store_true",
        help="fail unless the owner-controlled update signing identity is ready; "
             "use only as a tagged-release authorization gate",
    )
    args = parser.parse_args()
    code = sync_code()
    failures: list[str] = (check_release_metadata() + check_node24_actions()
                           + check_exe_paths() + check_frozen_tools()
                           + check_python_snippets())
    trust_problems = check_trust_store()
    if args.owner_release_auth:
        failures.extend(trust_problems)
    elif trust_problems:
        # Routine CI validates code and packaging independently of the private
        # release identity. Keep the owner blocker visible without making every
        # code change red or weakening the actual tagged-release gate.
        print("WARN  owner-dependent release authorization is not ready: "
              + "; ".join(trust_problems))
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
