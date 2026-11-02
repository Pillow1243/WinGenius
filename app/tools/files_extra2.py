"""File organiser and folder audits: sorting, dates, links, permissions, backups."""
from __future__ import annotations

import os
import shutil
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir, human_size, list_files, natural_key, unique_path
from ._helpers import out_dir, reject_disk_root

CATEGORY_MAP = {
    "documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx",
                  ".csv", ".ppt", ".pptx", ".epub"},
    "images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".wmv"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "installers": {".exe", ".msi", ".msix", ".appx", ".deb", ".dmg", ".apk"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yml", ".yaml", ".sql",
             ".ps1", ".bat", ".sh", ".c", ".cpp", ".h", ".cs", ".java", ".go", ".rs"},
}
JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db", "iconcache.db"}
JUNK_SUFFIXES = {".tmp", ".temp", ".bak", ".old", ".crdownload", ".part", ".partial"}


def _folder(values: Dict[str, Any], ctx: RunContext, key: str = "folder") -> Path:
    raw = str(values.get(key) or "").strip()
    if not raw:
        raise ToolError(ctx.M("پوشه را انتخاب کنید.", "Choose a folder."))
    path = Path(raw)
    if not path.is_dir():
        raise ToolError(ctx.M(f"پوشه پیدا نشد: {raw}", f"Folder not found: {raw}"))
    return reject_disk_root(path, ctx)


def _two_way_move(source: Path, dest: Path, move: bool) -> Path:
    ensure_dir(dest.parent)
    target = unique_path(dest)
    if move:
        shutil.move(str(source), str(target))
    else:
        shutil.copy2(source, target)
    return target


