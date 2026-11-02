"""Automation toolbox: full-text index, workflow listings, folder watches."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core import indexer
from ..core.registry import tool, all_tools
from ..core.spec import (
    K_BOOL, K_DIR, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir, human_size
from ..core.watcher import store as watch_store
from ..core.workflows import store as workflow_store


# ------------------------------------------------------------------- indexing
@tool(
    id="index_build", category="files", name="ایندکس جست‌وجوی متن", name_en="Build search index",
    desc="متن همه‌ی فایل‌های یک پوشه (PDF، Word، متن، کد…) را در یک ایندکس محلی ذخیره می‌کند تا بعد بتوانید داخلشان جست‌وجو کنید.",
    desc_en="Extract the text of every document in a folder into a local index you can then search.",
    icon="search", order=60,
    keywords=("index", "search", "full text", "ایندکس", "جست‌وجو", "متن کامل", "fts"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("patterns", K_TEXT, "الگوها (خالی = همه)", "Patterns (empty = all)",
              placeholder="*.pdf, *.docx, *.md"),
        Field("recursive", K_BOOL, "شامل زیرپوشه‌ها", "Include subfolders", default=True),
    ],
)
def index_build(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = str(v.get("folder") or "").strip()
    if not folder or not Path(folder).exists():
        raise ToolError(ctx.M("پوشه را انتخاب کنید.", "Choose a folder."))
    counters = indexer.build(
        [folder], str(v.get("patterns") or ""), bool(v.get("recursive", True)),
        on_progress=lambda current, total, name: ctx.progress(current, total, name),
        cancel=lambda: ctx.cancelled)
    stats = indexer.stats()
    details = (f"پوشه: {folder}\n"
               f"فایل‌های بررسی‌شده: {counters['files']}\n"
               f"تازه ایندکس‌شده: {counters['indexed']}\n"
               f"بدون تغییر: {counters['skipped']}\n"
               f"کاراکترهای استخراج‌شده: {counters['chars']:,}\n"
               f"کل ایندکس: {stats['docs']} سند · {human_size(stats['size'])}")
    message = (f"{counters['indexed']} سند ایندکس شد (کل: {stats['docs']})."
               if counters["indexed"] else
               f"ایندکس به‌روز است ({stats['docs']} سند).")
    return RunResult(message=message, details=details)


@tool(
    id="index_search", category="files", name="جست‌وجو در ایندکس", name_en="Search the index",
    desc="داخل متن همه‌ی فایل‌های ایندکس‌شده جست‌وجو می‌کند و نتیجه را با جمله‌ی مربوطه نشان می‌دهد.",
    desc_en="Search inside the text of every indexed file and show the matching snippet.",
    icon="search", order=61,
    keywords=("search", "find in files", "جست‌وجو", "پیدا کردن", "داخل فایل‌ها"),
    fields=[
        Field("query", K_TEXT, "عبارت جست‌وجو", "Query", required=True,
              placeholder="قرارداد ۱۴۰۳ یا \"total revenue\""),
        Field("limit", K_INT, "حداکثر نتیجه", "Max results", default=20, minimum=1, maximum=200),
        Field("out", K_SAVE, "ذخیره‌ی گزارش در", "Save report to", filt="Text (*.txt)"),
    ],
)
def index_search(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    query = str(v.get("query") or "").strip()
    if not query:
        raise ToolError(ctx.M("عبارت جست‌وجو را بنویسید.", "Enter a search query."))
    if indexer.stats().get("docs", 0) == 0:
        raise ToolError(ctx.M(
            "ایندکس هنوز خالی است. اول ابزار «ایندکس جست‌وجوی متن» را روی یک پوشه اجرا کنید.",
            "The index is empty — run “Build search index” on a folder first."))
    hits = indexer.search(query, int(v.get("limit") or 20))
    text = indexer.report(query, hits, ctx.lang)
    outputs: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = ensure_dir(Path(requested).parent) / Path(requested).name
        target.write_text(text, encoding="utf-8")
        outputs.append(str(target))
    message = (f"{len(hits)} نتیجه پیدا شد." if hits else
               "چیزی پیدا نشد.")
    return RunResult(message=message, details=text, outputs=outputs,
                     open_target=str(Path(hits[0].path).parent) if hits else None)


@tool(
    id="index_stats", category="files", name="وضعیت ایندکس", name_en="Index status",
    desc="می‌گوید چند سند و چند مگابایت متن در ایندکس جست‌وجو ذخیره شده است.",
    desc_en="Shows how many documents and how much text the search index holds.",
    icon="chart", order=62, keywords=("index", "stats", "ایندکس", "وضعیت"),
)
def index_stats(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    stats = indexer.stats()
    details = (f"مسیر ایندکس: {indexer.index_path()}\n"
               f"سندها: {stats['docs']}\n"
               f"کاراکترها: {stats['chars']:,}\n"
               f"حجم فایل‌های اصلی: {human_size(stats['size'])}\n"
               f"زبان‌های شناسایی‌شده: {stats['languages']}")
    message = (f"{stats['docs']} سند در ایندکس است." if stats["docs"] else "ایندکس خالی است.")
    return RunResult(message=message, details=details)


@tool(
    id="index_clear", category="files", name="پاک کردن ایندکس", name_en="Clear the index",
    desc="همه‌ی سندهای ایندکس جست‌وجو را حذف می‌کند (فایل‌های اصلی دست‌نخورده می‌مانند).",
    desc_en="Drops every document from the search index (your real files stay untouched).",
    icon="trash", order=63, danger=True, keywords=("index", "clear", "reset", "پاک", "ایندکس"),
)
def index_clear(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    before = indexer.stats().get("docs", 0)
    indexer.clear()
    return RunResult(message=f"ایندکس پاک شد ({before} سند)." if ctx.lang != "en"
                     else f"Index cleared ({before} documents).")


# ------------------------------------------------------------------ workflows
@tool(
    id="workflows_list", category="dev", name="فهرست گردش‌کارها", name_en="List workflows",
    desc="همه‌ی گردش‌کارهای آماده و ساخته‌شده را با گام‌هایشان نشان می‌دهد (برای اسکریپت و CLI).",
    desc_en="Shows every built-in and user workflow with its steps (handy for scripting).",
    icon="layers", order=70, keywords=("workflow", "chain", "pipeline", "گردش‌کار", "زنجیره"),
)
def workflows_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = workflow_store.all()
    lines: List[str] = []
    for workflow in items:
        steps = " → ".join(step.title(ctx.lang) for step in workflow.steps)
        lines.append(f"• {workflow.name_for(ctx.lang)} [{workflow.source}] — {steps}")
    details = "\n".join(lines) or "—"
    return RunResult(message=f"{len(items)} گردش‌کار" if ctx.lang != "en" else f"{len(items)} workflows",
                     details=details)


@tool(
    id="watch_status", category="system", name="وضعیت خودکارسازی", name_en="Automation status",
    desc="قانون‌های «پوشه را زیر نظر بگیر» را با وضعیت و آخرین اجرایشان نشان می‌دهد.",
    desc_en="Lists your folder watch rules with their last run and status.",
    icon="refresh", order=70, keywords=("watch", "automation", "خودکارسازی", "پوشه", "دیده‌بان"),
)
def watch_status(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    rules = watch_store.rules()
    if not rules:
        return RunResult(message=("هیچ قانونی تعریف نشده است." if ctx.lang != "en"
                                  else "No rules defined."),
                         details=("از «خودکارسازی» (Ctrl+Shift+A) یک قانون بسازید."
                                  if ctx.lang != "en" else "Create one in Automation (Ctrl+Shift+A)."))
    lines = []
    for rule in rules:
        state = "فعال" if rule.enabled else "خاموش"
        lines.append(f"• {rule.title(ctx.lang)} — {rule.pattern} → {rule.target_id} [{state}]"
                     f" · {rule.runs} اجرا")
    return RunResult(message=f"{len(rules)} قانون", details="\n".join(lines))


@tool(
    id="tools_inventory", category="dev", name="فهرست کامل ابزارها", name_en="Tool inventory",
    desc="همه‌ی ابزارها را با شناسه و دسته در یک گزارش متنی می‌نویسد (برای مستندسازی و اسکریپت).",
    desc_en="Writes every tool with its id and category into a text report.",
    icon="report", order=71, keywords=("inventory", "tools", "list", "فهرست", "ابزارها"),
    fields=[Field("out", K_SAVE, "ذخیره در", "Save to", filt="Text (*.txt)")],
)
def tools_inventory(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = all_tools()
    lines = [f"{'id':<26} {'category':<10} name"]
    for spec in items:
        lines.append(f"{spec.id:<26} {spec.category:<10} {spec.name_for(ctx.lang)}")
    text = "\n".join(lines)
    outputs: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = ensure_dir(Path(requested).parent) / Path(requested).name
        target.write_text(text, encoding="utf-8")
        outputs.append(str(target))
    return RunResult(message=f"{len(items)} ابزار" if ctx.lang != "en" else f"{len(items)} tools",
                     details=text[:4000], outputs=outputs)
