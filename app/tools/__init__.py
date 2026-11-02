"""Tool package: importing this module registers every tool.

Order matters only for display; each tool declares its own `order`.
Modules are imported defensively so one broken optional dependency can never
prevent the whole application (or the other tools) from loading.
"""
from __future__ import annotations

from typing import List, Tuple

from ..core.logger import get_logger

log = get_logger("tools")

_MODULES = (
    "pdf_tools",
    "pdf_extra",
    "image_extra",
    "media_extra",
    "files_extra",
    "system_extra",
    "network_extra",
    "security_extra",
    "dev_tools",
    "persian_tools",
    "windows_extra",
    "ocr_tools",
    "office_extra",
    "image_tools",
    "media_tools",
    "file_tools",
    "system_tools",
    "network_tools",
    "security_tools",
    "convert_tools",
    "windows_tools",
    "automation_tools",
    "text_intel",
    "dev_extra",
    "image_extra2",
    "pdf_extra2",
    "pdf_sign_tools",
    "files_extra2",
    "system_extra2",
    "undo_tools",
    "vault_tools",
    "auto_tools",
)

_loaded = False


def load_tools() -> List[Tuple[str, str]]:
    """Import all tool modules; return the list of failures (name, error)."""
    global _loaded
    failures: List[Tuple[str, str]] = []
    if _loaded:
        return failures
    import importlib

    for name in _MODULES:
        try:
            importlib.import_module(f".{name}", __name__)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("failed to import tool module %s", name)
            failures.append((name, str(exc)))
    _loaded = True
    return failures


def module_count() -> int:
    return len(_MODULES)
