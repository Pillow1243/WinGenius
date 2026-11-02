"""Logging setup: rotating file log + in-memory ring buffer used by the UI."""
from __future__ import annotations

import logging
import logging.handlers
import threading
from collections import deque

from .paths import logs_dir

_LOCK = threading.Lock()
_BUFFER: deque[str] = deque(maxlen=2000)
_CONFIGURED = False


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - never break logging
            return
        with _LOCK:
            _BUFFER.append(msg)


def setup_logging(debug: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger("wingenius")
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S"
    )
    try:
        handler = logging.handlers.RotatingFileHandler(
            logs_dir() / "wingenius.log", maxBytes=1_500_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        root.addHandler(handler)
    except OSError:
        pass

    buf = _BufferHandler()
    buf.setFormatter(fmt)
    root.addHandler(buf)


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"wingenius.{name}")


def recent_log(max_lines: int = 400) -> str:
    with _LOCK:
        return "\n".join(list(_BUFFER)[-max_lines:])
