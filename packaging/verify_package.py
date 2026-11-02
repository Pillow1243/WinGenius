#!/usr/bin/env python3
"""Verify the Windows build inputs without needing Windows.

PyInstaller and Inno Setup only run on Windows, so the one thing that *can* be
checked on every commit — that the spec, the installer script, the version
resource, the icon and the licence references all agree with each other — is
checked here.  A stale `AppVersion` in installer.iss or a renamed spec entry
otherwise only shows up as a broken release.

    python packaging/verify_package.py          # exit 0 = everything consistent
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"

problems: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {name}")
    else:
        problems.append(f"{name} — {detail}")
        print(f"  FAIL {name}  {detail}")


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def app_version() -> str:
    text = read(ROOT / "app" / "core" / "paths.py")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def main() -> int:
    version = app_version()
    print(f"[packaging] verifying build inputs for WinGenius {version or '?'}")

    check("app version is defined", bool(version), "APP_VERSION missing in paths.py")

    # ---------------------------------------------------------------- spec file
    spec = read(PACKAGING / "WinGenius.spec")
    check("spec exists", bool(spec), "packaging/WinGenius.spec missing")
    check("spec points at main.py", 'ROOT / "main.py"' in spec, "main.py entry missing")
    check("spec bundles the assets folder", 'app" / "assets"' in spec, "assets datas missing")
    check("spec bundles the licence", '"LICENSE"' in spec, "LICENSE not shipped next to the exe")
    check("spec keeps Qt WebEngine out", "PySide6.QtWebEngineCore" in spec, "excludes lost")
    check("spec collects the tool modules",
          'collect_submodules("app.tools")' in spec, "tools are imported dynamically")
    for referenced in re.findall(r'ROOT / "([^"]+)"', spec):
        check(f"spec path exists: {referenced}", (ROOT / referenced).exists(), referenced)

    # ------------------------------------------------------------- version file
    resource = read(PACKAGING / "version_info.txt")
    numbers = re.findall(r"filevers=\(([^)]+)\)", resource)
    if numbers:
        parts = [int(part.strip()) for part in numbers[0].split(",")]
        expected = [int(part) for part in (version.split(".") + ["0", "0", "0"])[:4]]
        check("version_info.txt matches APP_VERSION", parts[:3] == expected[:3],
              f"{parts} vs {expected}")
    else:
        check("version_info.txt is a valid VSVersionInfo", False, "filevers missing")

    # ---------------------------------------------------------------- installer
    iss = read(PACKAGING / "installer.iss")
    check("installer script exists", bool(iss), "packaging/installer.iss missing")
    match = re.search(r'#define AppVersion "([^"]+)"', iss)
    check("installer version matches the app", bool(match) and match.group(1) == version,
          f'{match.group(1) if match else "?"} vs {version}')
    check("installer ships the licence", "LicenseFile=" in iss, "no licence page")
    check("installer references the built exe", r"dist\{#AppExe}" in iss, "exe path missing")
    check("installer has a stable AppId", re.search(r"AppId=\{\{[0-9A-F-]+\}", iss) is not None,
          "AppId missing (upgrades would install side by side)")
    check("installer only lists languages Inno ships", "compiler:Default.isl" in iss,
          "custom .isl files break the build on a clean machine")

    # ------------------------------------------------------------------- assets
    icon = ROOT / "app" / "assets" / "icon.ico"
    check("windows icon exists", icon.exists() and icon.stat().st_size > 1000, "app/assets/icon.ico")
    check("LICENSE exists", (ROOT / "LICENSE").exists(), "no LICENSE file")
    check("third-party notices exist", (ROOT / "THIRD-PARTY-NOTICES.md").exists(), "no notices")
    check("requirements.txt is present", (ROOT / "requirements.txt").exists(), "missing")
    check("requirements-build.txt is present", (ROOT / "requirements-build.txt").exists(), "missing")

    build = read(PACKAGING / "build_exe.py")
    check("build script uses the spec", "WinGenius.spec" in build, "spec not referenced")
    check("build script looks for Inno Setup", "ISCC" in build, "installer step missing")

    print(f"\n{checks - len(problems)}/{checks} packaging checks passed")
    if problems:
        print("\nproblems:")
        for item in problems:
            print(f"  ✗ {item}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
