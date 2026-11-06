"""Advisory cross-process locks for cooperating WinGenius writers.

The sidecar is deliberately not deleted: removing lock files allows waiters to
lock different inodes. The OS releases a held lock if its process exits.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@contextmanager
def resource_lock(path: Path):
    target = Path(path).resolve()
    lock_path = target.with_name(f".{target.name}.wingenius.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        local_lock = _locks.setdefault(os.path.normcase(str(lock_path)), threading.Lock())
    with local_lock:
        if lock_path.is_symlink():
            raise OSError("refusing symlinked resource lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
