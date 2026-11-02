"""Small Windows helpers (ctypes based) with graceful no-ops elsewhere."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin(exe: str | None = None, params: str = "") -> bool:
    """Relaunch the current executable elevated (UAC prompt)."""
    if not IS_WINDOWS:
        return False
    try:
        exe = exe or sys.executable
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = params or ""
        else:
            params = params or f'"{Path(sys.argv[0]).resolve()}"'
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False


def empty_recycle_bin(confirm: bool = False) -> None:
    if not IS_WINDOWS:
        raise OSError("Windows only")
    flags = 0x1 | 0x2 | 0x4  # NOCONFIRMATION | NOPROGRESSUI | NOSOUND
    if not confirm:
        flags |= 0x0
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)


def set_wallpaper(image_path: str) -> None:
    if not IS_WINDOWS:
        raise OSError("Windows only")
    SPI_SETDESKWALLPAPER = 0x0014
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDWININICHANGE = 0x02
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, str(Path(image_path).resolve()), SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
    )
    if not ok:
        raise OSError("SystemParametersInfoW failed")


def open_with_shell(target: str) -> None:
    """Open applet / URI / exe through the Windows shell."""
    if not IS_WINDOWS:
        raise OSError("Windows only")
    os.startfile(target)  # type: ignore[attr-defined]


def start_hidden(cmd: str) -> subprocess.Popen:
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000
    return subprocess.Popen(cmd, shell=True, **kwargs)


def screen_size() -> tuple[int, int]:
    if not IS_WINDOWS:
        return (0, 0)
    try:
        user32 = ctypes.windll.user32
        return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
    except Exception:
        return (0, 0)


def dpi_awareness() -> None:
    """Enable crisp rendering on high-DPI Windows screens."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
