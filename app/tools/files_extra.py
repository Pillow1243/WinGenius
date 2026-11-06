"""File intelligence: archive inspection, folder sync, timestamps, trees, verify."""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILES, K_FILE, K_INT, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, walk_files
from ._helpers import ensure_dir, files_or_raise, first_file, out_dir

ALL_FILTER = "همه فایل‌ها (*)"
ZIP_FILTER = "آرشیو (*.zip *.7z *.tar *.gz *.tgz *.bz2 *.xz);;همه فایل‌ها (*)"


def _sha256(path: Path, ctx: RunContext) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            ctx.check_cancel()
    return digest.hexdigest()


# ------------------------------------------------------------------- archives
@tool(
    id="archive_list", category="files", name="فهرست محتوای آرشیو", name_en="List archive",
    desc="بدون باز کردن، محتوای آرشیو را با حجم و درصد فشرده‌سازی هر فایل نشان می‌دهد.",
    desc_en="Show what is inside an archive, with each entry's size and compression ratio.",
    icon="list", order=103,
    keywords=("list zip", "archive content", "محتوای زیپ", "فهرست آرشیو"),
    fields=[
        Field("src", K_FILE, "فایل آرشیو", "Archive file", required=True, filt=ZIP_FILTER),
    ],
)
def archive_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    rows: List[str] = []
    total_raw = 0
    suffix = src.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(src) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                total_raw += info.file_size
                ratio = 0.0 if info.file_size == 0 else 100.0 * (1 - info.compress_size / info.file_size)
                rows.append(f"{human_size(info.file_size):>10}  -{ratio:4.0f}%  {info.filename}")
    elif suffix in (".tar", ".gz", ".tgz", ".bz2", ".xz") or ".tar." in src.name.lower():
        with tarfile.open(src) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                total_raw += member.size
                rows.append(f"{human_size(member.size):>10}         {member.name}")
    else:
        raise ToolError(ctx.M("فقط ZIP و TAR پشتیبانی می‌شوند (برای 7z از «مدیریت ویندوز» استفاده کنید).",
                              "Only ZIP and TAR are supported here (use Windows/7-Zip for 7z)."))
    if not rows:
        return RunResult(message=ctx.M("آرشیو خالی است.", "The archive is empty."))
    return RunResult(
        message=ctx.M(
            f"{len(rows)} فایل — مجموع اصلی {human_size(total_raw)}، حجم آرشیو {human_size(src.stat().st_size)}",
            f"{len(rows)} file(s) — {human_size(total_raw)} raw, {human_size(src.stat().st_size)} archived"),
        details="\n".join(rows[:400]) + ("\n…" if len(rows) > 400 else ""),
    )


