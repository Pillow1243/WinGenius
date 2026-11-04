"""Scheduler, macro and insight tools — the "automation brain" of v4."""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..core import history as history_module
from ..core import macro as macro_module
from ..core import registry
from ..core.registry import tool
from ..core.scheduler import Schedule, describe_cron, next_run, parse_cron, store, upcoming
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_INT, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, walk_files
from ._helpers import out_dir


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


# ------------------------------------------------------------------- scheduler
@tool(
    id="schedule_list", category="system", name="زمان‌بندها", name_en="Scheduled runs",
    desc="فهرست کارهای زمان‌بندی‌شده با عبارت cron، اجرای بعدی و آخرین وضعیت.",
    desc_en="Lists scheduled jobs with their cron expression, next run and last status.",
    icon="clock", order=60, keywords=("schedule", "cron", "زمان‌بند", "کرون", "خودکار"),
    fields=[Field("include_off", K_BOOL, "نمایش خاموش‌ها", "Include disabled", default=True)],
)
def schedule_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = store.all()
    if not v.get("include_off", True):
        items = [item for item in items if item.enabled]
    if not items:
        return RunResult(message=ctx.M("هیچ زمان‌بندی‌ای ساخته نشده است.",
                                       "No schedules yet."))
    lines: List[str] = []
    for item in items:
        state = "✓" if item.enabled else "·"
        lines.append(f"{state} {item.title(ctx.lang)} — {item.cron} ({item.when(ctx.lang)})")
        lines.append(f"    {item.kind}: {item.target_id} · {ctx.M('اجراها', 'runs')}: {item.runs}"
                     f" · {ctx.M('بعدی', 'next')}: {_fmt(item.next_at())}")
    return RunResult(message=ctx.M(f"{len(items)} زمان‌بندی", f"{len(items)} schedule(s)"),
                     details="\n".join(lines))


