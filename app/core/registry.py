"""Central registry of every tool + category metadata."""
from __future__ import annotations

import functools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .logger import get_logger
from .privacy import scrub_text
from .spec import RunContext, RunResult, ToolError, ToolSpec

log = get_logger("tools")


def _friendly_message(ctx: RunContext, tool_id: str, exc: BaseException) -> str:
    """Bilingual 'this failed, and here is why' text for an unexpected crash."""
    kind = type(exc).__name__
    detail = scrub_text(str(exc).strip())
    detail = detail if len(detail) <= 160 else detail[:157] + "…"
    if ctx.lang == "en":
        return (f"'{tool_id}' could not finish with these inputs ({kind}).\n"
                f"{detail}\n\nTry different values; the full details are in the log.")
    return (f"ابزار «{tool_id}» با این ورودی‌ها به پایان نرسید ({kind}).\n"
            f"{detail}\n\nمقادیر را تغییر دهید؛ جزئیات کامل در فایل لاگ است.")


def _guarded(run: Callable, tool_id: str) -> Callable:
    """Never let an unexpected exception escape a tool.

    Real bugs are still logged with a traceback (and the original callable stays
    reachable through `__wrapped__`, which the test-suite uses to hunt them down),
    but the user only ever sees a sentence in their own language.
    """
    if getattr(run, "__win_genius_guard__", False):
        return run

    @functools.wraps(run)
    def wrapper(ctx: RunContext, values: Dict) -> "RunResult":
        try:
            return run(ctx, values)
        except (ToolError, SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:                       # noqa: BLE001 - last line of defence
            log.exception("tool %s crashed: %s", tool_id, exc)
            raise ToolError(_friendly_message(ctx, tool_id, exc)) from exc

    wrapper.__win_genius_guard__ = True
    return wrapper


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    name_en: str
    icon: str
    color: str
    tagline: str = ""
    tagline_en: str = ""

    def name_for(self, lang: str) -> str:
        return self.name_en if lang == "en" else self.name

    def tagline_for(self, lang: str) -> str:
        return (self.tagline_en or self.tagline) if lang == "en" else self.tagline


CATEGORIES: List[Category] = [
    Category("home", "خانه", "Home", "star", "#8EA2FF",
             "همه‌ی ابزارها", "All tools"),
    Category("pdf", "PDF", "PDF", "pdf", "#FF6B6B",
             "تبدیل، ادغام، قفل و بهینه‌سازی", "Convert, merge, protect"),
    Category("image", "تصویر", "Images", "image", "#4ECDC4",
             "تبدیل فرمت، تغییر اندازه، واترمارک", "Formats, resize, watermark"),
    Category("media", "صدا و ویدیو", "Media", "film", "#A78BFA",
             "استخراج صدا، GIF، فشرده‌سازی", "Extract audio, GIF, compress"),
    Category("files", "فایل‌ها", "Files", "folder", "#F7B731",
             "تغییر نام، زیپ، اثر انگشت، حذف امن", "Rename, zip, hash, shred"),
    Category("system", "سیستم", "System", "monitor", "#5B8CFF",
             "اطلاعات، سرویس‌ها، پاک‌سازی", "Info, services, cleanup"),
    Category("network", "شبکه", "Network", "globe", "#2ED573",
             "IP، پینگ، وای‌فای، اسکن پورت", "IP, ping, Wi-Fi, ports"),
    Category("security", "امنیت", "Security", "shield", "#FF4757",
             "رمز، رمزگذاری فایل، رمز عبور", "Passwords, encryption"),
    Category("convert", "تبدیل و متن", "Convert", "convert", "#FFA502",
             "QR، Base64، JSON، واحد، رنگ", "QR, Base64, JSON, units"),
    Category("dev", "توسعه‌دهنده", "Developer", "terminal", "#7B61FF",
             "Regex، Diff، JSON، JWT، URL، Cron", "Regex, diff, JSON, JWT, URL, cron"),
    Category("windows", "ویندوز", "Windows", "window", "#18C8F5",
             "ابزارهای داخلی ویندوز و پاور", "Built-in tools & power"),
    Category("flow", "جریان هوشمند", "Smart Flow", "sparkles", "#00D9A6",
             "ساخت، بررسی و اجرای زنجیره‌ی ابزارها", "Build, lint and run tool chains"),
]

CATEGORY_MAP: Dict[str, Category] = {c.key: c for c in CATEGORIES}

_TOOLS: Dict[str, ToolSpec] = {}
_PROTECT_EXISTING: ContextVar[bool] = ContextVar("protect_existing_tools", default=False)


@contextmanager
def protected_registration():
    """Plugins cannot replace registered tools; failed imports roll back atomically.

    This prevents accidental registry corruption, not malicious Python execution.
    Trusted Python plugins retain the user's process privileges.
    """
    snapshot = dict(_TOOLS)
    token = _PROTECT_EXISTING.set(True)
    try:
        yield
    except BaseException:
        _TOOLS.clear()
        _TOOLS.update(snapshot)
        raise
    finally:
        _PROTECT_EXISTING.reset(token)



def category(key: str) -> Category:
    return CATEGORY_MAP.get(key, CATEGORY_MAP["home"])


def register(spec: ToolSpec) -> ToolSpec:
    """Register a tool (idempotent: last definition wins)."""
    if _PROTECT_EXISTING.get() and spec.id in _TOOLS:
        raise ValueError(f"plugin cannot replace an existing tool: {spec.id}")
    if not spec.id:
        raise ValueError("tool id must not be empty")
    if spec.run is not None:
        spec.run = _guarded(spec.run, spec.id)
    _TOOLS[spec.id] = spec
    return spec


def tool(**kwargs):
    """Decorator sugar: `@tool(id=..., category=..., ...)`."""

    def deco(func):
        spec = ToolSpec(run=func, **kwargs)
        register(spec)
        return func

    return deco


def get(tool_id: str) -> Optional[ToolSpec]:
    return _TOOLS.get(tool_id)


def all_tools() -> List[ToolSpec]:
    return sorted(_TOOLS.values(), key=lambda t: (t.category, t.order, t.name))


def tools_in(category_key: str) -> List[ToolSpec]:
    if category_key == "home":
        return all_tools()
    return sorted(
        (t for t in _TOOLS.values() if t.category == category_key),
        key=lambda t: (t.order, t.name),
    )


def categories_with_counts() -> List[tuple[Category, int]]:
    counts: Dict[str, int] = {}
    for t in _TOOLS.values():
        counts[t.category] = counts.get(t.category, 0) + 1
    return [(c, counts.get(c.key, 0)) for c in CATEGORIES if c.key != "home"]


def load_all() -> list:
    """Import every tool module so decorators run; returns failures."""
    from ..tools import load_tools  # local import avoids cycles

    return load_tools()


def count() -> int:
    return len(_TOOLS)


def invoke(tool_id: str, ctx: RunContext, values: Dict, policy=None) -> RunResult:
    """Execute a registered tool through `ExecutionService` with policy enforcement."""
    from .execution import ExecutionPolicy, execution_service

    # A convenience API must fail closed: callers need to pass an explicit
    # confirmation policy before invoking destructive or elevated tools.
    active_policy = policy or ctx.policy or ExecutionPolicy.for_cli(yes=False)
    result = execution_service.execute(tool_id, values, ctx, policy=active_policy)
    return result or RunResult()


def iter_keywords(spec: ToolSpec) -> Iterable[str]:
    yield spec.id
    yield spec.name
    yield spec.name_en
    yield spec.category
    for kw in spec.keywords:
        yield kw
    for f in spec.fields:
        if f.label:
            yield f.label
