"""Declarative tool specification.

Every capability of WinGenius is described once, as data:

    ToolSpec(id, category, names, field list, run function)

The UI renders the form automatically from `fields`, runs `run` inside a
worker thread and shows the `RunResult`.  This keeps 60+ tools consistent and
free of hand-written UI bugs.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------- field kinds
K_FILE = "file"
K_FILES = "files"
K_DIR = "dir"
K_SAVE = "save"
K_TEXT = "text"
K_PASSWORD = "password"
K_MULTILINE = "multiline"
K_INT = "int"
K_FLOAT = "float"
K_CHOICE = "choice"
K_BOOL = "bool"
K_COLOR = "color"

ALL_KINDS = {
    K_FILE, K_FILES, K_DIR, K_SAVE, K_TEXT, K_PASSWORD,
    K_MULTILINE, K_INT, K_FLOAT, K_CHOICE, K_BOOL, K_COLOR,
}


@dataclass
class Field:
    """One input of a tool form."""

    key: str
    kind: str
    label: str = ""
    label_en: str = ""
    default: Any = None
    choices: Sequence[Tuple[Any, str, str]] = ()   # (value, label_fa, label_en)
    placeholder: str = ""
    placeholder_en: str = ""
    required: bool = False
    minimum: float = 0
    maximum: float = 1_000_000
    step: float = 1
    decimals: int = 0
    filt: str = ""                                  # file dialog filter
    multiple: bool = False
    help: str = ""
    help_en: str = ""
    suffix: str = ""
    sensitive: bool = False             # never persist or export the input value
    visible_when: Optional[Tuple[str, Any]] = None  # (other_key, value)

    def __post_init__(self) -> None:
        if self.kind not in ALL_KINDS:
            raise ValueError(f"unknown field kind: {self.kind}")
        if self.kind == K_CHOICE and not self.choices:
            raise ValueError(f"choice field {self.key} needs choices")

    def label_for(self, lang: str) -> str:
        return self.label_en or self.label if lang == "en" else (self.label or self.label_en)

    def help_for(self, lang: str) -> str:
        # Fall back to the other language: a missing translation must never
        # silently remove the help text from a form.
        return (self.help_en or self.help) if lang == "en" else (self.help or self.help_en)


@dataclass
class RunResult:
    """What a tool returns when it succeeds."""

    message: str = ""
    outputs: List[str] = dc_field(default_factory=list)
    details: str = ""
    open_target: Optional[str] = None

    def __post_init__(self) -> None:
        self.outputs = [str(p) for p in (self.outputs or [])]


class ToolError(Exception):
    """Expected, human readable failure (shown as a friendly message)."""


class Cancelled(ToolError):
    """Raised by tools when the user pressed Cancel."""

    def __init__(self, message: str = "cancelled") -> None:
        super().__init__(message)


class RunContext:
    """Everything a running tool may talk to (progress, log, cancel)."""

    def __init__(
        self,
        lang: str = "fa",
        output_dir: str = "",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        on_message: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        policy: Any = None,
    ) -> None:
        self.lang = "en" if lang == "en" else "fa"
        self.output_dir = output_dir
        self._on_progress = on_progress
        self._on_message = on_message
        self._cancel = cancel_event or threading.Event()
        self.policy = policy

    # ------------------------------------------------------------------ i18n
    def M(self, fa: str, en: Optional[str] = None) -> str:
        """Pick a message for the active language."""
        if self.lang == "en":
            return en if en is not None else fa
        return fa

    # -------------------------------------------------------------- feedback
    def progress(self, current: int, total: int = 0, message: str = "") -> None:
        if self._on_progress:
            try:
                self._on_progress(int(current), int(total), message)
            except Exception:
                pass
        self.check_cancel()

    def log(self, message: str) -> None:
        if self._on_message:
            try:
                self._on_message(str(message))
            except Exception:
                pass

    # ---------------------------------------------------------------- cancel
    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled(self.M("عملیات لغو شد.", "Operation cancelled."))

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


@dataclass
class ToolSpec:
    """A single capability shown as one node of the radial menu."""

    id: str
    category: str
    name: str
    name_en: str
    desc: str = ""
    desc_en: str = ""
    icon: str = "gear"
    fields: List[Field] = dc_field(default_factory=list)
    run: Optional[Callable[[RunContext, Dict[str, Any]], RunResult]] = None
    keywords: Sequence[str] = ()
    danger: bool = False
    admin: bool = False
    windows_only: bool = False
    order: int = 100

    # ------------------------------------------------------------------ info
    def name_for(self, lang: str) -> str:
        return self.name_en if (lang == "en" and self.name_en) else self.name

    def desc_for(self, lang: str) -> str:
        return (self.desc_en or self.desc) if lang == "en" else self.desc

    def field(self, key: str) -> Optional[Field]:
        for f in self.fields:
            if f.key == key:
                return f
        return None

    def defaults(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for f in self.fields:
            if f.default is not None:
                values[f.key] = f.default
            elif f.kind == K_BOOL:
                values[f.key] = False
            elif f.kind == K_CHOICE and f.choices:
                values[f.key] = f.choices[0][0]
            elif f.kind in (K_INT, K_FLOAT):
                values[f.key] = f.minimum
            else:
                values[f.key] = ""
        return values