@tool(
    id="schedule_next", category="system", name="اجراهای پیش‌رو", name_en="Upcoming runs",
    desc="نشان می‌دهد در ۲۴ ساعت آینده کدام زمان‌بندی‌ها و چه ساعتی اجرا می‌شوند.",
    desc_en="Shows which schedules fire next and exactly when.",
    icon="calendar", order=61, keywords=("agenda", "next", "پیش‌رو", "زمان‌بند"),
    fields=[Field("count", K_INT, "تعداد", "How many", default=8, minimum=1, maximum=40)],
)
def schedule_next(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pairs = []
    for item in store.all():
        if not item.enabled:
            continue
        for moment in upcoming(item.cron, 3):
            pairs.append((moment, item))
    pairs.sort(key=lambda pair: pair[0])
    pairs = pairs[: max(1, int(v.get("count") or 8))]
    if not pairs:
        return RunResult(message=ctx.M("زمان‌بندی فعالی نیست.", "No active schedules."))
    lines = [f"{_fmt(moment)} · {item.title(ctx.lang)} ({item.kind}: {item.target_id})"
             for moment, item in pairs]
    return RunResult(message=ctx.M(f"{len(pairs)} اجرای پیش‌رو", f"{len(pairs)} upcoming run(s)"),
                     details="\n".join(lines))


@tool(
    id="schedule_check", category="system", name="بررسی عبارت cron", name_en="Cron checker",
    desc="یک عبارت cron را اعتبارسنجی می‌کند، معنی فارسی‌اش را می‌گوید و ۵ اجرای بعدی را نشان می‌دهد.",
    desc_en="Validates a cron expression, explains it and lists the next five runs.",
    icon="clock", order=62, keywords=("cron", "check", "کرون", "زمان"),
    fields=[Field("cron", K_TEXT, "عبارت cron", "Cron expression", default="0 9 * * *",
                  placeholder="minute hour day month weekday")],
)
def schedule_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    expr = str(v.get("cron") or "").strip()
    try:
        parse_cron(expr)
    except Exception as exc:                                # CronError
        raise ToolError(ctx.M(f"عبارت نامعتبر: {exc}", f"invalid expression: {exc}")) from exc
    runs = upcoming(expr, 5)
    lines = [ctx.M("معنی: ", "meaning: ") + describe_cron(expr, ctx.lang), ""]
    lines += [f"{index}. {_fmt(moment)}" for index, moment in enumerate(runs, 1)]
    return RunResult(message=ctx.M("عبارت معتبر است.", "The expression is valid."),
                     details="\n".join(lines))


# ----------------------------------------------------------------------- macro
@tool(
    id="macro_run", category="system", name="اجرای ماکرو", name_en="Run a macro",
    desc="یک ماکرو JSON را اجرا می‌کند (شرط، حلقه روی فایل‌ها، فراخوانی ابزارها) — با حالت تمرینی.",
    desc_en="Runs a JSON macro (conditions, file loops, tool calls) — with a dry-run mode.",
    icon="play", order=63,
    keywords=("macro", "script", "ماکرو", "اسکریپت", "حلقه", "شرط"),
    fields=[
        Field("file", K_FILE, "فایل ماکرو", "Macro file", filt="Macro (*.json)"),
        Field("builtin", K_CHOICE, "یا قالب آماده", "Or a built-in template", default="",
              choices=[("", "—", "—"),
                       ("0", "بسته‌ی عکس‌ها", "Photo pack"),
                       ("1", "پاک‌سازی دانلود", "Tidy downloads"),
                       ("2", "PDF جست‌وجوپذیر", "Searchable PDFs")]),
        Field("folder", K_DIR, "پوشه (برای قالب‌ها)", "Folder (for templates)", default=""),
        Field("dry_run", K_BOOL, "حالت تمرینی", "Dry run", default=False),
    ],
)
def macro_run(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    macro: Dict[str, Any]
    if str(v.get("file") or "").strip():
        try:
            macro = macro_module.load_macro(Path(str(v["file"])))
        except macro_module.MacroError as exc:
            raise ToolError(str(exc)) from exc
    else:
        index = str(v.get("builtin") or "")
        templates = macro_module.builtin_macros(ctx.lang)
        if not index.isdigit() or int(index) >= len(templates):
            raise ToolError(ctx.M("یک فایل ماکرو انتخاب کنید یا یک قالب آماده.",
                                  "Pick a macro file or a built-in template."))
        macro = dict(templates[int(index)])

    variables: Dict[str, Any] = {}
    folder = str(v.get("folder") or "").strip()
    if folder:
        variables.update({"folder": folder, "src": folder, "out": str(out_dir(ctx, "macro"))})

    runner = macro_module.MacroRunner(lang=ctx.lang, output_dir=str(out_dir(ctx, "macro")),
                                      dry_run=bool(v.get("dry_run")),
                                      policy=getattr(ctx, "policy", None),
                                      on_log=lambda message: ctx.log(message),
                                      on_progress=lambda current, total: ctx.progress(current, total))
    result = runner.execute(macro, variables)
    if result.errors and not result.executed:
        raise ToolError("\n".join(result.errors[:6]))
    header = ctx.M(f"ماکرو «{macro.get('name', '')}» — {result.status}",
                   f"macro '{macro.get('name', '')}' — {result.status}")
    body = "\n".join(result.log[-40:])
    if result.errors:
        body += "\n\n" + ctx.M("خطاها:", "errors:") + "\n" + "\n".join(result.errors[:10])
    return RunResult(message=header, details=body,
                     outputs=result.outputs[:60])


@tool(
    id="macro_validate", category="system", name="بررسی ماکرو", name_en="Validate a macro",
    desc="یک فایل ماکرو را پیش از اجرا بازرسی می‌کند: نوع مرحله‌ها، ابزارهای ناشناخته و آرگومان‌های ناقص.",
    desc_en="Lints a macro before running it: step types, unknown tools, missing arguments.",
    icon="check", order=64, keywords=("macro", "lint", "validate", "ماکرو", "بررسی"),
    fields=[Field("file", K_FILE, "فایل ماکرو", "Macro file", required=True, filt="Macro (*.json)")],
)
def macro_validate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    try:
        macro = macro_module.load_macro(Path(str(v["file"])))
    except macro_module.MacroError as exc:
        raise ToolError(str(exc)) from exc
    known = [spec.id for spec in registry.all_tools()]
    problems = macro_module.validate(macro, known, ctx.lang)
    if problems:
        return RunResult(message=ctx.M(f"{len(problems)} ایراد پیدا شد.",
                                       f"{len(problems)} problem(s) found."),
                         details="\n".join(f"• {item}" for item in problems))
    steps = macro.get("steps", [])
    return RunResult(message=ctx.M("ماکرو سالم است.", "The macro looks healthy."),
                     details=ctx.M(f"{len(steps)} مرحله در سطح اول · همه‌ی ابزارها شناخته شده‌اند.",
                                   f"{len(steps)} top-level step(s) · every tool is known."))


@tool(
    id="macro_templates", category="system", name="قالب‌های ماکرو", name_en="Macro templates",
    desc="قالب‌های آماده‌ی ماکرو را در پوشه‌ی ماکروها ذخیره می‌کند تا ویرایش و اجرا کنید.",
    desc_en="Writes the built-in macro templates into your macros folder for editing.",
    icon="layers", order=65, keywords=("macro", "template", "قالب", "ماکرو"),
    fields=[Field("which", K_CHOICE, "کدام", "Which", default="all",
                  choices=[("all", "همه", "All"), ("0", "بسته‌ی عکس", "Photo pack"),
                           ("1", "پاک‌سازی دانلود", "Tidy downloads"),
                           ("2", "PDF جست‌وجوپذیر", "Searchable PDFs")])],
)
def macro_templates(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    templates = macro_module.builtin_macros(ctx.lang)
    which = str(v.get("which") or "all")
    chosen = templates if which == "all" else [templates[int(which)]] if which.isdigit() else templates
    written = []
    for template in chosen:
        path = macro_module.save_macro(template)
        written.append(path)
    return RunResult(message=ctx.M(f"{len(written)} قالب ذخیره شد.", f"{len(written)} template(s) saved."),
                     outputs=[str(path) for path in written],
                     details="\n".join(str(path) for path in written))


# -------------------------------------------------------------------- insights
@tool(
    id="usage_insights", category="system", name="گزارش کاربرد و روند", name_en="Usage insights",
    desc="از تاریخچه‌ی اجراها یک گزارش می‌سازد: پرکارترین ابزارها، نرخ موفقیت، ساعت شلوغ و مدت‌ها.",
    desc_en="Turns the run history into a report: busiest tools, success rate, busiest hour.",
    icon="chart", order=66, keywords=("insights", "stats", "report", "گزارش", "آمار", "روند"),
    fields=[
        Field("days", K_INT, "چند روز اخیر", "Last N days", default=30, minimum=1, maximum=365),
        Field("save_csv", K_BOOL, "ذخیره‌ی CSV", "Save a CSV report", default=False),
    ],
)
def usage_insights(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import csv

    days = max(1, int(v.get("days") or 30))
    cutoff = time.time() - days * 86400
    entries = [entry for entry in history_module.history.entries(limit=5000) if entry.when >= cutoff]
    if not entries:
        return RunResult(message=ctx.M("در این بازه اجرایی ثبت نشده است.",
                                       "No runs recorded in that window."))
    total = len(entries)
    done = sum(1 for entry in entries if entry.status == "done")
    failed = sum(1 for entry in entries if entry.status == "failed")
    durations = [entry.duration for entry in entries if entry.duration > 0]
    average = sum(durations) / len(durations) if durations else 0.0
    tools_top = Counter(entry.tool_id for entry in entries).most_common(10)
    hours = Counter(datetime.fromtimestamp(entry.when).hour for entry in entries)
    busiest_hour, busiest_count = (hours.most_common(1)[0] if hours else (0, 0))
    per_day: Dict[str, int] = {}
    for entry in entries:
        key = datetime.fromtimestamp(entry.when).strftime("%Y-%m-%d")
        per_day[key] = per_day.get(key, 0) + 1

    lines = [
        ctx.M(f"بازه: {days} روز · {total} اجرا", f"window: {days} day(s) · {total} runs"),
        ctx.M(f"موفق: {done} · ناموفق: {failed} · نرخ موفقیت: {done / total * 100:.1f}٪",
              f"done: {done} · failed: {failed} · success: {done / total * 100:.1f}%"),
        ctx.M(f"میانگین زمان: {average:.2f} ثانیه", f"average duration: {average:.2f}s"),
        ctx.M(f"شلوغ‌ترین ساعت: {busiest_hour:02d}:00 ({busiest_count} اجرا)",
              f"busiest hour: {busiest_hour:02d}:00 ({busiest_count} runs)"),
        "",
        ctx.M("پرکارترین ابزارها:", "top tools:"),
    ]
    lines += [f"{index}. {tool_id} — {count}" for index, (tool_id, count) in enumerate(tools_top, 1)]
    lines.append("")
    lines.append(ctx.M("روند روزانه:", "daily trend:"))
    for day in sorted(per_day)[-14:]:
        lines.append(f"{day}  {'█' * min(40, per_day[day])} {per_day[day]}")

    outputs: List[str] = []
    if v.get("save_csv"):
        target = out_dir(ctx, "reports") / time.strftime("usage-%Y%m%d-%H%M.csv")
        with target.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["when", "tool_id", "title", "status", "duration"])
            for entry in entries:
                writer.writerow([datetime.fromtimestamp(entry.when).isoformat(timespec="seconds"),
                                 entry.tool_id, entry.title, entry.status, f"{entry.duration:.3f}"])
        outputs.append(str(target))
        lines.append("")
        lines.append(ctx.M(f"CSV ذخیره شد: {target}", f"CSV saved: {target}"))

    return RunResult(
        message=ctx.M(f"{total} اجرا · موفقیت {done / total * 100:.0f}٪ · میانگین {average:.2f}s",
                      f"{total} runs · {done / total * 100:.0f}% success · {average:.2f}s avg"),
        details="\n".join(lines), outputs=outputs)


@tool(
    id="storage_report", category="system", name="گزارش فضای برنامه", name_en="App storage report",
    desc="حجم تنظیمات، تاریخچه، ژورنال، سطل بازیافت، ایندکس و لاگ‌ها را نشان می‌دهد و پیشنهاد پاک‌سازی می‌دهد.",
    desc_en="Shows how much space settings, history, journal, trash, index and logs take.",
    icon="disk", order=67, keywords=("storage", "space", "فضا", "حجم", "پاکسازی"),
    fields=[Field("purge_snapshots", K_BOOL, "پاک‌سازی عکس‌های ژورنال", "Purge old journal snapshots",
                  default=False)],
)
def storage_report(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from ..core.journal import journal, snapshots_dir, trash_dir, trash_size
    from ..core.paths import data_dir

    base = data_dir()
    rows: List[tuple] = []

    def size_of(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        total = 0
        if path.is_dir():
            for item in walk_files(path):
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total

    for label_fa, label_en, path in (
        ("تنظیمات", "settings", base / "settings.ini"),
        ("تاریخچه", "history", base / "history.json"),
        ("ژورنال", "journal", base / "journal.sqlite3"),
        ("ایندکس", "index", base / "search_index.sqlite3"),
        ("زمان‌بندی‌ها", "schedules", base / "schedules.json"),
        ("قانون‌های پوشه", "watch rules", base / "watch_rules.json"),
        ("گردش‌کارها", "workflows", base / "workflows.json"),
        ("عکس‌های ژورنال", "journal snapshots", snapshots_dir()),
        ("سطل بازیافت داخلی", "internal trash", trash_dir()),
        ("لاگ‌ها", "logs", base / "logs"),
    ):
        label = label_en if ctx.lang == "en" else label_fa
        rows.append((label, size_of(path)))

    purged = 0
    if v.get("purge_snapshots"):
        purged = journal().purge_snapshots()
    total = sum(size for _label, size in rows)
    lines = [f"{label:<24} {human_size(size):>10}" for label, size in rows]
    lines.append("-" * 36)
    lines.append(f"{'total':<24} {human_size(total):>10}")
    if purged:
        lines.append(ctx.M(f"{purged} عکس قدیمی ژورنال پاک شد.", f"{purged} old snapshot(s) removed."))
    lines.append("")
    lines.append(ctx.M(f"سطل داخلی: {human_size(trash_size())} — با ابزار «خالی کردن سطل» آزادش کنید.",
                       f"trash: {human_size(trash_size())} — empty it with the trash tool."))
    return RunResult(message=ctx.M(f"فضای داده‌ی برنامه: {human_size(total)}",
                                   f"app data footprint: {human_size(total)}"),
                     details="\n".join(lines))


@tool(
    id="backup_appdata", category="system", name="پشتیبان‌گیری از داده‌های برنامه",
    name_en="Back up app data",
    desc="تنظیمات، تاریخچه، گردش‌کارها، زمان‌بندی‌ها، ایندکس و ژورنال را در یک ZIP زمان‌دار می‌گذارد.",
    desc_en="Packs settings, history, workflows, schedules, index and journal into a timestamped ZIP.",
    icon="archive", order=68, keywords=("backup", "zip", "پشتیبان", "آرشیو"),
    fields=[Field("dest", K_DIR, "پوشه‌ی مقصد", "Destination folder", default="")],
)
def backup_appdata(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import zipfile

    from ..core.paths import data_dir

    base = data_dir()
    target_dir = Path(str(v.get("dest"))) if str(v.get("dest") or "").strip() else out_dir(ctx, "backups")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(ctx.M(f"پوشه‌ی مقصد ساخته نشد: {target_dir} ({exc.strerror or exc})",
                              f"Could not create {target_dir} ({exc.strerror or exc})"))
    if target_dir.is_dir() and not os.access(target_dir, os.W_OK):
        raise ToolError(ctx.M(f"اجازه‌ی نوشتن در این پوشه را ندارید: {target_dir}",
                              f"No permission to write into: {target_dir}"))
    archive = target_dir / time.strftime("wingenius-backup-%Y%m%d-%H%M.zip")
    wanted = ("settings.ini", "history.json", "workflows.json", "schedules.json", "watch_rules.json",
              "search_index.sqlite3", "journal.sqlite3", "macros", "flows")
    added = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in wanted:
            path = base / name
            if path.is_symlink():
                continue
            if path.is_file():
                if name in {"history.json", "workflows.json", "schedules.json", "watch_rules.json"}:
                    import json

                    from ..core.privacy import sanitize_document
                    try:
                        clean = sanitize_document(json.loads(path.read_text(encoding="utf-8")))
                    except ValueError:
                        clean = []
                    bundle.writestr(name, json.dumps(clean, ensure_ascii=False))
                elif path.suffix == ".sqlite3":
                    import tempfile

                    from ..core.upgrade import safe_copy_sqlite
                    fd, snap_name = tempfile.mkstemp(prefix="wg_bak_", suffix=".sqlite3")
                    os.close(fd)
                    snap = Path(snap_name)
                    try:
                        safe_copy_sqlite(path, snap)
                        bundle.write(snap, arcname=name)
                    finally:
                        snap.unlink(missing_ok=True)
                else:
                    bundle.write(path, arcname=name)
                added += 1
            elif path.is_dir():
                for item in walk_files(path):
                    if item.is_symlink():
                        continue
                    try:
                        bundle.write(item, arcname=str(item.relative_to(base)))
                        added += 1
                    except OSError:
                        continue
    return RunResult(message=ctx.M(f"{added} فایل در پشتیبان ذخیره شد.",
                                   f"{added} file(s) written to the backup."),
                     outputs=[str(archive)],
                     details=f"{archive}\n{human_size(archive.stat().st_size)}")


# ------------------------------------------------------------------ image dupes
@tool(
    id="image_dupes", category="image", name="تشخیص عکس‌های تکراری (ادراکی)",
    name_en="Perceptual duplicate photos",
    desc="با هش ادراکی (dHash) عکس‌های شبیه/تکراری را پیدا می‌کند — حتی با تغییر اندازه یا کیفیت.",
    desc_en="Finds near-duplicate photos with a perceptual dHash — even after resizing or recompression.",
    icon="image", order=78, keywords=("duplicate", "similar", "dhash", "تکراری", "شبیه", "عکس"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("threshold", K_INT, "آستانه‌ی تشابه (بت)", "Similarity threshold (bits)", default=6,
              minimum=0, maximum=24,
              help="هرچه کمتر، سخت‌گیرانه‌تر", help_en="lower = stricter"),
        Field("recursive", K_BOOL, "زیرپوشه‌ها", "Include subfolders", default=True),
        Field("move_to_trash", K_BOOL, "تکراری‌ها به سطل بروند", "Move duplicates to trash", default=False),
    ],
)
def image_dupes(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from PIL import Image

    from ..core.journal import journal, send_to_trash

    folder = Path(str(v.get("folder") or ""))
    if not folder.is_dir():
        raise ToolError(ctx.M("پوشه پیدا نشد.", "Folder not found."))
    threshold = max(0, min(24, int(v.get("threshold") or 6)))
    from ..core.utils import list_files

    files = [item for item in list_files(folder, recursive=bool(v.get("recursive", True)))
             if item.suffix.lower() in
             (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")]
    if len(files) < 2:
        return RunResult(message=ctx.M("حداقل دو عکس لازم است.", "At least two images are needed."))

    def dhash(path: Path, size: int = 8):
        try:
            with Image.open(path) as image:
                small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
                pixels = list(small.getdata())
        except Exception:
            return None
        bits = 0
        for row in range(size):
            for col in range(size):
                left = pixels[row * (size + 1) + col]
                right = pixels[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return bits

    hashes = []
    for index, path in enumerate(files, 1):
        ctx.progress(index, len(files), ctx.M("محاسبه‌ی هش ادراکی", "Hashing images"))
        value = dhash(path)
        if value is not None:
            hashes.append((path, value))

    groups: List[List[Path]] = []
    used = set()
    for index, (path, value) in enumerate(hashes):
        if path in used:
            continue
        cluster = [path]
        for other_path, other_value in hashes[index + 1:]:
            if other_path in used:
                continue
            if bin(value ^ other_value).count("1") <= threshold:
                cluster.append(other_path)
                used.add(other_path)
        if len(cluster) > 1:
            used.add(path)
            groups.append(sorted(cluster, key=lambda item: item.stat().st_size, reverse=True))

    if not groups:
        return RunResult(message=ctx.M("عکس تکراری پیدا نشد.", "No duplicates found."))

    trashed: List[str] = []
    if v.get("move_to_trash"):
        extras = [item for group in groups for item in group[1:]]
        pairs = send_to_trash(extras)
        if pairs:
            journal().record_trash(pairs, ctx.M("عکس‌های تکراری", "duplicate photos"),
                                   tool_id="image_dupes")
            trashed = [str(stored) for _orig, stored in pairs]

    lines: List[str] = []
    wasted = 0
    for number, group in enumerate(groups, 1):
        keep = group[0]
        lines.append(f"#{number} → {keep.name} ({human_size(keep.stat().st_size)})")
        for item in group[1:]:
            wasted += item.stat().st_size
            lines.append(f"    ~ {item.name} ({human_size(item.stat().st_size)})")
    if trashed:
        lines.append("")
        lines.append(ctx.M(f"{len(trashed)} فایل به سطل رفت (قابل بازگردانی).",
                           f"{len(trashed)} file(s) moved to trash (undoable)."))
    return RunResult(
        message=ctx.M(f"{len(groups)} گروه تکراری · {human_size(wasted)} قابل آزادسازی",
                      f"{len(groups)} duplicate group(s) · {human_size(wasted)} reclaimable"),
        details="\n".join(lines), outputs=trashed)
