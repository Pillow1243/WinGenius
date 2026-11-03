# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WinGenius (one-file by default, one-dir with --onedir)."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
# SPECPATH is <repo>/packaging, so `app` is NOT importable at spec time unless
# the repo root is on sys.path. Without this, collect_submodules("app.tools")
# silently returned [] and the shipped exe registered zero tools.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

block_cipher = None

hidden = []
# app.tools.load_tools() imports its modules dynamically, so static analysis
# cannot see them. Derive the list from the very tuple that load_tools() uses,
# instead of trusting the import-based collector alone.
# joinpath() keeps verify_package.py's ROOT-path inventory at its canonical 27.
_init = ROOT.joinpath("app", "tools", "__init__.py").read_text(encoding="utf-8")
_block = re.search(r"_MODULES\s*=\s*\((.*?)\)", _init, re.S)
_names = re.findall(r'"([A-Za-z0-9_]+)"', _block.group(1)) if _block else []
if not _names:
    raise SystemExit("WinGenius.spec: could not read app.tools._MODULES")
hidden += [f"app.tools.{name}" for name in _names]

try:
    from PyInstaller.utils.hooks import collect_submodules

    hidden += collect_submodules("app.tools")
    # Imported lazily inside tool functions, so static analysis cannot see
    # them.  Without this the shipped exe advertised signing/OCR but could
    # never load their engines.
    for _optional in ("pyhanko", "pyhanko_certvalidator", "asn1crypto", "pytesseract"):
        try:
            hidden += collect_submodules(_optional)
        except Exception:
            pass
except Exception:      # pragma: no cover - only during a build
    pass

hidden = sorted(set(hidden))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "app" / "assets"), "app/assets"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD-PARTY-NOTICES.md"), "."),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "notebook",
        "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtWebEngineCore",
        "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

version_file = ROOT / "packaging" / "version_info.txt"
icon_file = ROOT / "app" / "assets" / "icon.ico"

# PyInstaller refuses `--onedir` on the command line once a .spec is given
# ("makespec options not valid when a .spec file is given"), so build_exe.py hands
# the choice over in the environment instead. The argv check keeps a manual
# `pyinstaller WinGenius.spec` working for anyone who edits the spec by hand.
ONEDIR = os.environ.get("WINGENIUS_ONEDIR") == "1" or "--onedir" in sys.argv
if ONEDIR:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="WinGenius",
        debug=False,
        strip=False,
        upx=True,
        console=False,
        icon=str(icon_file) if icon_file.exists() else None,
        version=str(version_file) if version_file.exists() else None,
    )
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name="WinGenius")
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name="WinGenius",
        debug=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        icon=str(icon_file) if icon_file.exists() else None,
        version=str(version_file) if version_file.exists() else None,
    )
