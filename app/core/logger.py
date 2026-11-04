"""Logging setup: rotating file log + in-memory ring buffer used by the UI."""
from __future__ import annotations

import logging
import logging.handlers
import threading
from collections import deque

from .paths import logs_dir
from .privacy import scrub_text

_LOCK = threading.Lock()
_BUFFER: deque[str] = deque(maxlen=2000)
_CONFIGURED = False


class _ScrubbingFormatter(logging.Formatter):
    """Redacts inline secrets and anonymizes home paths before persisting logs."""

    def __init__(self, fmt: str, datefmt: str | None = None, *, scrub_paths: bool = False) -> None:
        super().__init__(fmt, datefmt)
        self._scrub_paths = scrub_paths

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return scrub_text(rendered, scrub_paths=self._scrub_paths)


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

    fmt = _ScrubbingFormatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S",
        scrub_paths=False,
    )
    try:
        handler = logging.handlers.RotatingFileHandler(
            logs_dir() / "wingenius.log", maxBytes=1_500_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            _ScrubbingFormatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                scrub_paths=True,
            )
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
