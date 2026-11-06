"""Single-instance guard.

Two copies of WinGenius fighting over the same profile is a real hazard: both
write `settings.ini`, both run the cron scheduler, both hold the journal database
and both would keep restoring the same trash folder.  This module makes sure only
one GUI instance owns the profile and, when a second one starts, asks the first to
come to the front instead of spawning a rival.

Implementation notes
--------------------
`QSharedMemory` is used as the mutex *and* carries a tiny liveness token.  A hard
crash can leave the shared segment behind on some platforms, so a stale segment
that nobody answers on is removed rather than blocking the app forever.

Everything degrades to "just start" when Qt is unavailable or the platform has no
shared memory — a guard must never be the reason the app refuses to open.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

KEY_PREFIX = "WinGenius.instance."


def _key_for(profile: Optional[str] = None) -> str:
    """One key per data folder, so two portable profiles can coexist."""
    from .paths import data_dir

    seed = profile or str(data_dir().resolve())
    digest = hashlib.sha1(os.path.normcase(str(seed)).encode("utf-8")).hexdigest()[:12]
    return f"{KEY_PREFIX}{digest}"


def _write_slot(buffer, payload: bytes) -> None:
    """Write `payload`, NUL-padded, into the shared-memory slot.

    Qt hands us a memoryview whose *format* depends on the platform and binding: on
    Windows it can come back as a character view, and `view[n:] = b"..."` then raises
    `ValueError: memoryview assignment: lvalue and rvalue have different structures`.
    Casting to a flat byte view first makes the write portable, and writing the whole
    slot in one assignment keeps both sides the same shape.
    """
    view = buffer if isinstance(buffer, memoryview) else memoryview(buffer)
    if view.format != "B" or view.ndim != 1:
        try:
            view = view.cast("B")
        except (TypeError, ValueError):
            view = memoryview(bytes(view)).cast("B")
    size = view.nbytes
    try:
        view[:] = payload[:size].ljust(size, b"\0")
    except (TypeError, ValueError):
        view.cast("B")[:] = payload[:size].ljust(size, b"\0")


class InstanceGuard:
    """Try to become the primary instance for this profile."""

    def __init__(self, profile: Optional[str] = None, stale_after: float = 6.0) -> None:
        self.key = _key_for(profile)
        self.stale_after = stale_after
        self._memory = None
        self._local = None
        self.primary = False

    # ------------------------------------------------------------------ public
    def acquire(self) -> bool:
        """Return True when this process is the only instance."""
        try:
            from PySide6.QtCore import QSharedMemory
        except Exception:                       # pragma: no cover - no Qt
            self.primary = True
            return True

        memory = QSharedMemory(self.key)
        if memory.attach():
            # somebody owns it — check whether that somebody is still alive
            alive = self._looks_alive(memory)
            memory.detach()
            if alive:
                self.primary = False
                return False
            # stale segment: take it over
            memory = QSharedMemory(self.key)
        if not memory.create(64):
            # creation failed for another reason (permissions, race) — do not block
            self.primary = not self._looks_alive(QSharedMemory(self.key))
            return self.primary

        payload = f"{os.getpid()} {time.time():.0f}".encode()
        memory.lock()
        try:
            _write_slot(memory.data(), payload)
        except Exception:                       # pragma: no cover - exotic bindings
            # The slot is only an optimisation (other instances read it to tell a live
            # owner from a stale segment); never let it block startup.
            pass
        finally:
            memory.unlock()
        self._memory = memory
        self.primary = True
        return True

    def announce(self, message: str = "ping") -> bool:
        """Ask the running instance to surface itself."""
        try:
            from PySide6.QtNetwork import QLocalSocket
        except Exception:                       # pragma: no cover
            return False
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(400):
            return False
        socket.write(message.encode("utf-8") + b"\n")
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return True

    def listen(self, on_ping, on_message=None) -> None:
        """Serve `on_ping` when a second launch asks the window to come forward."""
        try:
            from PySide6.QtNetwork import QLocalServer
        except Exception:                       # pragma: no cover
            return
        server = QLocalServer()
        try:
            QLocalServer.removeServer(self.key)      # leftovers from a crash
        except Exception:
            pass
        if not server.listen(self.key):
            return

        def _handle() -> None:
            while server.hasPendingConnections():
                connection = server.nextPendingConnection()
                buffer = bytearray()

                def _read(c=connection, data=buffer) -> None:
                    data.extend(bytes(c.readAll()))
                    if len(data) > 1024 * 1024:
                        c.disconnectFromServer()
                        return
                    if b"\n" not in data:
                        return
                    raw = bytes(data).split(b"\n", 1)[0]
                    c.disconnectFromServer()
                    try:
                        message = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        message = None
                    if on_message and isinstance(message, dict):
                        on_message(message)
                    else:
                        on_ping()

                connection.readyRead.connect(_read)
                if connection.bytesAvailable():
                    _read()

        server.newConnection.connect(_handle)
        self._local = server

    def release(self) -> None:
        try:
            if self._local is not None:
                self._local.close()
        except Exception:
            pass
        try:
            if self._memory is not None:
                self._memory.detach()
        except Exception:
            pass
        self._local = None
        self._memory = None
        self.primary = False

    # ----------------------------------------------------------------- helpers
    def _looks_alive(self, memory) -> bool:
        """A segment is stale only when its owner is gone."""
        try:
            if not memory.attach():
                return False
            memory.lock()
            try:
                raw = bytes(memory.data()).rstrip(b"\0").decode("utf-8", "ignore")
            finally:
                memory.unlock()
            parts = raw.split()
            if len(parts) != 2:
                return False
            pid = int(parts[0])
            float(parts[1])  # validate the stored timestamp, not the owner's age
            if pid == os.getpid():
                return False
            return _pid_alive(pid)
        except Exception:
            return False
        finally:
            try:
                memory.detach()
            except Exception:
                pass


def _consume(connection) -> None:
    try:
        connection.readAll()
        connection.disconnectFromServer()
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":                                  # pragma: no cover - windows
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # Access denied means a process may exist but belongs to another
                # security context. Never treat that as proof it is dead.
                return ctypes.windll.kernel32.GetLastError() == 5
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == 259          # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False
