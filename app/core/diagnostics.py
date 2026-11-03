"""Self-diagnostics: everything the app needs, checked in one place."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Dict, List, Tuple

from .paths import APP_NAME, APP_VERSION, data_dir, default_output_dir, logs_dir
from .utils import deps_available, human_size

try:
    from .winapi import is_admin
except Exception:  # pragma: no cover
    def is_admin() -> bool:  # type: ignore
        return False


def _line(label: str, value: str, ok: bool | None = None) -> str:
    mark = "" if ok is None else ("✓ " if ok else "! ")
    return f"{mark}{label:<22}: {value}"


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def tesseract_path() -> str:
    return shutil.which("tesseract") or ""


def _data_version() -> str:
    from . import upgrade

    version = upgrade.current_version()
    if version == 0:
        return f"fresh (will become {upgrade.DATA_VERSION})"
    if version == upgrade.DATA_VERSION:
        return f"{version} (current)"
    return f"{version} → {upgrade.DATA_VERSION} on next launch"


def data_upgrade_report() -> Dict[str, object]:
    """Run the migration and return its report (used by --doctor and settings)."""
    from . import upgrade

    return upgrade.migrate()


def deps_report() -> List[Tuple[str, bool]]:
    return sorted(deps_available().items())


def diagnostics_text(full: bool = True) -> str:
    lines: List[str] = [
        _line("App", f"{APP_NAME} {APP_VERSION}"),
        _line("Python", f"{platform.python_version()} ({platform.machine()})"),
        _line("OS", f"{platform.system()} {platform.release()} {platform.version()}"),
        _line("Admin", "yes" if is_admin() else "no", is_admin() if full else None),
        _line("Data folder", str(data_dir())),
        _line("Data version", _data_version()),
        _line("Logs", str(logs_dir())),
        _line("Default output", str(default_output_dir())),
        _line("ffmpeg", ffmpeg_path() or "not found", bool(ffmpeg_path())),
        _line("tesseract (OCR)", tesseract_path() or "not found", bool(tesseract_path())),
        "",
        "Libraries:",
    ]
    for name, ok in deps_report():
        lines.append(f"  {'✓' if ok else '✗'}  {name}")
    try:
        from .plugins import discover
        from .registry import count, load_all

        failures = load_all()
        plugins = discover()
        lines += ["", _line("Tools registered", str(count())),
                  _line("Plugin files", str(len(plugins)))]
        for path in plugins:
            lines.append(f"  • {path.name}")
        if failures:
            lines += ["", _line("Tool modules failed", str(len(failures)))]
            for name, error in failures:
                lines.append(f"  ! {name}: {error}")
    except Exception as exc:  # pragma: no cover
        lines.append(f"registry error: {exc}")
    if full:
        try:
            import psutil

            usage = psutil.disk_usage(str(data_dir()))
            lines += ["", _line("Disk free", f"{human_size(usage.free)} of {human_size(usage.total)}")]
        except Exception:
            pass
    return "\n".join(lines)


def doctor_issues() -> List[str]:
    """Short list of things the user should fix (shown in Settings)."""
    issues: List[str] = []
    deps = dict(deps_report())
    for name in ("pypdf", "PyMuPDF", "Pillow"):
        if not deps.get(name, False):
            issues.append(f"{name} missing → some tools disabled")
    if not ffmpeg_path():
        issues.append("ffmpeg missing → audio/video tools disabled")
    if os.name == "nt" and not is_admin():
        issues.append("not running as administrator → system tools limited")
    try:
        from .registry import load_all

        for name, error in load_all():
            issues.append(f"tool module {name} failed to import: {error}")
    except Exception:                                   # pragma: no cover
        pass
    return issues
