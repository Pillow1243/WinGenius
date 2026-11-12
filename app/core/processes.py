"""Bounded subprocess execution with process-tree cleanup.

Use this for commands whose lifetime is owned by WinGenius. It never invokes a
shell and starts a new process group/session so timeout and cancellation can
reap descendants as well as the direct child. This is process supervision, not
an OS security sandbox: children retain the current user's privileges.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

IS_WINDOWS = os.name == "nt"


class ProcessCancelled(Exception):
    """Raised after the owned process tree has been stopped on request."""

    def __init__(self, args: Any, stdout: str = "", stderr: str = "") -> None:
        super().__init__(f"process cancelled: {args!r}")
        self.args_run = args
        self.stdout = stdout
        self.stderr = stderr


def _windows_job_for(proc: subprocess.Popen) -> int | None:
    """Place a child in a kill-on-close Job Object when Windows permits it."""
    if not IS_WINDOWS:
        return None
    try:  # pragma: no cover - exercised on Windows CI

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.INT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            return None
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        ok = kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ) and kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(proc._handle))  # type: ignore[attr-defined]
        if not ok:
            kernel.CloseHandle(handle)
            return None
        return int(handle)
    except Exception:
        # Job Objects are an optional hardening layer; callers retain the
        # bounded taskkill/process fallback when this Windows API is unavailable.
        return None


def _close_windows_job(handle: int | None) -> None:
    if handle is None:
        return
    try:  # pragma: no cover - exercised on Windows CI
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        # Closing an invalid/unsupported handle is best-effort during cleanup.
        pass


def _stop_tree(proc: subprocess.Popen, grace: float, job_handle: int | None = None) -> tuple[str, str]:
    """Ask the process tree to stop, then force-kill it within a bounded time."""
    if IS_WINDOWS:
        # Give cooperative console children the grace window first. A process
        # group was created specifically so CTRL_BREAK does not target WinGenius.
        try:
            proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        except OSError:
            # The root may have exited between the timeout and signal delivery.
            pass
        try:
            return proc.communicate(timeout=max(0.0, grace))
        except subprocess.TimeoutExpired:
            # Graceful shutdown did not finish; continue to Job Object/taskkill.
            pass
        # A Job Object is the primary Windows tree boundary and also works if
        # the root process already exited while descendants retained a pipe.
        if job_handle is not None:
            try:
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
                kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                if kernel.TerminateJobObject(wintypes.HANDLE(job_handle), 1):
                    try:
                        return proc.communicate(timeout=max(0.1, grace))
                    except subprocess.TimeoutExpired:
                        # Job termination did not drain pipes; continue to taskkill.
                        pass
            except Exception:
                # Job APIs can be unavailable under nested/managed job policies;
                # the explicit taskkill fallback below still targets the tree.
                pass
        # taskkill is a Windows inbox utility and /T handles descendants which
        # did not cooperate with the bounded graceful shutdown.
        try:
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = str(Path(system_root) / "System32" / "taskkill.exe")
            killer = subprocess.Popen(
                [taskkill, "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            killer.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            killer.kill()
            try:
                killer.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # The taskkill helper was itself stopped; process.kill remains below.
                pass
        except OSError:
            try:
                proc.kill()
            except OSError:
                # The owned process may already have exited during fallback.
                pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            # The process group already exited before the signal was delivered.
            pass
        except OSError:
            try:
                proc.terminate()
            except OSError:
                # The direct process likewise may have exited during fallback.
                pass

    try:
        return proc.communicate(timeout=max(0.0, grace))
    except subprocess.TimeoutExpired:
        if not IS_WINDOWS:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The group is already gone; no further kill is needed.
                pass
            except OSError:
                try:
                    proc.kill()
                except OSError:
                    # The direct child may have exited between the two attempts.
                    pass
        else:
            try:
                proc.kill()
            except OSError:
                # The direct child may have exited during the timeout cleanup.
                pass
        return proc.communicate()


def _cleanup_successful_group(process_group: int, grace: float) -> None:
    """Stop children left behind after a command's root process exits normally."""
    if IS_WINDOWS:
        return  # the kill-on-close Job Object is closed in run_process's finally

    def group_alive() -> bool:
        try:
            os.killpg(process_group, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    if not group_alive():
        return
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + min(max(0.0, grace), 0.3)
    while group_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if group_alive():
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            # The descendant group exited after the liveness check.
            pass


def run_process(
    args: Sequence[str] | str,
    *,
    timeout: float | None = 60,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
    cancel_event: threading.Event | None = None,
    terminate_grace: float = 1.0,
    **popen_options: Any,
) -> subprocess.CompletedProcess:
    """Run argv without a shell; supervise and reap its process tree.

    Raises :class:`subprocess.TimeoutExpired` on timeout and
    :class:`ProcessCancelled` when ``cancel_event`` is set. Captured output is
    included on both exceptions. ``timeout=None`` disables only the deadline;
    cancellation remains available.
    """
    if isinstance(args, str):
        raise TypeError("pass an argument sequence; shell command strings are forbidden")
    argv = [os.fspath(part) for part in args]
    if not argv or not argv[0]:
        raise ValueError("command argv must not be empty")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive or None")
    if terminate_grace < 0:
        raise ValueError("terminate_grace must not be negative")

    options = dict(popen_options)
    options.setdefault("stdin", subprocess.PIPE if input is not None else subprocess.DEVNULL)
    options.setdefault("stdout", subprocess.PIPE)
    options.setdefault("stderr", subprocess.PIPE)
    options.setdefault("text", isinstance(input, str) or input is None)
    options.setdefault("close_fds", True)
    options["shell"] = False
    if cwd is not None:
        options["cwd"] = os.fspath(cwd)
    if env is not None:
        options["env"] = dict(env)
    if IS_WINDOWS:
        options["creationflags"] = (
            int(options.get("creationflags", 0))
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        options["start_new_session"] = True

    proc = subprocess.Popen(argv, **options)
    job_handle = _windows_job_for(proc)
    deadline = None if timeout is None else time.monotonic() + timeout
    sent_input = input
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                out, err = _stop_tree(proc, terminate_grace, job_handle)
                raise ProcessCancelled(argv, out or "", err or "")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                out, err = _stop_tree(proc, terminate_grace, job_handle)
                raise subprocess.TimeoutExpired(
                    argv, float(timeout) if timeout is not None else 0.0, output=out, stderr=err
                )
            # Short polling keeps cancellation responsive without busy-spinning.
            wait_for = 0.1 if remaining is None else min(0.1, max(0.001, remaining))
            try:
                out, err = proc.communicate(input=sent_input, timeout=wait_for)
                _cleanup_successful_group(proc.pid, terminate_grace)
                return subprocess.CompletedProcess(argv, proc.returncode, out or "", err or "")
            except subprocess.TimeoutExpired:
                # communicate() accepts the input once; subsequent calls only drain.
                sent_input = None
    finally:
        # KILL_ON_JOB_CLOSE prevents descendants from surviving a normally
        # exited root process after they close their inherited output handles.
        _close_windows_job(job_handle)