# ---------------------------------------------------------------------- folder
@tool(
    id="empty_folders", category="files", name="پاک کردن پوشه‌های خالی",
    name_en="Remove empty folders",
    desc="پوشه‌های خالی (بدون فایل) را پیدا و در صورت تأیید حذف می‌کند.",
    desc_en="Find folders that contain no files and delete them when you confirm.",
    icon="folder", order=104,
    keywords=("empty folders", "cleanup folders", "پوشه خالی", "پاکسازی"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("delete", K_BOOL, "حذف کن", "Delete them", default=False),
    ],
)
def empty_folders(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    root = Path(str(v.get("folder") or ""))
    if not root.is_dir():
        raise ToolError(ctx.M("پوشه پیدا نشد.", "Folder not found."))
    empty: List[Path] = []
    for base, _dirs, _files in os.walk(root, topdown=False):
        base_path = Path(base)
        if base_path == root:
            continue
        try:
            if not any(base_path.iterdir()):
                empty.append(base_path)
        except OSError:
            continue
    if not empty:
        return RunResult(message=ctx.M("هیچ پوشه‌ی خالی نبود.", "No empty folders found."))
    lines = sorted(str(p) for p in empty)
    message = ctx.M(f"{len(empty)} پوشه‌ی خالی پیدا شد.", f"{len(empty)} empty folder(s) found.")
    if v.get("delete"):
        removed = 0
        for index, path in enumerate(empty, start=1):
            ctx.progress(index, len(empty), path.name)
            try:
                path.rmdir()
                removed += 1
            except OSError:
                pass
        message = ctx.M(f"{removed} پوشه‌ی خالی حذف شد.", f"{removed} empty folder(s) deleted.")
    return RunResult(message=message, details="\n".join(lines[:400]))


@tool(
    id="folder_sync", category="files", name="همگام‌سازی دو پوشه", name_en="Sync two folders",
    desc="پوشه‌ی مقصد را مثل مبدأ می‌کند: فایل‌های تازه/تغییریافته را کپی و اضافی‌ها را پاک می‌کند.",
    desc_en="Make the target folder match the source: copy new/changed files, delete extras.",
    icon="sync", order=105,
    keywords=("sync folders", "backup folder", "همگام سازی", "پشتیبان گیری پوشه"),
    fields=[
        Field("source", K_DIR, "پوشه‌ی مبدأ", "Source folder", required=True),
        Field("target", K_DIR, "پوشه‌ی مقصد", "Target folder", required=True),
        Field("mode", K_CHOICE, "حالت", "Mode", default="mirror",
              choices=[("mirror", "آینه‌ی کامل (حذف اضافی‌ها)", "Full mirror (delete extras)"),
                       ("update", "فقط افزودن/به‌روزرسانی", "Add / update only")]),
        Field("dry_run", K_BOOL, "فقط گزارش بده (بدون تغییر)", "Dry run (report only)", default=True),
    ],
)
def folder_sync(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    source = Path(str(v.get("source") or ""))
    target = Path(str(v.get("target") or ""))
    if not source.is_dir():
        raise ToolError(ctx.M("پوشه‌ی مبدأ پیدا نشد.", "Source folder not found."))
    dry = bool(v.get("dry_run", True))
    if not target.is_dir() and not dry:
        ensure_dir(target)
    if source.resolve() == target.resolve():
        raise ToolError(ctx.M("مبدأ و مقصد یکی هستند.", "Source and target are the same folder."))
    mirror = str(v.get("mode") or "mirror") == "mirror"
    src_files = walk_files(source)
    copied = updated = deleted = 0
    lines: List[str] = []
    seen: set = set()
    for index, path in enumerate(src_files, start=1):
        rel = path.relative_to(source)
        seen.add(str(rel))
        ctx.progress(index, len(src_files), ctx.M(f"بررسی {rel}", f"Checking {rel}"))
        destination = target / rel
        if not destination.exists():
            lines.append(f"+ {rel}")
            copied += 1
            if not dry:
                ensure_dir(destination.parent)
                shutil.copy2(path, destination)
        elif destination.stat().st_size != path.stat().st_size or \
                destination.stat().st_mtime < path.stat().st_mtime:
            if _sha256(path, ctx) != _sha256(destination, ctx):
                lines.append(f"~ {rel}")
                updated += 1
                if not dry:
                    shutil.copy2(path, destination)
    if mirror:
        for path in walk_files(target):
            rel = str(path.relative_to(target))
            if rel not in seen:
                lines.append(f"- {rel}")
                deleted += 1
                if not dry:
                    try:
                        path.unlink()
                    except OSError:
                        pass
    prefix = ctx.M("(آزمایشی) ", "(dry run) ") if dry else ""
    message = ctx.M(
        f"{prefix}{copied} فایل جدید، {updated} به‌روزرسانی، {deleted} حذف.",
        f"{prefix}{copied} new, {updated} updated, {deleted} deleted.")
    return RunResult(message=message, details="\n".join(lines[:500]) + ("\n…" if len(lines) > 500 else ""))


@tool(
    id="file_timestamp", category="files", name="تغییر تاریخ فایل", name_en="Set file timestamps",
    desc="تاریخ ساخت/دسترسی فایل‌ها را تغییر می‌دهد (برای مرتب‌سازی آلبوم یا آرشیو).",
    desc_en="Change created/accessed dates of files (useful for albums and archives).",
    icon="clock", order=106,
    keywords=("touch", "change date", "تاریخ فایل", "زمان فایل"),
    fields=[
        Field("items", K_FILES, "فایل‌ها", "Files", required=True, filt=ALL_FILTER, multiple=True),
        Field("when", K_CHOICE, "تاریخ", "Date", default="now",
              choices=[("now", "همین حالا", "Now"), ("custom", "تاریخ دلخواه", "Custom date"),
                       ("shift", "جابه‌جایی به عقب/جلو", "Shift by days")]),
        Field("custom", K_TEXT, "تاریخ دلخواه", "Custom date", default="2024-01-01 12:00",
              placeholder="YYYY-MM-DD HH:MM"),
        Field("days", K_INT, "تعداد روز جابه‌جایی", "Days to shift", default=0, minimum=-3650, maximum=3650,
              visible_when=("when", "shift")),
    ],
)
def file_timestamp(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = files_or_raise(v, "items", ctx)
    mode = str(v.get("when") or "now")
    if mode == "custom":
        raw = str(v.get("custom") or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                epoch = time.mktime(datetime.strptime(raw, fmt).timetuple())
                break
            except ValueError:
                continue
        else:
            raise ToolError(ctx.M("قالب تاریخ درست نیست. نمونه: 2024-01-01 12:00",
                                  "Invalid date format. Example: 2024-01-01 12:00"))
    changed = 0
    for index, path in enumerate(items, start=1):
        ctx.progress(index, len(items), path.name)
        if mode == "now":
            epoch = time.time()
        elif mode == "shift":
            epoch = path.stat().st_mtime + int(v.get("days") or 0) * 86400
        try:
            os.utime(path, (epoch, epoch))
            changed += 1
        except OSError as exc:
            ctx.log(f"{path}: {exc}")
    stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    return RunResult(
        message=ctx.M(f"تاریخ {changed} فایل روی {stamp} تنظیم شد.",
                      f"Timestamps of {changed} file(s) set to {stamp}."),
    )


@tool(
    id="tree_export", category="files", name="نقشه‌ی درختی پوشه", name_en="Folder tree export",
    desc="ساختار پوشه‌ها را به صورت درخت متنی (برای مستندسازی یا README) می‌نویسد.",
    desc_en="Export the folder structure as a text tree, ready for docs or a README.",
    icon="list", order=107,
    keywords=("tree", "folder structure", "درخت پوشه", "ساختار پروژه"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("depth", K_INT, "حداکثر عمق", "Max depth", default=3, minimum=1, maximum=10),
        Field("show_files", K_BOOL, "نمایش فایل‌ها", "Include files", default=True),
        Field("out", K_TEXT, "ذخیره در فایل (اختیاری)", "Save to file (optional)"),
    ],
)
def tree_export(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    root = Path(str(v.get("folder") or ""))
    if not root.is_dir():
        raise ToolError(ctx.M("پوشه پیدا نشد.", "Folder not found."))
    depth_limit = max(1, int(v.get("depth") or 3))
    show_files = bool(v.get("show_files", True))
    lines: List[str] = [root.name + "/"]
    folders = files = 0

    def walk(path: Path, prefix: str, level: int) -> None:
        nonlocal folders, files
        if level > depth_limit:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        entries = [e for e in entries if show_files or e.is_dir()]
        for position, entry in enumerate(entries):
            last = position == len(entries) - 1
            branch = "└── " if last else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{branch}{entry.name}{suffix}")
            if entry.is_dir():
                folders += 1
                walk(entry, prefix + ("    " if last else "│   "), level + 1)
            else:
                files += 1

    walk(root, "", 1)
    text = "\n".join(lines)
    outputs: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        dest = Path(requested)
        if dest.suffix == "":
            dest = out_dir(ctx) / f"{root.name}_tree.txt"
        ensure_dir(dest.parent)
        dest.write_text(text, encoding="utf-8")
        outputs.append(str(dest))
        message = ctx.M(f"درخت در فایل ذخیره شد ({folders} پوشه، {files} فایل).\n{dest}",
                        f"Tree saved ({folders} folders, {files} files).\n{dest}")
    else:
        message = ctx.M(f"{folders} پوشه و {files} فایل.", f"{folders} folders and {files} files.")
    return RunResult(message=message, outputs=outputs, details=text[:8000],
                     open_target=str(outputs[0]) if outputs else None)


@tool(
    id="copy_verify", category="files", name="کپی با تأیید سلامت", name_en="Copy and verify",
    desc="فایل را کپی می‌کند و با SHA-256 مطمئن می‌شود بایت‌به‌بایت سالم رسیده است.",
    desc_en="Copy files and prove the copy is byte-identical with SHA-256.",
    icon="check", order=108,
    keywords=("verify copy", "checksum copy", "کپی مطمئن", "بررسی سلامت"),
    fields=[
        Field("items", K_FILES, "فایل‌ها", "Files", required=True, filt=ALL_FILTER, multiple=True),
        Field("out_dir", K_DIR, "پوشه‌ی مقصد", "Destination folder", required=True),
    ],
)
def copy_verify(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = files_or_raise(v, "items", ctx)
    folder = str(v.get("out_dir") or "").strip()
    if not folder:
        raise ToolError(ctx.M("پوشه‌ی مقصد را انتخاب کنید.", "Choose a destination folder."))
    target_dir = ensure_dir(folder)
    ok, bad = 0, 0
    lines: List[str] = []
    outputs: List[str] = []
    for index, path in enumerate(items, start=1):
        ctx.progress(index, len(items), ctx.M(f"کپی {path.name}", f"Copying {path.name}"))
        destination = target_dir / path.name
        counter = 1
        while destination.exists():
            destination = target_dir / f"{path.stem}-{counter}{path.suffix}"
            counter += 1
        shutil.copy2(path, destination)
        source_hash = _sha256(path, ctx)
        destination_hash = _sha256(destination, ctx)
        if source_hash == destination_hash:
            ok += 1
            outputs.append(str(destination))
            lines.append(f"✓ {path.name}  {human_size(path.stat().st_size)}  {source_hash[:16]}…")
        else:
            bad += 1
            lines.append(f"✗ {path.name} — {ctx.M('هش متفاوت است', 'hash mismatch')}")
    return RunResult(
        message=ctx.M(f"{ok} فایل سالم کپی شد" + (f"، {bad} خطا" if bad else "") + f".\n{target_dir}",
                      f"Copied {ok} file(s) with matching hashes" + (f", {bad} failed" if bad else "")
                      + f".\n{target_dir}"),
        outputs=outputs, details="\n".join(lines), open_target=str(target_dir),
    )


@tool(
    id="file_lock_check", category="files", name="بررسی قفل فایل", name_en="File lock check",
    desc="می‌گوید کدام فایل‌ها توسط برنامه‌ی دیگری باز/قفل شده‌اند (خطای «در حال استفاده»).",
    desc_en="Detect files locked by other programs (the 'file in use' error).",
    icon="lock", order=109,
    keywords=("file in use", "locked file", "فایل قفل", "در حال استفاده"),
    fields=[
        Field("items", K_FILES, "فایل‌ها", "Files", required=True, filt=ALL_FILTER, multiple=True),
    ],
)
def file_lock_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = [Path(p) for p in (v.get("items") or [])]
    if not items:
        raise ToolError(ctx.M("فایلی انتخاب نشده.", "No files selected."))
    locked: List[str] = []
    free: List[str] = []
    for index, path in enumerate(items, start=1):
        ctx.progress(index, len(items), path.name)
        if not path.exists():
            locked.append(f"✕ {path} — {ctx.M('وجود ندارد', 'missing')}")
            continue
        try:
            with open(path, "r+b"):
                pass
            free.append(str(path))
        except OSError:
            locked.append(str(path))
    message = ctx.M(f"{len(free)} فایل آزاد، {len(locked)} فایل قفل/در دسترس‌نبود.",
                    f"{len(free)} free, {len(locked)} locked or unavailable.")
    details = "\n".join([f"🔒 {p}" for p in locked] + [f"✓ {p}" for p in free])
    return RunResult(message=message, details=details)
