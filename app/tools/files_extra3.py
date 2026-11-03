"""File extras: AES-encrypted ZIP archives and long-path auditing for Windows."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_DIR, K_FILE, K_FILES, K_INT, K_PASSWORD, K_SAVE, Field, RunContext, RunResult,
    ToolError,
)
from ..core.utils import ensure_dir, human_size, unique_path
from ._helpers import files_or_raise, first_file, out_dir, reject_disk_root, save_target

# Windows' classic MAX_PATH budget; Explorer and many installers still obey it.
DEFAULT_LIMIT = 260


def _pyzipper(ctx: RunContext):
    try:
        import pyzipper

        return pyzipper
    except Exception:
        raise ToolError(ctx.M(
            "برای این ابزار pyzipper لازم است: pip install pyzipper",
            "This tool needs pyzipper: pip install pyzipper"))


@tool(
    id="archive_encrypt", category="files", name="ساخت ZIP رمزدار (AES)",
    name_en="Create an AES-encrypted ZIP",
    desc="فایل‌ها را در یک ZIP با رمز واقعی AES-256 می‌گذارد — نه آن رمز بی‌ارزش قدیمی ZIP.",
    desc_en="Packs files into a ZIP protected with real AES-256, not the broken legacy ZIP password.",
    icon="lock", order=60,
    keywords=("zip", "encrypt", "aes", "password", "archive", "زیپ", "رمز", "رمزدار", "رمزگذاری"),
    fields=[
        Field("src", K_FILES, "فایل‌ها", "Files", required=True),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
        Field("level", K_CHOICE, "فشرده‌سازی", "Compression", default="normal",
              choices=[("store", "بدون فشرده‌سازی", "Store only"), ("normal", "معمولی", "Normal"),
                       ("max", "حداکثر", "Maximum")]),
        Field("out", K_SAVE, "ذخیره‌ی ZIP", "Save ZIP", filt="ZIP (*.zip)"),
    ],
)
def archive_encrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pyzipper = _pyzipper(ctx)
    files = files_or_raise(v, "src", ctx)
    password = str(v.get("password") or "")
    if not password:
        raise ToolError(ctx.M("رمز را وارد کنید.", "Enter a password."))
    if len(password) < 6:
        raise ToolError(ctx.M("رمز کوتاه است؛ حداقل ۶ نویسه بگذارید.",
                              "That password is too short; use at least 6 characters."))
    level = str(v.get("level") or "normal")
    compression = {"store": zipfile.ZIP_STORED, "normal": zipfile.ZIP_DEFLATED,
                   "max": zipfile.ZIP_DEFLATED}.get(level, zipfile.ZIP_DEFLATED)
    target = save_target(ctx, str(v.get("out") or "").strip(), "archive_encrypted.zip")
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    total = 0
    written = 0
    with pyzipper.AESZipFile(target, "w", compression=compression,
                             compresslevel=9 if level == "max" else 6) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for index, path in enumerate(files, start=1):
            ctx.progress(index, len(files), path.name)
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        archive.write(child, arcname=str(child.relative_to(path.parent)))
                        total += child.stat().st_size
                        written += 1
            else:
                archive.write(path, arcname=path.name)
                total += path.stat().st_size
                written += 1
    size = target.stat().st_size
    ratio = (1 - size / total) * 100 if total else 0
    change = (f"{ratio:.0f}% کمتر" if ratio >= 0 else f"{-ratio:.0f}% بیشتر") \
        if ctx.lang != "en" else (f"{ratio:.0f}% smaller" if ratio >= 0 else f"{-ratio:.0f}% larger")
    return RunResult(
        message=ctx.M(
            f"{written} فایل با AES-256 رمزدار شد ({human_size(total)} → {human_size(size)}، {change}).",
            f"{written} file(s) encrypted with AES-256 ({human_size(total)} → {human_size(size)}, "
            f"{change})."),
        details=ctx.M(
            "این آرشیو با رمز باز می‌شود؛ رمز را گم نکنید — راه بازیابی وجود ندارد.",
            "The archive needs the password; there is no recovery if you lose it."),
        outputs=[str(target)], open_target=str(target))


@tool(
    id="archive_decrypt", category="files", name="باز کردن ZIP رمزدار",
    name_en="Open an encrypted ZIP",
    desc="آرشیو ZIP رمزدار (AES یا کلاسیک) را در پوشه‌ی خروجی باز می‌کند؛ مسیرهای خطرناک را رد می‌کند.",
    desc_en="Extracts a password-protected ZIP (AES or legacy) into a folder and refuses path escapes.",
    icon="archive", order=61,
    keywords=("zip", "decrypt", "extract", "unzip", "رمز", "باز کردن", "استخراج", "زیپ"),
    fields=[
        Field("src", K_FILE, "فایل ZIP", "ZIP file", required=True, filt="ZIP (*.zip)"),
        Field("password", K_PASSWORD, "رمز", "Password"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def archive_decrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    source = first_file(v, "src", ctx)
    password = str(v.get("password") or "")
    requested_dir = str(v.get("out_dir") or "").strip()
    folder = ensure_dir(Path(requested_dir) if requested_dir else out_dir(ctx, "unzipped"))
    destination = folder.resolve()
    extracted: List[str] = []
    skipped: List[str] = []
    # pyzipper reads both AES-encrypted and ordinary ZIPs; the stdlib reader
    # cannot decrypt AES at all, so it is only the fallback.
    archive = None
    try:
        import pyzipper

        archive = pyzipper.AESZipFile(source)
    except Exception:                                  # noqa: BLE001 - pyzipper optional
        try:
            archive = zipfile.ZipFile(source)
        except zipfile.BadZipFile as exc:
            raise ToolError(ctx.M(f"فایل ZIP سالم نیست: {exc}", f"Not a valid ZIP file: {exc}"))
    with archive:
        infos = archive.infolist()
        if not infos:
            raise ToolError(ctx.M("این آرشیو خالی است.", "This archive is empty."))
        for index, info in enumerate(infos, start=1):
            ctx.progress(index, len(infos), info.filename)
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
                skipped.append(name)
                continue
            target = (destination / name).resolve()
            try:
                target.relative_to(destination)
            except ValueError:
                skipped.append(name)
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            key = password.encode("utf-8") if password else None
            try:
                with archive.open(info, pwd=key) as handle, target.open("wb") as out_handle:
                    while True:
                        chunk = handle.read(1 << 20)
                        if not chunk:
                            break
                        out_handle.write(chunk)
            except RuntimeError as exc:
                if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                    raise ToolError(ctx.M(
                        "رمز اشتباه است یا این فایل رمز دارد.",
                        "Wrong password, or the entry is encrypted."))
                raise ToolError(ctx.M(f"استخراج ناموفق بود: {exc}", f"Extraction failed: {exc}"))
            except NotImplementedError as exc:
                raise ToolError(ctx.M(
                    f"این نوع رمزگذاری پشتیبانی نمی‌شود (احتمالاً AES): {exc}\n"
                    "با «ساخت ZIP رمزدار» خود WinGenius باز می‌شود.",
                    f"This encryption is not supported (probably AES): {exc}\n"
                    "ZIPs made by WinGenius itself always open."))
            extracted.append(str(target))
    details = ctx.M(f"{len(extracted)} فایل در {folder} باز شد.",
                    f"{len(extracted)} file(s) extracted into {folder}.")
    if skipped:
        details += "\n" + ctx.M("رد شد (مسیر ناامن): ", "Refused (unsafe path): ") + ", ".join(skipped[:10])
    return RunResult(message=details, outputs=extracted[:200], open_target=str(folder))


@tool(
    id="path_length_audit", category="files", name="بازرسی طول مسیر فایل‌ها",
    name_en="Audit long file paths",
    desc="مسیرهایی که از سقف ویندوز (۲۶۰ نویسه) رد می‌شوند را پیدا می‌کند — عامل اصلی خطاهای عجیب کپی و بکاپ.",
    desc_en="Finds paths that break Windows' 260-character limit — the usual cause of bizarre copy/backup errors.",
    icon="ruler", order=62,
    keywords=("path", "max_path", "260", "long", "windows", "مسیر", "طولانی", "خطا", "کپی"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("limit", K_INT, "سقف طول مسیر", "Path limit", default=DEFAULT_LIMIT,
              minimum=64, maximum=4096),
        Field("save", K_SAVE, "ذخیره‌ی گزارش", "Save the report", filt="CSV (*.csv *.txt)"),
    ],
)
def path_length_audit(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder_raw = str(v.get("folder") or "").strip()
    if not folder_raw or not Path(folder_raw).is_dir():
        raise ToolError(ctx.M(f"پوشه پیدا نشد: {folder_raw or '—'}",
                              f"Folder not found: {folder_raw or '—'}"))
    folder = reject_disk_root(Path(folder_raw), ctx)
    limit = int(v.get("limit") or DEFAULT_LIMIT)
    longest: List[tuple] = []
    over = 0
    scanned = 0
    deepest = 0
    for path in folder.rglob("*"):
        scanned += 1
        if scanned % 200 == 0:
            ctx.progress(scanned, 0, path.name)
        text = str(path)
        deepest = max(deepest, text.count("\\") + text.count("/"))
        if len(text) > limit:
            over += 1
            longest.append((len(text), text))
    longest.sort(reverse=True)
    lines = [
        f"folder   {folder}",
        f"limit    {limit} characters",
        f"scanned  {scanned} entries",
        f"over     {over}",
        f"deepest  {deepest} levels",
        "",
    ]
    for length, text in longest[:40]:
        lines.append(f"{length:5}  {text}")
    if not over:
        lines.append(("— هیچ مسیری از سقف رد نشده است." if ctx.lang != "en"
                      else "— no path exceeds the limit."))
    report = "\n".join(lines)
    saved: List[str] = []
    requested = str(v.get("save") or "").strip()
    if requested:
        target = save_target(ctx, requested, "long_paths.csv")
        if target.suffix.lower() == ".csv":
            body = "length,path\n" + "\n".join(
                f'{length},"{text.replace(chr(34), chr(34) * 2)}"' for length, text in longest)
        else:
            body = report
        target.write_text(body, encoding="utf-8")
        saved.append(str(target))
    return RunResult(
        message=ctx.M(
            f"{over} مسیر بلندتر از {limit} نویسه پیدا شد (از {scanned} مورد).",
            f"{over} path(s) longer than {limit} characters (of {scanned} entries)."),
        details=report[:8000], outputs=saved)
