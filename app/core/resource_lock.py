"""Advisory cross-process locks for cooperating WinGenius writers.

The sidecar is deliberately not deleted: removing lock files lets waiters lock
different inodes. OS locks are released when a process exits. This protects
cooperating writers only; it is not a security boundary against hostile users.
"""

from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


class LockTimeoutError(TimeoutError):
    """A resource lock could not be acquired before its deadline."""


def _try_os_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK, 13, 33, 36):
                return False
            raise
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def _os_unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def resource_lock(path: Path, *, timeout: float | None = 30.0) -> Iterator[None]:
    """Acquire a per-resource in-process and cross-process exclusive lock.

    ``timeout=None`` waits indefinitely. A finite timeout applies to both the
    local thread lock and the OS lock and raises :class:`LockTimeoutError`.
    """
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be non-negative or None")
    target = Path(path).resolve()
    lock_path = target.with_name(f".{target.name}.wingenius.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path))
    with _guard:
        local_lock = _locks.setdefault(key, threading.Lock())

    deadline = None if timeout is None else time.monotonic() + timeout
    if deadline is None:
        acquired = local_lock.acquire()
    else:
        acquired = local_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
    if not acquired:
        raise LockTimeoutError(f"timed out waiting for resource lock: {target}")

    fd = -1
    locked = False
    try:
        # O_NOFOLLOW is available on POSIX; Windows still gets an explicit
        # reparse/symlink check before opening the persistent sidecar.
        if lock_path.is_symlink():
            raise OSError("refusing symlinked resource lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(lock_path, flags, 0o600)
        if os.fstat(fd).st_size == 0:
            try:
                os.write(fd, b"0")
            except OSError:
                # Another process may have initialized it concurrently; the
                # fixed one-byte region is still valid if the file grew.
                if os.fstat(fd).st_size == 0:
                    raise
        while not _try_os_lock(fd):
            if deadline is not None and time.monotonic() >= deadline:
                raise LockTimeoutError(f"timed out waiting for resource lock: {target}")
            time.sleep(0.025)
        locked = True
        yield
    finally:
        try:
            if locked:
                _os_unlock(fd)
        finally:
            if fd >= 0:
                os.close(fd)
            local_lock.release()
