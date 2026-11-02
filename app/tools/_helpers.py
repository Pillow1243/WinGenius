"""Shared helpers for tool implementations (import-safe, no heavy deps at import)."""
from __future__ import annotations

import importlib
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional

from ..core.spec import RunContext, ToolError
from ..core.utils import ensure_dir, unique_path

IS_WINDOWS = sys.platform.startswith("win")


def require(module: str, pip_name: Optional[str] = None, ctx: Optional[RunContext] = None) -> Any:
    """Import an optional dependency or raise a friendly ToolError."""
    try:
        return importlib.import_module(module)
    except Exception as exc:  # ImportError or a broken install
        pip_name = pip_name or module
        msg_fa = (
            f"کتابخانه‌ی «{pip_name}» در دسترس نیست. برای نصب این دستور را در ترمینال اجرا کنید:\n"
            f"pip install {pip_name}\n\nجزئیات: {exc}"
        )
        msg_en = (
            f"The '{pip_name}' package is not available. Install it with:\n"
            f"pip install {pip_name}\n\nDetails: {exc}"
        )
        raise ToolError(ctx.M(msg_fa, msg_en) if ctx else msg_fa) from exc


def need_windows(ctx: RunContext) -> None:
    if not IS_WINDOWS:
        raise ToolError(ctx.M(
            "این ابزار فقط روی ویندوز در دسترس است.",
            "This tool is only available on Windows.",
        ))


def as_path_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    text = str(value)
    if "|" in text:
        return [p for p in text.split("|") if p.strip()]
    return [text] if text.strip() else []


def first_file(values: dict, key: str, ctx: RunContext) -> Path:
    files = as_path_list(values.get(key))
    if not files:
        raise ToolError(ctx.M("لطفاً یک فایل انتخاب کنید.", "Please choose a file."))
    path = Path(files[0])
    if not path.exists():
        raise ToolError(ctx.M(f"فایل پیدا نشد: {path}", f"File not found: {path}"))
    if path.is_dir():
        raise ToolError(ctx.M(f"این یک پوشه است: {path}", f"This is a folder: {path}"))
    return path


def reject_disk_root(path: Path, ctx: RunContext) -> Path:
    """Refuse to scan an entire drive by accident (it takes minutes and finds junk)."""
    try:
        resolved = Path(path).resolve()
        is_root = resolved.parent == resolved
    except OSError:
        resolved, is_root = path, False
    if is_root:
        raise ToolError(ctx.M(
            f"این مسیر ریشه‌ی دیسک است: {path}\nیک پوشه‌ی مشخص (مثلاً Downloads یا Documents) انتخاب کنید.",
            f"That is a drive root: {path}\nPick a real folder such as Downloads or Documents."))
    return path


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-_.]{0,251}[A-Za-z0-9])?$")


def validate_host(host: str, ctx: RunContext) -> str:
    """Reject hostnames the socket layer cannot even encode (e.g. "../..")."""
    text = (host or "").strip().strip("[]")
    if not text:
        raise ToolError(ctx.M("آدرس را وارد کنید.", "Enter a host."))
    if not HOST_RE.match(text) or ".." in text:
        raise ToolError(ctx.M(f"آدرس نامعتبر است: {host}", f"Invalid host: {host}"))
    return text


def read_text_file(path: Path | str, ctx: RunContext, encoding: str = "utf-8") -> str:
    """Read a text file, turning every OS-level failure into a friendly message."""
    target = Path(str(path))
    if not target.exists():
        raise ToolError(ctx.M(f"فایل پیدا نشد: {target}", f"File not found: {target}"))
    if target.is_dir():
        raise ToolError(ctx.M(f"این یک پوشه است، نه فایل: {target}",
                              f"That is a folder, not a file: {target}"))
    try:
        return target.read_text(encoding=encoding, errors="replace")
    except PermissionError:
        raise ToolError(ctx.M(f"اجازه‌ی خواندن این فایل را ندارید: {target}",
                              f"No permission to read: {target}"))
    except OSError as exc:
        raise ToolError(ctx.M(f"فایل خوانده نشد: {target} ({exc.strerror or exc})",
                              f"Could not read {target} ({exc.strerror or exc})"))


def text_or_file(values: dict, ctx: RunContext, text_key: str = "text",
                 file_key: str = "file", required: bool = True) -> str:
    """Return the pasted text, or the contents of the chosen file."""
    text = str(values.get(text_key) or "")
    if text.strip():
        return text
    raw_path = str(values.get(file_key) or "").strip()
    if raw_path:
        return read_text_file(raw_path, ctx)
    if required:
        raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
    return ""


def files_or_raise(values: dict, key: str, ctx: RunContext) -> List[Path]:
    files = [Path(p) for p in as_path_list(values.get(key))]
    if not files:
        raise ToolError(ctx.M("لطفاً حداقل یک فایل انتخاب کنید.", "Please choose at least one file."))
    missing = [p for p in files if not p.exists()]
    if missing:
        raise ToolError(ctx.M(
            "این فایل‌ها پیدا نشدند:\n" + "\n".join(str(m) for m in missing),
            "These files were not found:\n" + "\n".join(str(m) for m in missing),
        ))
    return files


def out_dir(ctx: RunContext, subfolder: str = "") -> Path:
    base = Path(ctx.output_dir) if ctx.output_dir else Path.home() / "WinGenius"
    if subfolder:
        base = base / subfolder
    return ensure_dir(base)


def save_target(ctx: RunContext, requested: str, default_name: str) -> Path:
    """Resolve a 'save as' field, falling back to the output folder."""
    requested = (requested or "").strip()
    if requested:
        path = Path(requested)
        if path.is_dir():
            return unique_path(path / default_name)
        if path.suffix == "":
            return unique_path(path / default_name)
        ensure_dir(path.parent)
        return path
    return unique_path(out_dir(ctx) / default_name)


def replace_suffix(path: Path, suffix: str) -> str:
    return f"{path.stem}{suffix}"


def zip_available() -> bool:
    return shutil.which("7z") is not None


def has_ffmpeg() -> Optional[str]:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def has_ffprobe() -> Optional[str]:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    ffmpeg = has_ffmpeg()
    if ffmpeg:
        candidate = Path(ffmpeg).with_name(Path(ffmpeg).stem.replace("ffmpeg", "ffprobe"))
        for name in (candidate, Path(ffmpeg).parent / "ffprobe.exe", Path(ffmpeg).parent / "ffprobe"):
            if Path(name).exists():
                return str(name)
    return None


def fmt_out_message(ctx: RunContext, count: int, where: str) -> str:
    if count == 1:
        return ctx.M(f"انجام شد. ۱ فایل ساخته شد:\n{where}", f"Done. 1 file created:\n{where}")
    return ctx.M(
        f"انجام شد. {count} فایل ساخته شد در:\n{where}",
        f"Done. {count} files created in:\n{where}",
    )


def open_folder(path: Path) -> None:
    from ..core.launcher import open_folder as launch_folder

    try:
        launch_folder(path)
    except Exception:
        pass


def iter_with_progress(ctx: RunContext, items: Iterable, label: str = "") -> Iterable:
    items = list(items)
    total = len(items)
    for index, item in enumerate(items, start=1):
        ctx.progress(index, total, label.format(index=index, total=total) if label else "")
        yield item
