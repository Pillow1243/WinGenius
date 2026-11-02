"""Build WinGenius.exe with PyInstaller.

    python packaging/build_exe.py              → dist\\WinGenius.exe      (one file)
    python packaging/build_exe.py --onedir     → dist\\WinGenius\\         (fast start)
    python packaging/build_exe.py --installer  → dist\\WinGenius-Setup.exe (needs Inno Setup 6)
    python packaging/build_exe.py --clean      → wipe build/ and dist/ first
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "WinGenius.spec"
ISS = ROOT / "packaging" / "installer.iss"
DIST = ROOT / "dist"
WORK = ROOT / "build"


def run(command: list[str]) -> int:
    print("→", " ".join(command))
    return subprocess.call(command)


#: Flags that belong to this script, not to PyInstaller. `--installer` made PyInstaller
#: exit with "unrecognized arguments", and `--onedir` made it exit with "makespec
#: options not valid when a .spec file is given" — the spec reads it from the
#: environment instead (WINGENIUS_ONEDIR).
OWN_FLAGS = {"--installer", "--onedir", "--onefile"}


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("! This build produces a Windows executable — run it on Windows.")
    if "--clean" in argv:
        for folder in (DIST, WORK):
            shutil.rmtree(folder, ignore_errors=True)

    os.environ["WINGENIUS_ONEDIR"] = "1" if "--onedir" in argv else "0"
    pyinstaller_args = [arg for arg in argv if arg not in OWN_FLAGS]
    if os.environ["WINGENIUS_ONEDIR"] == "1":
        print("→ one-dir build (dist\\WinGenius\\)")
    if run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC),
            *pyinstaller_args]) != 0:
        print("PyInstaller failed.")
        return 1

    exe = DIST / "WinGenius.exe"
    if exe.exists():
        print(f"✓ {exe}  ({exe.stat().st_size / (1024 * 1024):.1f} MB)")

    if "--installer" in argv:
        candidates = [
            shutil.which("iscc"),
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]
        compiler = next((c for c in candidates if c and Path(c).exists()), None)
        if not compiler:
            print("! Inno Setup 6 not found — install it from https://jrsoftware.org/isdl.php")
            return 2
        if run([compiler, str(ISS)]) != 0:
            return 1
        setups = sorted(DIST.glob("WinGenius-Setup*.exe"))
        for setup in setups:
            print(f"✓ {setup}  ({setup.stat().st_size / (1024 * 1024):.1f} MB)")
        if not setups:
            print("! Inno Setup finished but no WinGenius-Setup*.exe appeared in dist/")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
