"""Undo, trash and journal tools — every change stays reversible."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core.journal import describe, journal, send_to_trash, trash_dir, trash_size
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILES, K_INT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size


@tool(
    id="undo_last", category="files", name="بازگردانی آخرین عملیات", name_en="Undo last operation",
    desc="آخرین تغییر فایل‌ها را برمی‌گرداند: تغییر نام، جابه‌جایی، کپی، نوشتن و انتقال به سطل.",
    desc_en="Reverses the most recent file operation: rename, move, copy, write or trash.",
    icon="undo", order=12, keywords=("undo", "revert", "بازگردانی", "لغو", "برگرداندن"),
    fields=[
        Field("count", K_INT, "چند عملیات", "How many operations", default=1, minimum=1, maximum=20),
    ],
)
def undo_last(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    count = max(1, min(20, int(v.get("count") or 1)))
    results = journal().undo_last(count)
    if not results:
        return RunResult(message=ctx.M("چیزی برای بازگردانی نیست.", "Nothing to undo."))
    restored = [path for item in results for path in item["restored"]]
    problems = [problem for item in results for problem in item["problems"]]
    statuses = ", ".join(f"#{item['id']}:{item['status']}" for item in results)
    lines = [f"• {item['operation'].title(ctx.lang)} → {item['status']}" for item in results]
    if problems:
        lines.append("")
        lines.append(("مشکلات:" if ctx.lang != "en" else "Problems:"))
        lines.extend(f"– {problem}" for problem in problems[:20])
    return RunResult(
        message=ctx.M(f"{len(restored)} مورد بازگردانده شد ({statuses}).",
                      f"{len(restored)} item(s) restored ({statuses})."),
        outputs=restored, details="\n".join(lines))


@tool(
    id="undo_history", category="files", name="تاریخچه‌ی عملیات (ژورنال)", name_en="Operation journal",
    desc="فهرست عملیات اخیر با وضعیت قابل‌بازگردانی و حجم سطل بازیافت داخلی.",
    desc_en="Lists recent operations, which ones are still undoable, and the internal trash size.",
    icon="history", order=13, keywords=("journal", "history", "ژورنال", "تاریخچه", "undo"),
    fields=[Field("limit", K_INT, "تعداد", "How many", default=25, minimum=5, maximum=200)],
)
def undo_history(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    limit = max(5, min(200, int(v.get("limit") or 25)))
    items = journal().entries(limit=limit)
    stats = journal().stats()
    if not items:
        return RunResult(message=ctx.M("ژورنال خالی است.", "The journal is empty."))
    lines: List[str] = []
    for op in items:
        flag = ("✓" if op.undoable else "·")
        lines.append(f"{flag} #{op.id} {describe(op, ctx.lang)}")
    lines.append("")
    lines.append(ctx.M(f"سطل بازیافت داخلی: {human_size(trash_size())} · مسیر: {trash_dir()}",
                       f"internal trash: {human_size(trash_size())} · at {trash_dir()}"))
    return RunResult(
        message=ctx.M(f"{len(items)} عملیات · {stats['pending']} قابل بازگردانی",
                      f"{len(items)} operation(s) · {stats['pending']} undoable"),
        details="\n".join(lines))


@tool(
    id="trash_move", category="files", name="حذف امن با سطل داخلی", name_en="Safe delete to trash",
    desc="فایل‌ها را پاک نمی‌کند؛ به سطل بازیافت داخلی وین‌جنیوس می‌برد تا هر وقت خواستید بازگردانید.",
    desc_en="Does not delete: moves files to the WinGenius trash so they can always be restored.",
    icon="trash", order=14, keywords=("delete", "trash", "حذف", "سطل", "بازیافت"),
    fields=[
        Field("files", K_FILES, "فایل‌ها", "Files", required=True),
        Field("record", K_BOOL, "ثبت در ژورنال", "Record in journal", default=True),
    ],
)
def trash_move(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = [Path(str(item)) for item in (v.get("files") or []) if str(item).strip()]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشده است.", "No files selected."))
    pairs = send_to_trash(files)
    if not pairs:
        return RunResult(message=ctx.M("فایلی جابه‌جا نشد.", "Nothing was moved."))
    if v.get("record", True):
        journal().record_trash(pairs, ctx.M(f"{len(pairs)} فایل به سطل رفت",
                                            f"{len(pairs)} file(s) trashed"), tool_id="trash_move")
    return RunResult(
        message=ctx.M(f"{len(pairs)} فایل به سطل بازیافت داخلی رفت (قابل بازگردانی).",
                      f"{len(pairs)} file(s) moved to the internal trash (undoable)."),
        outputs=[str(stored) for _orig, stored in pairs],
        details="\n".join(f"{orig} → {stored}" for orig, stored in pairs))


@tool(
    id="trash_restore", category="files", name="بازگردانی سطل بازیافت", name_en="Restore from trash",
    desc="همه‌ی فایل‌های سطل داخلی (یا فقط یک بسته) را به جای اصلی برمی‌گرداند.",
    desc_en="Restores everything (or one batch) from the internal trash to its original place.",
    icon="undo", order=15, keywords=("restore", "trash", "بازگردانی", "سطل"),
    fields=[Field("limit", K_INT, "حداکثر بسته", "Max batches", default=20, minimum=1, maximum=200)],
)
def trash_restore(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from ..core.journal import Journal

    items = journal().entries(limit=200)
    pending = [op for op in items if op.kind == "trash" and not op.undone]
    restored: List[str] = []
    for op in pending[: max(1, int(v.get("limit") or 20))]:
        result = journal().undo_one(op)
        restored.extend(result["restored"])
    if not restored:
        return RunResult(message=ctx.M("چیزی در سطل نیست.", "The trash is empty."))
    _ = Journal  # keep the import meaningful for type checkers
    return RunResult(
        message=ctx.M(f"{len(restored)} فایل بازگردانده شد.", f"{len(restored)} file(s) restored."),
        outputs=restored)


@tool(
    id="trash_cleanup", category="files", name="خالی کردن سطل داخلی", name_en="Empty internal trash",
    danger=True,
    desc="سطل بازیافت داخلی را برای همیشه پاک می‌کند (بعد از آن بازگردانی ممکن نیست).",
    desc_en="Permanently empties the internal trash (no undo afterwards).",
    icon="trash", order=16, keywords=("empty trash", "cleanup", "خالی", "پاکسازی"),
    fields=[Field("confirm", K_CHOICE, "تأیید", "Confirm", default="no",
                  choices=[("no", "نه، فعلاً نه", "No, keep it"), ("yes", "بله پاک کن", "Yes, empty it")])],
)
def trash_cleanup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if str(v.get("confirm") or "no") != "yes":
        return RunResult(message=ctx.M("لغو شد؛ سطل دست‌نخورده ماند.", "Cancelled — trash untouched."))
    import shutil

    size = trash_size()
    removed = 0
    for item in trash_dir().iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
            removed += 1
        except OSError:
            pass
    return RunResult(message=ctx.M(f"{removed} بسته ({human_size(size)}) پاک شد.",
                                   f"{removed} batch(es) removed ({human_size(size)})."))
