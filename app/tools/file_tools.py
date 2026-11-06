"""File manager utilities: rename, duplicates, hashes, archives, shredder."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILES, K_FILE, K_INT, K_PASSWORD, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import (hash_file, human_size, list_files, natural_key, safe_filename,
                          unique_path, walk_files)
from ._helpers import reject_disk_root
from ._helpers import as_path_list, ensure_dir, first_file, out_dir, save_target

ALL_FILTER = "همه فایل‌ها (*.*)"


def _folder(values: dict, key: str, ctx: RunContext) -> Path:
    raw = str(values.get(key) or "").strip()
    if not raw:
        raise ToolError(ctx.M("لطفاً یک پوشه انتخاب کنید.", "Please choose a folder."))
    path = Path(raw)
    if not path.exists() or not path.is_dir():
        raise ToolError(ctx.M(f"پوشه پیدا نشد: {path}", f"Folder not found: {path}"))
    return reject_disk_root(path, ctx)


# ------------------------------------------------------------------ rename
@tool(
    id="batch_rename", category="files", name="تغییر نام گروهی", name_en="Batch rename",
    desc="با الگو، جست‌وجو/جایگزینی یا شماره‌گذاری، نام صدها فایل را یکجا عوض می‌کند.",
    desc_en="Rename many files with a template, find/replace or numbering.",
    icon="note", order=10, keywords=("batch rename", "rename", "تغییر نام", "اسم فایل"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("mode", K_CHOICE, "روش", "Method", default="template",
              choices=[("template", "الگو", "Template"),
                       ("replace", "جست‌وجو و جایگزینی", "Find & replace"),
                       ("regex", "عبارت منظم (Regex)", "Regex")]),
        Field("template", K_TEXT, "الگو", "Template", default="{name}_{n}",
              help="می‌توانید از {name} {n} {date} {ext} استفاده کنید",
              help_en="Available: {name} {n} {date} {ext}", visible_when=("mode", "template")),
        Field("find", K_TEXT, "متن جست‌وجو", "Find", visible_when=("mode", ("replace", "regex"))),
        Field("replace", K_TEXT, "جایگزین", "Replace", default="", visible_when=("mode", ("replace", "regex"))),
        Field("start", K_INT, "شروع شماره", "Start number", default=1, minimum=0, maximum=1000000),
        Field("padding", K_INT, "تعداد رقم شماره", "Number padding", default=2, minimum=1, maximum=8),
        Field("ext_filter", K_TEXT, "فقط این پسوندها", "Only extensions", default="",
              placeholder="مثلاً: jpg,png (خالی = همه)", placeholder_en="e.g. jpg,png (empty = all)"),
        Field("recursive", K_BOOL, "شامل زیرپوشه‌ها", "Include subfolders", default=False),
        Field("preview", K_BOOL, "فقط پیش‌نمایش (بدون تغییر)", "Preview only (no changes)", default=True),
    ],
)
def batch_rename(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, "folder", ctx)
    exts = [e.strip().lower().lstrip(".") for e in str(v.get("ext_filter") or "").split(",") if e.strip()]
    files = list_files(folder, recursive=bool(v.get("recursive")))
    if exts:
        files = [f for f in files if f.suffix.lower().lstrip(".") in exts]
    files.sort(key=lambda p: natural_key(p.name))
    if not files:
        raise ToolError(ctx.M("فایلی در این پوشه پیدا نشد.", "No files found in this folder."))
    mode = str(v.get("mode") or "template").strip().lower()
    if mode not in ("template", "replace", "regex"):
        raise ToolError(ctx.M(f"روش نامعتبر است: {mode} (الگو/جایگزینی/Regex)",
                              f"unknown method: {mode} (template/replace/regex)"))
    start = int(v.get("start") or 1)
    padding = max(1, int(v.get("padding") or 2))
    import datetime

    today = datetime.date.today().isoformat()
    preview_only = bool(v.get("preview"))
    planned: List[tuple[Path, Path]] = []
    pattern = None
    if mode == "regex":
        raw = str(v.get("find") or "")
        if not raw:
            raise ToolError(ctx.M("عبارت منظم را وارد کنید.", "Enter a regex pattern."))
        try:
            pattern = re.compile(raw)
        except re.error as exc:
            raise ToolError(ctx.M(f"عبارت منظم نامعتبر است: {exc}", f"Invalid regex: {exc}"))
    for index, path in enumerate(files):
        number = str(start + index).zfill(padding)
        if mode == "template":
            template = str(v.get("template") or "{name}_{n}")
            stem = (template
                    .replace("{name}", path.stem)
                    .replace("{n}", number)
                    .replace("{date}", today)
                    .replace("{ext}", path.suffix.lstrip(".")))
            new_name = f"{safe_filename(stem)}{path.suffix}"
        elif mode == "replace":
            find = str(v.get("find") or "")
            new_name = path.name.replace(find, str(v.get("replace") or "")) if find else path.name
        else:
            new_name = pattern.sub(str(v.get("replace") or ""), path.name) if pattern else path.name
        new_name = safe_filename(new_name)
        if new_name == path.name:
            continue
        planned.append((path, path.with_name(new_name)))
    if not planned:
        return RunResult(message=ctx.M("نامی برای تغییر پیدا نشد.", "Nothing to rename."))
    if preview_only:
        lines = [f"{old.name}  →  {new.name}" for old, new in planned[:200]]
        return RunResult(
            message=ctx.M(f"پیش‌نمایش {len(planned)} تغییر (بدون اعمال). برای اجرا تیک «فقط پیش‌نمایش» را بردارید.",
                          f"Preview of {len(planned)} renames (nothing applied)."),
            details="\n".join(lines),
        )
    done = 0
    skipped: List[str] = []
    renames: List[tuple] = []
    for index, (old, new) in enumerate(planned):
        ctx.progress(index, len(planned), f"{old.name} → {new.name}")
        target = unique_path(new) if new.exists() else new
        try:
            old.rename(target)
            done += 1
            renames.append((old, target))
        except OSError as exc:
            skipped.append(f"{old.name}: {exc}")
    if renames:
        from ..core.journal import journal
        journal().record_moves(renames, ctx.M(f"تغییر نام {len(renames)} فایل",
                                              f"renamed {len(renames)} file(s)"),
                               tool_id="batch_rename")
    details = "\n".join(skipped)
    return RunResult(
        message=ctx.M(f"{done} فایل تغییر نام یافت." + (f"\n{len(skipped)} مورد ناموفق." if skipped else ""),
                      f"{done} file(s) renamed." + (f"\n{len(skipped)} failed." if skipped else "")),
        details=details, open_target=str(folder),
    )


# ------------------------------------------------------------- duplicates
@tool(
    id="find_duplicates", category="files", name="یافتن فایل‌های تکراری", name_en="Find duplicates",
    desc="با اثر انگشت محتوا (SHA-256) فایل‌های تکراری را پیدا می‌کند.",
    desc_en="Find duplicate files using SHA-256 content hashing.",
    icon="scan", order=20, danger=True, keywords=("duplicate files", "تکراری", "فضای هدر رفته"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("recursive", K_BOOL, "شامل زیرپوشه‌ها", "Include subfolders", default=True),
        Field("min_size", K_INT, "حداقل حجم (KB)", "Minimum size (KB)", default=0, minimum=0, maximum=10000000),
        Field("delete", K_BOOL, "حذف نسخه‌های اضافی (خطرناک!)", "Delete extra copies (dangerous!)", default=False,
              help="فقط وقتی این گزینه روشن باشد حذف انجام می‌شود؛ پیش‌فرض فقط گزارش است.",
              help_en="Deletion only happens when this is on; the default is a report."),
    ],
)
def find_duplicates(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, "folder", ctx)
    files = list_files(folder, recursive=bool(v.get("recursive")))
    min_size = int(v.get("min_size") or 0) * 1024
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= min_size and size > 0:
            by_size[size].append(path)
    candidates = [group for group in by_size.values() if len(group) > 1]
    total_candidates = sum(len(g) for g in candidates)
    groups: Dict[str, List[Path]] = defaultdict(list)
    processed = 0
    for group in candidates:
        for path in group:
            processed += 1
            ctx.progress(processed, total_candidates, path.name)
            try:
                digest = hash_file(path, "sha256")
            except OSError:
                continue
            groups[digest].append(path)
    dupes = {k: sorted(v2, key=lambda p: len(str(p))) for k, v2 in groups.items() if len(v2) > 1}
    def _size(item: Path) -> int:
        try:
            return item.stat().st_size
        except OSError:
            return 0

    wasted = sum(sum(_size(p) for p in paths[1:]) for paths in dupes.values())
    lines: List[str] = []
    removed = 0
    for digest, paths in dupes.items():
        lines.append(f"[{digest[:12]}] {human_size(paths[0].stat().st_size)} × {len(paths)}")
        lines.append(f"    keep: {paths[0]}")
        for extra in paths[1:]:
            lines.append(f"    dup : {extra}")
            if v.get("delete"):
                try:
                    extra.unlink()
                    removed += 1
                except OSError as exc:
                    lines.append(f"    (failed: {exc})")
        if len(lines) > 1000:
            lines.append("…")
            break
    if not dupes:
        return RunResult(message=ctx.M("فایل تکراری پیدا نشد. 🎉", "No duplicates found."))
    message = ctx.M(
        f"{len(dupes)} گروه تکراری، {human_size(wasted)} فضای قابل آزادسازی.",
        f"{len(dupes)} duplicate groups, {human_size(wasted)} reclaimable.",
    )
    if v.get("delete"):
        message += ctx.M(f"\n{removed} فایل حذف شد.", f"\n{removed} file(s) deleted.")
    return RunResult(message=message, details="\n".join(lines), open_target=str(folder))


# ------------------------------------------------------------------- hashes
@tool(
    id="hash_files", category="files", name="اثر انگشت فایل", name_en="File checksum",
    desc="MD5/SHA-1/SHA-256 فایل‌ها را محاسبه و در یک گزارش ذخیره می‌کند.",
    desc_en="Compute MD5/SHA-1/SHA-256 checksums and save a report.",
    icon="hash", order=30, keywords=("hash", "checksum", "sha256", "md5", "اثر انگشت"),
    fields=[
        Field("src", K_FILES, "فایل‌ها", "Files", required=True, filt=ALL_FILTER, multiple=True),
        Field("algo", K_CHOICE, "الگوریتم", "Algorithm", default="sha256",
              choices=[("sha256", "SHA-256", "SHA-256"), ("md5", "MD5", "MD5"),
                       ("sha1", "SHA-1", "SHA-1"), ("sha512", "SHA-512", "SHA-512")]),
        Field("save", K_SAVE, "ذخیره گزارش در", "Save report to", filt="Text (*.txt)"),
    ],
)
def hash_files(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    algo = str(v.get("algo") or "sha256")
    lines: List[str] = []
    for index, path in enumerate(files):
        if not path.exists():
            lines.append(f"{path}: missing")
            continue
        ctx.progress(index, len(files), path.name)
        digest = hash_file(path, algo,
                           lambda done, total, name=path.name: ctx.progress(done, total, name))
        lines.append(f"{digest}  {path}")
    report = "\n".join(lines)
    outputs: List[str] = []
    save_to = str(v.get("save") or "").strip()
    if save_to:
        dest = save_target(ctx, save_to, "checksums.txt")
        dest.write_text(report + "\n", encoding="utf-8")
        outputs.append(str(dest))
    return RunResult(message=ctx.M(f"{len(lines)} فایل هش شد.", f"{len(lines)} file(s) hashed."),
                     details=report, outputs=outputs)


# -------------------------------------------------------------------- zip
@tool(
    id="zip_create", category="files", name="ساخت فایل ZIP", name_en="Create ZIP",
    desc="فایل‌ها یا یک پوشه را با انتخاب سطح فشرده‌سازی در ZIP بسته‌بندی می‌کند.",
    desc_en="Pack files or a folder into a ZIP archive.",
    icon="zip", order=40, keywords=("zip", "compress files", "زیپ", "بسته بندی"),
    fields=[
        Field("src", K_FILES, "فایل‌ها / پوشه‌ها", "Files / folders", required=True, filt=ALL_FILTER,
              multiple=True),
        Field("level", K_CHOICE, "فشرده‌سازی", "Compression", default="normal",
              choices=[("none", "بدون فشرده‌سازی", "Store"), ("normal", "معمولی", "Normal"),
                       ("max", "حداکثر", "Maximum")]),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="ZIP (*.zip)"),
    ],
)
def zip_create(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    items = [Path(p) for p in as_path_list(v.get("src"))]
    if not items:
        raise ToolError(ctx.M("چیزی برای بسته‌بندی انتخاب نشد.", "Nothing selected."))
    level = {"none": zipfile.ZIP_STORED, "normal": zipfile.ZIP_DEFLATED,
             "max": zipfile.ZIP_DEFLATED}.get(str(v.get("level") or "normal"), zipfile.ZIP_DEFLATED)
    compresslevel = 9 if str(v.get("level")) == "max" else 6
    name = f"{safe_filename(items[0].stem if len(items) == 1 else 'archive')}.zip"
    dest = save_target(ctx, v.get("out", ""), name)
    total = len(items)
    with zipfile.ZipFile(dest, "w", compression=level, compresslevel=compresslevel) as archive:
        for index, item in enumerate(items):
            if not item.exists():
                raise ToolError(ctx.M(f"پیدا نشد: {item}", f"Not found: {item}"))
            if item.is_dir():
                for path in sorted(item.rglob("*"), key=lambda p: natural_key(str(p))):
                    if path.is_file():
                        ctx.progress(index, total, path.name)
                        archive.write(path, path.relative_to(item.parent))
            else:
                ctx.progress(index, total, item.name)
                archive.write(item, item.name)
    return RunResult(
        message=ctx.M(f"ZIP ساخته شد ({human_size(dest.stat().st_size)}).",
                      f"ZIP created ({human_size(dest.stat().st_size)})."),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="zip_extract", category="files", name="استخراج ZIP", name_en="Extract ZIP",
    desc="فایل ZIP/RAR-مانند را با حفظ ساختار پوشه‌ها باز می‌کند.",
    desc_en="Extract a ZIP archive keeping the folder structure.",
    icon="folder", order=50, keywords=("unzip", "extract", "باز کردن زیپ"),
    fields=[
        Field("src", K_FILE, "فایل ZIP", "ZIP file", required=True, filt="ZIP (*.zip);;همه فایل‌ها (*)"),
        Field("dest", K_DIR, "پوشه‌ی مقصد", "Destination folder"),
        Field("password", K_PASSWORD, "رمز (اختیاری)", "Password (optional)"),
    ],
)
def zip_extract(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    dest = Path(str(v.get("dest") or "")).expanduser() if str(v.get("dest") or "").strip() else out_dir(ctx) / src.stem
    dest = ensure_dir(dest)
    password = str(v.get("password") or "").encode() or None
    extracted = 0
    skipped: List[str] = []
    renamed: List[str] = []
    root = dest.resolve()
    max_entries = 10_000
    max_total_bytes = 2 * 1024 * 1024 * 1024
    max_ratio = 200
    total_written = 0
    try:
        with zipfile.ZipFile(src) as archive:
            members = archive.infolist()
            total = len(members)
            if total > max_entries:
                raise ToolError(ctx.M(
                    f"تعداد فایل‌های داخل آرشیو بیش از سقف مجاز ({max_entries:,}) است.",
                    f"Archive exceeds the maximum entry limit ({max_entries:,}).",
                ))
            for index, member in enumerate(members):
                ctx.progress(index, total, member.filename)
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    skipped.append(member.filename)
                    continue
                if member.file_size > 10 * 1024 * 1024:
                    ratio = member.file_size / max(1, member.compress_size)
                    if ratio > max_ratio:
                        raise ToolError(ctx.M(
                            f"نسبت فشرده‌سازی مشکوک (ZIP bomb) در {member.filename}.",
                            f"Suspicious compression ratio (ZIP bomb) in {member.filename}.",
                        ))
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
                    skipped.append(member.filename)
                    continue
                target = (dest / name).resolve()
                # zip-slip: never let a member escape the destination folder.
                if root not in target.parents and target != root:
                    skipped.append(member.filename)
                    continue
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    extracted += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    # Default collision policy: never overwrite an existing file;
                    # write the new member under a unique name instead.
                    unique = unique_path(target)
                    renamed.append(f"{member.filename} → {unique.name}")
                    target = unique
                tmp = target.with_name(target.name + f".part-{os.getpid()}")
                try:
                    with archive.open(member, pwd=password) as src_fh, open(tmp, "wb") as dst_fh:
                        while True:
                            chunk = src_fh.read(1 << 20)
                            if not chunk:
                                break
                            total_written += len(chunk)
                            if total_written > max_total_bytes:
                                raise ToolError(ctx.M(
                                    "حجم بازشده‌ی آرشیو از سقف مجاز (۲ گیگابایت) گذشت.",
                                    "Uncompressed archive size exceeded the 2 GiB safety limit.",
                                ))
                            dst_fh.write(chunk)
                    os.replace(tmp, target)      # atomic: no half-written files
                except ToolError:
                    tmp.unlink(missing_ok=True)
                    raise
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                extracted += 1
    except RuntimeError as exc:
        raise ToolError(ctx.M(f"رمز اشتباه یا فایل معیوب است: {exc}", f"Wrong password or corrupt file: {exc}"))
    except zipfile.BadZipFile as exc:
        raise ToolError(ctx.M(f"فایل ZIP معتبر نیست: {exc}", f"Not a valid ZIP: {exc}"))
    message = ctx.M(f"{extracted} مورد استخراج شد.", f"{extracted} item(s) extracted.")
    if skipped:
        message += ctx.M(f" · {len(skipped)} مورد ناامن (خارج از مقصد) رد شد",
                         f" · skipped {len(skipped)} unsafe path(s)")
    if renamed:
        message += ctx.M(
            f" · {len(renamed)} مورد به‌خاطر وجود فایل هم‌نام، نام منحصربه‌فرد گرفت:\n" + "\n".join(renamed[:10]),
            f" · {len(renamed)} item(s) renamed to avoid overwriting existing files:\n" + "\n".join(renamed[:10]),
        )
    return RunResult(message=message, outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------------------ shred
def _overwrite(path: Path, passes: int) -> None:
    size = path.stat().st_size
    with open(path, "ba+", buffering=0) as fh:
        for _ in range(max(1, passes)):
            fh.seek(0)
            written = 0
            while written < size:
                chunk = os.urandom(min(1024 * 1024, size - written))
                fh.write(chunk)
                written += len(chunk)
            fh.flush()
            os.fsync(fh.fileno())
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


@tool(
    id="secure_delete", category="files", name="حذف امن فایل", name_en="Secure delete",
    desc="فایل را چند بار بازنویسی و سپس پاک می‌کند تا بازیابی سخت‌تر شود "
         "(روی SSD و فایل‌سیستم‌های copy-on-write تضمین قطعی نیست).",
    desc_en="Overwrites the file a few times, then deletes it — this makes recovery "
            "hard, but on SSDs and copy-on-write filesystems it is not a guarantee.",
    icon="trash", order=60, danger=True,
    keywords=("shred", "wipe", "حذف امن", "پاک کردن قطعی"),
    fields=[
        Field("src", K_FILES, "فایل‌ها", "Files", required=True, filt=ALL_FILTER, multiple=True),
        Field("passes", K_INT, "تعداد بازنویسی", "Overwrite passes", default=3, minimum=1, maximum=7,
              help="بیشتر = کندتر. سه گذر برای دیسک معمولی کافی است.",
              help_en="More passes = slower. Three is enough on a spinning disk."),
    ],
)
def secure_delete(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    passes = int(v.get("passes") or 3)
    done = 0
    failed: List[str] = []
    for index, path in enumerate(files):
        ctx.progress(index, len(files), path.name)
        try:
            if path.is_symlink():
                raise ToolError(ctx.M("حذف امن روی میان‌بر/سیم‌لینک مجاز نیست.",
                                      "Secure delete refuses symbolic links for safety."))
            if not path.exists():
                continue
            if path.is_dir():
                raise ToolError(ctx.M("این ابزار فقط فایل می‌پذیرد.", "This tool only handles files."))
            _overwrite(path, passes)
            path.unlink()
            done += 1
        except Exception as exc:
            failed.append(f"{path.name}: {exc}")
    return RunResult(
        message=ctx.M(f"{done} فایل بازنویسی و حذف شد.", f"{done} file(s) overwritten and deleted.")
        + (ctx.M(f"\n{len(failed)} مورد ناموفق.", f"\n{len(failed)} failed.") if failed else ""),
        details=(ctx.M(
            "بازنویسی، بازیابی را سخت می‌کند ولی تضمینی نیست: روی SSD، فایل‌سیستم‌های "
            "copy-on-write (btrfs/APFS) و درایوهایی با TRIM، نسخه‌های قبلی ممکن است "
            "جای دیگری باقی بمانند. برای داده‌ی حساس از رمزنگاری کامل دیسک استفاده کنید.",
            "Overwriting makes recovery hard but does not guarantee it: on SSDs, "
            "copy-on-write filesystems (btrfs/APFS) and drives with TRIM the old blocks "
            "may survive elsewhere. Use full-disk encryption for sensitive data.")
            + ("\n\n" + "\n".join(failed) if failed else "")),
    )


# --------------------------------------------------------------- large files
@tool(
    id="large_files", category="files", name="فایل‌های حجیم", name_en="Largest files",
    desc="بزرگ‌ترین فایل‌های یک درایو/پوشه را برای آزادسازی فضا پیدا می‌کند.",
    desc_en="Find the biggest files in a folder to free up space.",
    icon="speed", order=70, keywords=("large files", "فضای دیسک", "بزرگترین فایل"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("count", K_INT, "تعداد نتیجه", "Results", default=30, minimum=5, maximum=500),
        Field("min_mb", K_INT, "حداقل حجم (MB)", "Minimum size (MB)", default=10, minimum=0, maximum=100000),
    ],
)
def large_files(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, "folder", ctx)
    min_bytes = int(v.get("min_mb") or 0) * 1024 * 1024
    limit = int(v.get("count") or 30)
    found: List[tuple[int, Path]] = []
    scanned = 0
    for path in walk_files(folder):
        if True:  # keep the existing indentation of the loop body
            try:
                size = path.stat().st_size
            except OSError:
                continue
            scanned += 1
            if scanned % 200 == 0:
                ctx.progress(0, 0, ctx.M(f"{scanned} فایل بررسی شد…", f"{scanned} files scanned…"))
            if size >= min_bytes:
                found.append((size, path))
    found.sort(key=lambda item: -item[0])
    found = found[:limit]
    if not found:
        return RunResult(message=ctx.M("فایل بزرگی پیدا نشد.", "No large files found."))
    lines = [f"{human_size(size):>10}  {path}" for size, path in found]
    total = sum(size for size, _ in found)
    return RunResult(
        message=ctx.M(f"{len(found)} فایل بزرگ، مجموع {human_size(total)}.",
                      f"{len(found)} large file(s), {human_size(total)} total."),
        details="\n".join(lines), open_target=str(folder),
    )


# --------------------------------------------------------------- folder size
@tool(
    id="folder_size", category="files", name="حجم پوشه‌ها", name_en="Folder sizes",
    desc="حجم هر زیرپوشه را نشان می‌دهد تا بفهمید فضا کجا مصرف شده.",
    desc_en="Show the size of each subfolder.",
    icon="folder", order=80, keywords=("folder size", "حجم پوشه", "فضا"),
    fields=[Field("folder", K_DIR, "پوشه", "Folder", required=True)],
)
def folder_size(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, "folder", ctx)

    def dir_size(path: Path) -> int:
        total = 0
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    entries = [p for p in folder.iterdir() if p.is_dir()]
    results: List[tuple[int, Path]] = []
    for index, path in enumerate(entries):
        ctx.progress(index, len(entries), path.name)
        try:
            results.append((dir_size(path), path))
        except OSError:
            continue
    loose = sum(f.stat().st_size for f in folder.glob("*") if f.is_file())
    results.sort(key=lambda item: -item[0])
    total = sum(size for size, _ in results) + loose
    lines = [f"{human_size(size):>10}  {path.name}" for size, path in results]
    lines.append(f"{human_size(loose):>10}  {ctx.M('(فایل‌های داخل خود پوشه)', '(files directly in folder)')}")
    return RunResult(
        message=ctx.M(f"مجموع: {human_size(total)} در {len(results)} زیرپوشه.",
                      f"Total: {human_size(total)} across {len(results)} subfolder(s)."),
        details="\n".join(lines), open_target=str(folder),
    )


# ------------------------------------------------------------ search files
@tool(
    id="file_search", category="files", name="جست‌وجوی فایل", name_en="Search files",
    desc="بر اساس نام یا محتوای متنی، فایل‌ها را در یک پوشه پیدا می‌کند.",
    desc_en="Find files by name pattern or text content.",
    icon="search", order=90, keywords=("search files", "پیدا کردن فایل", "grep"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("name", K_TEXT, "الگوی نام", "Name pattern", default="*"),
        Field("content", K_TEXT, "متن داخل فایل (اختیاری)", "Text inside file (optional)"),
        Field("limit", K_INT, "حداکثر نتیجه", "Max results", default=200, minimum=10, maximum=5000),
    ],
)
def file_search(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = _folder(v, "folder", ctx)
    pattern = str(v.get("name") or "*") or "*"
    content = str(v.get("content") or "").strip().lower()
    limit = int(v.get("limit") or 200)
    matches: List[str] = []
    scanned = 0
    for path in walk_files(folder):
        if pattern not in ("*", "") and not fnmatch.fnmatch(path.name, pattern):
            continue
        if not path.is_file():
            continue
        scanned += 1
        if scanned % 300 == 0:
            ctx.progress(0, 0, ctx.M(f"{scanned} فایل بررسی شد…", f"{scanned} files scanned…"))
        if content:
            try:
                if path.stat().st_size > 20 * 1024 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if content not in text:
                continue
        matches.append(str(path))
        if len(matches) >= limit:
            break
    if not matches:
        return RunResult(message=ctx.M("نتیجه‌ای پیدا نشد.", "No matches found."))
    return RunResult(
        message=ctx.M(f"{len(matches)} فایل پیدا شد.", f"{len(matches)} file(s) found."),
        details="\n".join(matches), outputs=matches[:50], open_target=str(folder),
    )


# --------------------------------------------------------------- split / join
@tool(
    id="split_file", category="files", name="تقسیم فایل بزرگ", name_en="Split file",
    desc="فایل بزرگ را به بخش‌های دلخواه (برای فلش یا ایمیل) تقسیم می‌کند.",
    desc_en="Split a big file into chunks (USB / email friendly).",
    icon="crop", order=100, keywords=("split file", "تقسیم فایل", "بخش بندی"),
    fields=[
        Field("src", K_FILE, "فایل", "File", required=True, filt=ALL_FILTER),
        Field("size_mb", K_INT, "حجم هر بخش (MB)", "Chunk size (MB)", default=90, minimum=1, maximum=100000),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def split_file(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    chunk = max(1, int(v.get("size_mb") or 90)) * 1024 * 1024
    folder = ensure_dir(str(v.get("out_dir") or out_dir(ctx)))
    total = src.stat().st_size
    parts: List[str] = []
    with open(src, "rb") as fh:
        index = 1
        read = 0
        while True:
            data = fh.read(chunk)
            if not data:
                break
            dest = unique_path(folder / f"{src.name}.part{index:03d}")
            dest.write_bytes(data)
            parts.append(str(dest))
            read += len(data)
            ctx.progress(read, total, dest.name)
            index += 1
    return RunResult(
        message=ctx.M(f"{len(parts)} بخش ساخته شد. برای چسباندن از «اتصال بخش‌ها» استفاده کنید.",
                      f"{len(parts)} part(s) created. Rejoin with 'Join parts'."),
        outputs=parts, open_target=str(folder),
    )


@tool(
    id="join_file", category="files", name="اتصال بخش‌ها", name_en="Join parts",
    desc="بخش‌های .partXXX را به فایل اصلی برمی‌گرداند.",
    desc_en="Reassemble .partXXX chunks into the original file.",
    icon="zip", order=110, keywords=("join parts", "اتصال", "چسباندن بخش‌ها"),
    fields=[
        Field("src", K_FILES, "بخش‌ها", "Parts", required=True, filt=ALL_FILTER, multiple=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt=ALL_FILTER),
    ],
)
def join_file(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    parts = [Path(p) for p in as_path_list(v.get("src"))]
    if not parts:
        raise ToolError(ctx.M("بخشی انتخاب نشد.", "No parts selected."))
    parts.sort(key=lambda p: natural_key(p.name))
    first_name = parts[0].name.split(".part")[0] or "joined.bin"
    dest = save_target(ctx, v.get("out", ""), first_name)
    total = sum(p.stat().st_size for p in parts)
    written = 0
    with open(dest, "wb") as out:
        for path in parts:
            with open(path, "rb") as fh:
                while True:
                    data = fh.read(4 * 1024 * 1024)
                    if not data:
                        break
                    out.write(data)
                    written += len(data)
                    ctx.progress(written, total, path.name)
    return RunResult(message=ctx.M(f"{len(parts)} بخش متصل شد ({human_size(written)}).",
                                  f"{len(parts)} parts joined ({human_size(written)})."),
                     outputs=[str(dest)], open_target=str(dest))