# ------------------------------------------------------------------- organise
@tool(
    id="organize_files", category="files", name="دسته‌بندی خودکار فایل‌ها", name_en="Auto-organise files",
    desc="فایل‌های یک پوشه را بر اساس نوع، سال یا ماه در پوشه‌های مرتب می‌چیند (با پیش‌نمایش امن).",
    desc_en="Sorts a folder's files into tidy subfolders by type, year or month — with a safe dry run.",
    icon="folder", order=50, keywords=("organize", "sort", "tidy", "دسته‌بندی", "مرتب", "پوشه"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("mode", K_CHOICE, "دسته‌بندی بر اساس", "Group by", default="type",
              choices=[("type", "نوع فایل", "File type"), ("year", "سال", "Year"),
                       ("month", "سال-ماه", "Year-month")]),
        Field("action", K_CHOICE, "کار", "Action", default="copy",
              choices=[("copy", "کپی", "Copy"), ("move", "انتقال", "Move")]),
        Field("recursive", K_BOOL, "شامل زیرپوشه‌ها", "Include subfolders", default=False),
        Field("preview", K_BOOL, "فقط پیش‌نمایش", "Dry run only", default=True),
    ],
)
def organize_files(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    mode = str(v.get("mode") or "type")
    recursive = bool(v.get("recursive"))
    preview = bool(v.get("preview", True))
    move = str(v.get("action") or "copy") == "move"
    files = [path for path in list_files(folder, recursive=recursive) if path.is_file()]
    if not files:
        raise ToolError(ctx.M("فایلی در این پوشه نیست.", "This folder has no files."))
    plan: List[Tuple[Path, Path]] = []
    for path in files:
        if mode == "type":
            suffix = path.suffix.lower()
            group = next((name for name, exts in CATEGORY_MAP.items() if suffix in exts),
                         suffix.lstrip(".") or "other")
        else:
            stamp = datetime.fromtimestamp(path.stat().st_mtime)
            group = stamp.strftime("%Y") if mode == "year" else stamp.strftime("%Y-%m")
        plan.append((path, folder / group / path.name))
    lines = [f"{path.name} → {dest.parent.name}/" for path, dest in plan[:200]]
    applied: List[Tuple[Path, Path]] = []
    if not preview:
        for index, (path, dest) in enumerate(plan, start=1):
            ctx.progress(index, len(plan), path.name)
            if path.parent == dest.parent:
                continue
            target = _two_way_move(path, dest, move)
            applied.append((path, target))
        if applied:
            from ..core.journal import journal
            if move:
                journal().record_moves(applied, ctx.M(f"دسته‌بندی {len(applied)} فایل",
                                                      f"organised {len(applied)} file(s)"),
                                       tool_id="organize_files")
            else:
                journal().record_created([str(dst) for _src, dst in applied],
                                         ctx.M(f"کپی {len(applied)} فایل", f"copied {len(applied)} file(s)"),
                                         tool_id="organize_files")
    headline = (ctx.M(f"{len(plan)} فایل {'منتقل' if move else 'کپی'} شد.",
                      f"{len(plan)} file(s) {'moved' if move else 'copied'}.")
                if not preview else
                ctx.M(f"پیش‌نمایش: {len(plan)} فایل جابه‌جا می‌شود (اجرا نشد).",
                      f"Dry run: {len(plan)} file(s) would move (nothing changed)."))
    return RunResult(message=headline, details="\n".join(lines), open_target=None if preview else str(folder))


# ---------------------------------------------------------------- rename dates
@tool(
    id="rename_by_date", category="files", name="نام‌گذاری بر اساس تاریخ", name_en="Rename by date",
    desc="به نام فایل‌ها تاریخ عکس (EXIF) یا تاریخ تغییر فایل را اضافه می‌کند و مرتبشان می‌کند.",
    desc_en="Prefixes file names with the photo (EXIF) or modified date and sorts them.",
    icon="camera", order=51, keywords=("rename", "date", "exif", "تاریخ", "تغییر نام", "عکس"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("pattern", K_TEXT, "الگوی نام", "Name pattern", default="DATE_ORIGINAL",
              placeholder="{date}_{name}"),
        Field("source", K_CHOICE, "تاریخ از", "Date from", default="auto",
              choices=[("auto", "EXIF یا تغییر فایل", "EXIF or modified"),
                       ("modified", "تاریخ تغییر", "Modified")]),
        Field("recursive", K_BOOL, "زیرپوشه‌ها", "Subfolders", default=False),
        Field("preview", K_BOOL, "فقط پیش‌نمایش", "Dry run only", default=True),
    ],
)
def rename_by_date(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    source = str(v.get("source") or "auto")
    recursive = bool(v.get("recursive"))
    preview = bool(v.get("preview", True))
    files = [path for path in list_files(folder, recursive=recursive) if path.is_file()]
    if not files:
        raise ToolError(ctx.M("فایلی پیدا نشد.", "No files found."))
    renamed: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        stamp: datetime
        taken = None
        if source == "auto" and path.suffix.lower() in (".jpg", ".jpeg", ".tif", ".tiff"):
            try:
                from PIL import Image

                with Image.open(path) as image:
                    exif = image.getexif()
                    raw = exif.get(36867) or exif.get(306)
                    if raw:
                        taken = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
            except Exception:
                taken = None
        stamp = taken or datetime.fromtimestamp(path.stat().st_mtime)
        new_name = f"{stamp.strftime('%Y-%m-%d_%H%M')}_{path.stem}{path.suffix.lower()}"
        target = path.with_name(new_name)
        if target == path or target.exists():
            continue
        renamed.append(f"{path.name} → {new_name}")
        if not preview:
            path.rename(target)
    if not renamed:
        return RunResult(message=ctx.M("نام‌ها از قبل مرتب بودند.", "Names were already tidy."), details="—")
    message = (ctx.M(f"{len(list(renamed))} فایل تغییر نام داده می‌شود (پیش‌نمایش).",
                     f"{len(renamed)} file(s) would be renamed (dry run).") if preview else
               ctx.M(f"{len(renamed)} فایل تغییر نام یافت.", f"{len(renamed)} file(s) renamed."))
    return RunResult(message=message, details="\n".join(renamed[:300]))


# ----------------------------------------------------------------- hard links
@tool(
    id="hardlink_report", category="files", name="گزارش هاردلینک و اینود", name_en="Hard-link report",
    desc="فایل‌هایی که فیزیکاً یک بلوک دیسک را به اشتراک می‌گذارند (هاردلینک) را پیدا می‌کند.",
    desc_en="Finds files that physically share the same disk blocks (hard links).",
    icon="copy", order=52, keywords=("hardlink", "inode", "duplicate", "هاردلینک", "اینود", "تکراری"),
    fields=[Field("folder", K_DIR, "پوشه", "Folder", required=True),
            Field("recursive", K_BOOL, "زیرپوشه‌ها", "Subfolders", default=True)],
)
def hardlink_report(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    groups: Dict[Tuple[int, int], List[Path]] = defaultdict(list)
    files = list_files(folder, recursive=bool(v.get("recursive", True)))
    for index, path in enumerate(files, start=1):
        if index % 50 == 0:
            ctx.progress(index, len(files), path.name)
        try:
            stat = path.stat()
        except OSError:
            continue
        groups[(stat.st_dev, stat.st_ino)].append(path)
    linked = {key: paths for key, paths in groups.items() if len(paths) > 1}
    if not linked:
        return RunResult(message=ctx.M(f"هاردلینکی پیدا نشد ({len(files)} فایل بررسی شد).",
                                       f"No hard links ({len(files)} files scanned)."), details="—")
    lines: List[str] = []
    saved_bytes = 0
    for _key, paths in list(linked.items())[:200]:
        size = paths[0].stat().st_size
        saved_bytes += size * (len(paths) - 1)
        lines.append(" · ".join(str(path) for path in paths))
    details = f"{ctx.M('فضای اشتراکی', 'Shared space')}: {human_size(saved_bytes)}\n\n" + "\n".join(lines)
    return RunResult(message=ctx.M(f"{len(linked)} گروه هاردلینک", f"{len(linked)} hard-link groups"),
                     details=details)


# --------------------------------------------------------------- permissions
@tool(
    id="permissions_report", category="files", name="گزارش دسترسی فایل‌ها", name_en="Permissions report",
    desc="دسترسی‌ها، مالک و اندازه‌ی فایل‌های یک پوشه را در یک گزارش CSV می‌نویسد.",
    desc_en="Writes a CSV report of permissions, owner and size for a folder's files.",
    icon="shield", order=53, keywords=("permissions", "acl", "owner", "دسترسی", "مالک", "مجوز"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="CSV (*.csv)"),
    ],
)
def permissions_report(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    files = [path for path in list_files(folder, recursive=False) if path.is_file()]
    rows = ["path,mode,readable,writable,size_bytes,owner,modified"]
    problems = 0
    for index, path in enumerate(files, start=1):
        if index % 40 == 0:
            ctx.progress(index, len(files), path.name)
        try:
            stat = path.stat()
            mode = oct(stat.st_mode)[-3:]
            readable = os.access(path, os.R_OK)
            writable = os.access(path, os.W_OK)
            owner = str(stat.st_uid)
        except OSError:
            continue
        if not readable or not writable:
            problems += 1
        rows.append(f'"{path.name}",{mode},{readable},{writable},{stat.st_size},'
                    f"{owner},{datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}")
    requested = str(v.get("out") or "").strip()
    outputs: List[str] = []
    if requested:
        dest = ensure_dir(Path(requested).parent) / Path(requested).name
        dest.write_text("\n".join(rows), encoding="utf-8-sig")
        outputs.append(str(dest))
    return RunResult(message=ctx.M(f"{len(files)} فایل · {problems} مورد بدون دسترسی کامل",
                                   f"{len(files)} files · {problems} with limited access"),
                     details="\n".join(rows[:40]), outputs=outputs)


# -------------------------------------------------------------------- flatten
@tool(
    id="flatten_folder", category="files", name="صاف کردن ساختار پوشه", name_en="Flatten a folder tree",
    desc="همه‌ی فایل‌های داخل زیرپوشه‌ها را به یک پوشه می‌آورد (با نام یکتا و بدون حذف چیزی).",
    desc_en="Collects every file from nested folders into one flat folder, never overwriting.",
    icon="folder", order=54, keywords=("flatten", "collect", "صاف", "جمع", "زیرپوشه"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("preview", K_BOOL, "فقط پیش‌نمایش", "Dry run only", default=True),
    ],
)
def flatten_folder(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    preview = bool(v.get("preview", True))
    files = [path for path in list_files(folder, recursive=True) if path.parent != folder]
    if not files:
        raise ToolError(ctx.M("همه‌ی فایل‌ها از قبل در یک پوشه‌اند.", "Everything is already flat."))
    moved: List[str] = []
    applied: List[Tuple[Path, Path]] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        target = folder / path.name
        if target.exists():
            target = unique_path(target)
        moved.append(f"{path.relative_to(folder)} → {target.name}")
        if not preview:
            shutil.move(str(path), str(target))
            applied.append((path, target))
    if applied:
        from ..core.journal import journal
        journal().record_moves(applied, ctx.M(f"صاف‌کردن {len(applied)} فایل",
                                              f"flattened {len(applied)} file(s)"),
                               tool_id="flatten_folder")
    message = (ctx.M(f"{len(files)} فایل جمع می‌شود (پیش‌نمایش).",
                     f"{len(files)} file(s) would be collected (dry run).") if preview else
               ctx.M(f"{len(files)} فایل صاف شد.", f"{len(files)} file(s) flattened."))
    return RunResult(message=message, details="\n".join(moved[:300]),
                     open_target=None if preview else str(folder))


# ------------------------------------------------------------------ junk scan
@tool(
    id="junk_scan", category="files", name="یافتن فایل‌های زباله", name_en="Find junk files",
    desc="Thumbs.db، .DS_Store، فایل‌های موقت و پشتیبان‌های قدیمی را پیدا می‌کند و حجم کل را می‌گوید.",
    desc_en="Finds Thumbs.db, .DS_Store, temp files and stale backups, with their total size.",
    icon="trash", order=55, keywords=("junk", "temp", "thumbs", "زباله", "موقت", "پاک‌سازی"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("days", K_INT, "قدیمی‌تر از (۰ = همه)", "Older than days (0 = all)",
              default=0, minimum=0, maximum=3650),
        Field("delete", K_BOOL, "حذف کن (پیش‌فرض: فقط گزارش)", "Delete (default: report only)",
              default=False),
    ],
)
def junk_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    days = int(v.get("days") or 0)
    delete = bool(v.get("delete"))
    cutoff = time.time() - days * 86400 if days else 0
    matches: List[Path] = []
    for path in list_files(folder, recursive=True):
        try:
            if not path.is_file():
                continue
            name = path.name.lower()
            if name in JUNK_NAMES or path.suffix.lower() in JUNK_SUFFIXES:
                if cutoff and path.stat().st_mtime > cutoff:
                    continue
                matches.append(path)
        except OSError:
            continue
    total = sum(path.stat().st_size for path in matches if path.exists())
    lines = [f"{human_size(path.stat().st_size):>10}  {path}" for path in matches[:300]
             if path.exists()]
    removed = 0
    if delete:
        for index, path in enumerate(matches, start=1):
            ctx.progress(index, len(matches), path.name)
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    headline = (ctx.M(f"{removed} فایل زباله حذف شد.", f"{removed} junk file(s) deleted.") if delete else
                ctx.M(f"{len(matches)} فایل زباله · {human_size(total)}",
                      f"{len(matches)} junk file(s) · {human_size(total)}"))
    return RunResult(message=headline, details="\n".join(lines) or "—")


# --------------------------------------------------------------- recent files
@tool(
    id="recent_changes", category="files", name="تغییرات اخیر", name_en="Recent changes",
    desc="فایل‌هایی که در چند ساعت/روز گذشته تغییر کرده‌اند را با زمان و حجم نشان می‌دهد.",
    desc_en="Lists files changed in the last hours/days with their time and size.",
    icon="clock", order=56, keywords=("recent", "changed", "today", "اخیر", "تغییر", "امروز"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("hours", K_INT, "چند ساعت گذشته", "Last N hours", default=24, minimum=1, maximum=8760),
        Field("recursive", K_BOOL, "زیرپوشه‌ها", "Subfolders", default=True),
    ],
)
def recent_changes(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    hours = int(v.get("hours") or 24)
    cutoff = time.time() - hours * 3600
    found: List[Tuple[float, Path, int]] = []
    for path in list_files(folder, recursive=bool(v.get("recursive", True))):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            found.append((stat.st_mtime, path, stat.st_size))
    found.sort(key=lambda item: -item[0])
    if not found:
        return RunResult(message=ctx.M(f"در {hours} ساعت گذشته تغییری نبود.",
                                       f"No changes in the last {hours}h."), details="—")
    lines = [f"{datetime.fromtimestamp(when):%Y-%m-%d %H:%M}  {human_size(size):>9}  {path}"
             for when, path, size in found[:400]]
    return RunResult(message=ctx.M(f"{len(found)} فایل تغییر کرده", f"{len(found)} changed file(s)"),
                     details="\n".join(lines))


# ------------------------------------------------------------- type statistics
@tool(
    id="file_type_stats", category="files", name="آمار نوع فایل‌ها", name_en="File type statistics",
    desc="پوشه را می‌شمارد: هر پسوند چند فایل و چند مگابایت را اشغال کرده است.",
    desc_en="Counts a folder by extension: how many files and how much space each type uses.",
    icon="chart", order=57, keywords=("stats", "extension", "types", "آمار", "پسوند", "حجم"),
    fields=[Field("folder", K_DIR, "پوشه", "Folder", required=True),
            Field("recursive", K_BOOL, "زیرپوشه‌ها", "Subfolders", default=True)],
)
def file_type_stats(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    counts: Dict[str, int] = defaultdict(int)
    sizes: Dict[str, int] = defaultdict(int)
    for path in list_files(folder, recursive=bool(v.get("recursive", True))):
        try:
            if not path.is_file():
                continue
            suffix = path.suffix.lower() or "(بدون پسوند)"
            counts[suffix] += 1
            sizes[suffix] += path.stat().st_size
        except OSError:
            continue
    if not counts:
        raise ToolError(ctx.M("فایلی پیدا نشد.", "No files found."))
    ordered = sorted(counts.items(), key=lambda item: -sizes[item[0]])
    total_size = sum(sizes.values())
    lines = [f"{suffix:<16} {counts[suffix]:>6} فایل  {human_size(sizes[suffix]):>10}"
             f"  {sizes[suffix] * 100 / max(1, total_size):>5.1f}%" for suffix, _ in ordered[:40]]
    return RunResult(message=ctx.M(f"{sum(counts.values())} فایل · {human_size(total_size)}",
                                   f"{sum(counts.values())} files · {human_size(total_size)}"),
                     details="\n".join(lines))


# ---------------------------------------------------------------- empty files
@tool(
    id="empty_file_finder", category="files", name="فایل‌های خالی", name_en="Empty files",
    desc="فایل‌های صفر بایتی را پیدا می‌کند (نشانه‌ی دانلود ناقص یا خطای ذخیره‌سازی).",
    desc_en="Finds zero-byte files — usually incomplete downloads or failed saves.",
    icon="trash", order=58, keywords=("empty", "zero", "0 byte", "خالی", "صفر", "ناقص"),
    fields=[Field("folder", K_DIR, "پوشه", "Folder", required=True),
            Field("delete", K_BOOL, "حذف کن", "Delete them", default=False)],
)
def empty_file_finder(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, ctx)
    empty = []
    for path in list_files(folder, recursive=True):
        try:
            if path.stat().st_size == 0:
                empty.append(path)
        except OSError:
            continue
    if not empty:
        return RunResult(message=ctx.M("هیچ فایل خالی‌ای نبود.", "No empty files."), details="—")
    removed = 0
    if bool(v.get("delete")):
        for path in empty:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    message = (ctx.M(f"{removed} فایل خالی حذف شد.", f"{removed} empty file(s) deleted.")
               if v.get("delete") else ctx.M(f"{len(empty)} فایل خالی", f"{len(empty)} empty file(s)"))
    return RunResult(message=message, details="\n".join(str(path) for path in empty[:400]))


# --------------------------------------------------------------- quick backup
@tool(
    id="quick_backup", category="files", name="پشتیبان‌گیری سریع", name_en="Quick backup",
    desc="از یک پوشه نسخه‌ی زمان‌دار می‌سازد، فقط فایل‌های تغییرکرده را کپی می‌کند و فهرست SHA-256 می‌نویسد.",
    desc_en="Creates a timestamped copy of a folder, copying only changed files, with a SHA-256 manifest.",
    icon="shield", order=59, keywords=("backup", "snapshot", "پشتیبان", "نسخه", "کپی"),
    fields=[
        Field("folder", K_DIR, "پوشه‌ی مبدأ", "Source folder", required=True),
        Field("dest", K_DIR, "پوشه‌ی مقصد", "Destination folder", required=True),
        Field("keep", K_INT, "چند نسخه نگه داشته شود", "Keep N snapshots", default=5, minimum=1, maximum=60),
    ],
)
def quick_backup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from ..core.utils import hash_file

    folder = _folder(v, ctx, "folder")
    dest_root = Path(str(v.get("dest") or "").strip())
    if not str(dest_root):
        raise ToolError(ctx.M("پوشه‌ی مقصد را انتخاب کنید.", "Choose a destination folder."))
    ensure_dir(dest_root)
    target = dest_root / f"backup_{datetime.now():%Y%m%d_%H%M%S}"
    files = list_files(folder, recursive=True)
    copied = 0
    skipped = 0
    manifest: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        relative = path.relative_to(folder)
        destination = target / relative
        try:
            ensure_dir(destination.parent)
            shutil.copy2(path, destination)
            manifest.append(f"{hash_file(path)}  {relative.as_posix()}")
            copied += 1
        except OSError:
            skipped += 1          # a locked or unreadable file never stops a backup
    try:
        ensure_dir(target)
        (target / "MANIFEST.sha256").write_text("\n".join(manifest), encoding="utf-8")
    except OSError as exc:
        raise ToolError(ctx.M(f"فایل فهرست پشتیبان نوشته نشد: {exc}",
                              f"Could not write the backup manifest: {exc}"))
    keep = max(1, int(v.get("keep") or 5))
    snapshots = sorted(dest_root.glob("backup_*"), key=lambda item: natural_key(item.name), reverse=True)
    for old in snapshots[keep:]:
        shutil.rmtree(old, ignore_errors=True)
    return RunResult(message=ctx.M(f"پشتیبان با {copied} فایل ساخته شد (بدون کپی تکراری: {skipped}).",
                                   f"Backup created with {copied} file(s)."),
                     details=f"{target}\n{keep} نسخه نگه داشته می‌شود · "
                             f"{len(snapshots[keep:])} نسخه‌ی قدیمی پاک شد.",
                     outputs=[str(target)], open_target=str(target))
