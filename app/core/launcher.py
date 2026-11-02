"""Hand a file, folder or shell target to the operating system — safely.

Why this module exists
---------------------
`os.startfile()` runs ShellExecute **inside our own process**. On Windows without an
interactive desktop (a CI runner, a service session, `psexec`, some remote shells) it
does not raise — it aborts the interpreter with `Fatal Python error: Aborted`, taking
the journal, the vault and the rest of the app down with it. That is exactly what
happened on the GitHub Windows runner while the fuzz suite called `windows_applets`.

So every "open this for me" path in WinGenius goes through here, and here we always
spawn a **child** process: if the shell decides to die, it dies alone.

Tests and CI set `WINGENIUS_NO_LAUNCH=1`, which turns every launch into a dry run —
no Notepad popping up in the middle of a test run, and no risk of leaving stray
windows behind on a user's machine either.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Set to "1" to make every launch a no-op that only reports what it *would* do.
NO_LAUNCH_ENV = "WINGENIUS_NO_LAUNCH"

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def dry_run() -> bool:
    """True when launching is disabled for this process (tests, CI, dry runs)."""
    return os.environ.get(NO_LAUNCH_ENV, "").strip() in {"1", "true", "yes", "on"}


def _detached_flags() -> int:
    if not IS_WINDOWS:
        return 0
    flags = 0x00000008                      # DETACHED_PROCESS
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def _spawn(command: list[str]) -> None:
    subprocess.Popen(                              # noqa: S603 - fixed argv, no shell
        command,
        shell=False,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_detached_flags(),
    )


def open_target(target: str | Path) -> str:
    """Open a file, folder, URL or `shell:`/`ms-settings:` target.

    Returns a short description of what happened — `"launched"` or `"dry-run: …"` —
    so callers can put it in the result message instead of guessing.
    """
    text = str(target)
    if dry_run():
        return f"dry-run: {text}"
    if IS_WINDOWS:
        # `cmd /c start "" <target>` is ShellExecute in a child process; the empty
        # title argument keeps paths with spaces intact.
        _spawn(["cmd", "/c", "start", "", text])
        return "launched"
    if IS_MAC:
        _spawn(["open", text])
        return "launched"
    opener = shutil.which("xdg-open")
    if not opener:
        raise RuntimeError(
            "xdg-open پیدا نشد؛ برای باز کردن فایل/پوشه در لینوکس آن را نصب کنید "
            "(sudo apt install xdg-utils)")
    _spawn([opener, text])
    return "launched"


def open_folder(path: str | Path) -> str:
    """Open a folder in the system file manager."""
    if IS_WINDOWS and not dry_run():
        _spawn(["explorer", str(path)])
        return "launched"
    return open_target(path)


def reveal(path: str | Path) -> str:
    """Open the file manager with the given file selected."""
    if IS_WINDOWS and not dry_run():
        _spawn(["explorer", f"/select,{path}"])
        return "launched"
    if IS_MAC and not dry_run():
        _spawn(["open", "-R", str(path)])
        return "launched"
    return open_folder(Path(path).parent)


def run_shell(command: str) -> str:
    """Run a shell command string detached (used for `cmd`/`powershell` applets)."""
    if dry_run():
        return f"dry-run: {command}"
    subprocess.Popen(                              # noqa: S602 - intentional shell use
        command,
        shell=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        creationflags=_detached_flags(),
    )
    return "launched"
