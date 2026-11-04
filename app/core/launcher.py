"""Hand a file, folder or shell target to the operating system — safely.

Why this module exists
---------------------
`os.startfile()` runs ShellExecute **inside our own process**. On Windows without an
interactive desktop (a CI runner, a service session, `psexec`, some remote shells) it
does not raise — it aborts the interpreter with `Fatal Python error: Aborted`, taking
the journal, the vault and the rest of the app down with it. That is exactly what
happened on the GitHub Windows runner while the fuzz suite called `windows_applets`.

So every "open this for me" path in WinGenius goes through here, and here we always
spawn a **child** process with `shell=False` and environment-passed parameters (never
`cmd /c start`, which interprets `&`, `|`, and `%VAR%` metacharacters).

Tests and CI set `WINGENIUS_NO_LAUNCH=1`, which turns every launch into a dry run —
after target validation has passed.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

#: Set to "1" to make every launch a no-op that only reports what it *would* do.
NO_LAUNCH_ENV = "WINGENIUS_NO_LAUNCH"

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

_ALLOWED_SCHEMES = {"http", "https", "mailto", "ms-settings", "shell"}
_BLOCKED_SCHEMES = {
    "javascript", "vbscript", "data", "file", "ms-msdt", "search-ms",
    "ms-officecmd", "ms-word", "ms-excel", "ms-powerpoint", "mhtml",
}
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")


def dry_run() -> bool:
    """True when launching is disabled for this process (tests, CI, dry runs)."""
    return os.environ.get(NO_LAUNCH_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _detached_flags() -> int:
    if not IS_WINDOWS:
        return 0
    flags = 0x00000008                      # DETACHED_PROCESS
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def _validate_target(target: str | Path) -> str:
    text = str(target or "").strip()
    if not text:
        raise ValueError("empty launch target")
    if any(ch in text for ch in ("\x00", "\r", "\n")):
        raise ValueError("control characters are not permitted in launch targets")
    if isinstance(target, Path) or _DRIVE_PATH_RE.match(text):
        return text
    if text.startswith(("\\\\", "//")):
        raise ValueError("UNC network targets are blocked for safety")
    match = _SCHEME_RE.match(text)
    if match:
        scheme = match.group(1).lower()
        if scheme in _BLOCKED_SCHEMES or scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"blocked URI scheme: {scheme}")
    return text


def _spawn(command: list[str], extra_env: dict[str, str] | None = None) -> None:
    env = None
    if extra_env:
        env = dict(os.environ)
        env.update(extra_env)
    subprocess.Popen(                              # noqa: S603 - fixed argv, shell=False
        command,
        shell=False,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        creationflags=_detached_flags(),
    )


def open_target(target: str | Path) -> str:
    """Open a file, folder, URL or `shell:`/`ms-settings:` target safely."""
    text = _validate_target(target)
    if dry_run():
        return f"dry-run: {text}"
    if IS_WINDOWS:
        _spawn(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                "Start-Process -FilePath $env:WG_LAUNCH_TARGET",
            ],
            extra_env={"WG_LAUNCH_TARGET": text},
        )
        return "launched"
    if IS_MAC:
        _spawn(["open", text])
        return "launched"
    opener = shutil.which("xdg-open")
    if not opener:
        raise RuntimeError(
            "xdg-open پیدا نشد؛ برای باز کردن فایل/پوشه در لینوکس آن را نصب کنید "
            "(sudo apt install xdg-utils)"
        )
    _spawn([opener, text])
    return "launched"


def open_folder(path: str | Path) -> str:
    """Open a folder in the system file manager."""
    text = _validate_target(Path(path))
    if IS_WINDOWS and not dry_run():
        _spawn(["explorer", os.path.normpath(text)])
        return "launched"
    return open_target(Path(text))


def reveal(path: str | Path) -> str:
    """Open the file manager with the given file selected."""
    clean = _validate_target(Path(path))
    if IS_WINDOWS and not dry_run():
        norm = os.path.normpath(clean)
        if "," in norm:
            _spawn(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    "Start-Process explorer.exe -ArgumentList ('/select,\"' + $env:WG_REVEAL_TARGET + '\"')",
                ],
                extra_env={"WG_REVEAL_TARGET": norm},
            )
        else:
            _spawn(["explorer", f"/select,{norm}"])
        return "launched"
    if IS_MAC and not dry_run():
        _spawn(["open", "-R", clean])
        return "launched"
    return open_folder(Path(clean).parent)


def run_shell(command: str) -> str:
    """Run a trusted executable argv without invoking a command shell."""
    text = str(command or "").strip()
    if not text or any(ch in text for ch in ("\x00", "\r", "\n", "&", "|", ";", "`", "$")):
        raise ValueError("unsafe shell command rejected")
    argv = shlex.split(text, posix=not IS_WINDOWS)
    if not argv:
        raise ValueError("empty command")
    if dry_run():
        return f"dry-run: {argv[0]}"
    _spawn(argv)
    return "launched"
