# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WinGenius (one-file by default, one-dir with --onedir)."""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

block_cipher = None

hidden = []
# tools are imported dynamically by the registry — collect them all
try:
    from PyInstaller.utils.hooks import collect_submodules

    hidden += collect_submodules("app.tools")
except Exception:      # pragma: no cover - only during a build
    pass

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

ONEDIR = "--onedir" in sys.argv
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
