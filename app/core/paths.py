"""Path / environment helpers for WinGenius.

Keeps every filesystem location in one place so the frozen (PyInstaller)
build and the source run behave identically.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "WinGenius"
APP_NAME_FA = "وین‌جنیوس"
APP_VERSION = "5.0.0"
APP_TAGLINE = "دستیار همه‌کاره ویندوز"
APP_TAGLINE_EN = "Your all-in-one Windows assistant"

IS_WINDOWS = sys.platform.startswith("win")
IS_FROZEN = bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Folder that contains the `app` package (or the exe when frozen)."""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Read-only bundled resources (assets)."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else project_root()
    return root.joinpath("app", "assets", *parts)


def is_portable() -> bool:
    """True when the app should keep its data next to the exe instead of %APPDATA%.

    Three ways to ask for it, cheapest first:
      * the ``--portable`` command-line flag,
      * ``WINGENIUS_PORTABLE=1`` in the environment,
      * an empty ``portable.flag`` file next to the exe / project root.
    """
    if "--portable" in sys.argv[1:]:
        return True
    if str(os.environ.get("WINGENIUS_PORTABLE", "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        return (project_root() / "portable.flag").exists()
    except OSError:
        return False


def data_dir() -> Path:
    """Writable data folder (settings, logs, cache) — per-user, or portable."""
    if is_portable():
        path = project_root() / "data"
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass                                  # read-only USB: fall back below
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.ini"


def default_output_dir() -> Path:
    """Sensible default for generated files: ~/Downloads/WinGenius."""
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.exists() else Path.home()
    path = base / "WinGenius"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path.home()
    return path
