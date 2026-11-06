"""Utilities shared by tools: sizes, paths, subprocess, hashing, ranges."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, List, Sequence, Tuple

IS_WINDOWS = sys.platform.startswith("win")


# --------------------------------------------------------------------- numbers
def human_size(num: float) -> str:
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num)} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds or 0))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ------------------------------------------------------------------ filesystem
def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def unique_path(path: str | Path) -> Path:
    """Return a non-existing path by appending -1, -2 … if needed."""
    p = Path(path)
    if not p.exists():
        return p
    stem, suffix, parent = p.stem, p.suffix, p.parent
    index = 1
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def safe_filename(name: str, fallback: str = "file") -> str:
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    return name or fallback


def native(path: str | Path) -> str:
    """Long-path-safe string for Windows APIs."""
    p = str(Path(path))
    if IS_WINDOWS and len(p) > 250 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def open_path(path: str | Path) -> None:
    """Open a file/folder with the OS default handler (never in-process)."""
    p = str(path)
    if not p:
        return
    from .launcher import open_target

    open_target(p)


def reveal_path(path: str | Path) -> None:
    """Open the containing folder and select the file when possible."""
    p = Path(path)
    if not p.exists():
        open_path(p.parent)
        return
    from .launcher import reveal

    reveal(native(p) if IS_WINDOWS else p)


def list_files(folder: str | Path, recursive: bool = False, pattern: str = "*") -> List[Path]:
    """Files under `folder`, skipping anything the OS refuses to show us.

    A plain `rglob` raises PermissionError as soon as it meets a system folder, and
    that used to bubble up as a crash. Walking with `os.walk(..., onerror=...)`
    keeps the useful files and quietly ignores the rest.
    """
    root = Path(str(folder))
    try:
        if not root.is_dir():
            return []
    except OSError:
        return []
    found: List[Path] = []
    if recursive:
        for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda _exc: None):
            for name in filenames:
                if fnmatch.fnmatch(name, pattern):
                    found.append(Path(dirpath) / name)
    else:
        try:
            entries = list(os.scandir(root))
        except OSError:
            return []
        for entry in entries:
            try:
                if fnmatch.fnmatch(entry.name, pattern) and safe_is_file(Path(entry.path)):
                    found.append(Path(entry.path))
            except OSError:
                continue
    return sorted(found, key=lambda p: p.name.lower())


def safe_is_file(path: Path) -> bool:
    """`path.is_file()` that also swallows EPERM (e.g. under /proc on Linux)."""
    try:
        return path.is_file()
    except OSError:
        return False


def walk_files(folder: str | Path, on_error=None) -> List[Path]:
    """Every file under `folder`, tolerating unreadable or vanished entries."""
    root = Path(str(folder))
    try:
        if not root.is_dir():
            return []
    except OSError:
        return []
    found: List[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, onerror=on_error or (lambda _exc: None)):
        for name in filenames:
            candidate = Path(dirpath) / name
            if safe_is_file(candidate):
                found.append(candidate)
    return found


def natural_key(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text))]


# ------------------------------------------------------------------ subprocess
def run_cmd(
    args: Sequence[str] | str,
    timeout: float = 60,
    cwd: str | Path | None = None,
    shell: bool = False,
) -> Tuple[int, str, str]:
    """Run a command quietly (no flashing console window on Windows)."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            shell=shell,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:  # pragma: no cover
        return 1, "", str(exc)


# --------------------------------------------------------------------- hashing
HASH_ALGOS = ("md5", "sha1", "sha256", "sha512")


def hash_file(path: str | Path, algo: str = "sha256", on_chunk=None) -> str:
    algo = algo.lower()
    if algo not in HASH_ALGOS:
        raise ValueError(f"unsupported hash: {algo}")
    digest = hashlib.new(algo)
    total = os.path.getsize(path) or 1
    done = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            if on_chunk:
                on_chunk(done, total)
    return digest.hexdigest()


# ----------------------------------------------------------------- page ranges
class RangeError(ValueError):
    """A page/row range that could not be parsed.

    Carries the offending token so callers can build one clear, translated
    message instead of leaking "bad number: …" into the UI.
    """

    def __init__(self, message: str, part: str = "") -> None:
        super().__init__(message)
        self.part = part or message


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_digits(text: Any) -> str:
    """Western digits → Persian digits (۰-۹).  Leaves everything else alone."""
    return str(text).translate(PERSIAN_DIGITS)


def ui_number(value: Any, lang: str = "fa") -> str:
    """Format a number for display: Persian digits in Persian UI when enabled.

    Paths and file names never go through this — only counters, sizes and dates.
    """
    text = str(value)
    if lang != "en":
        try:
            from .settings import settings

            if settings.get("ui/persian_digits"):
                return fa_digits(text)
        except Exception:                              # settings unavailable
            return fa_digits(text)
    return text


def parse_ranges(text: str, max_index: int, one_based: bool = True) -> List[int]:
    """Parse "1-3, 5, 8-" into a zero-based index list (clipped to max_index)."""
    text = (text or "").strip()
    if not text:
        return list(range(max_index))
    offset = 1 if one_based else 0
    out: List[int] = []
    for part in re.split(r"[,\s;]+", text):
        if not part:
            continue
        if "-" in part:
            start_s, _, end_s = part.partition("-")
            try:
                start = int(start_s) if start_s else (1 if one_based else 0)
                end = int(end_s) if end_s else max_index - 1 + offset
            except ValueError:
                raise RangeError(f"bad range: {part}", part) from None
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise RangeError(f"bad number: {part}", part) from None
        if start > end:
            start, end = end, start
        for value in range(start, end + 1):
            index = value - offset
            if 0 <= index < max_index and index not in out:
                out.append(index)
    return out


# ----------------------------------------------------------------------- misc
def deps_available() -> dict:
    """Report which optional libraries are importable (used by Settings/About)."""
    import importlib.util

    checks = {
        "PySide6": "PySide6",
        "pypdf": "pypdf",
        "PyMuPDF": "pymupdf",
        "Pillow": "PIL",
        "reportlab": "reportlab",
        "python-docx": "docx",
        "openpyxl": "openpyxl",
        "qrcode": "qrcode",
        "cryptography": "cryptography",
        "psutil": "psutil",
        "requests": "requests",
        "mss": "mss",
        "markdown": "markdown",
        "imageio-ffmpeg": "imageio_ffmpeg",
    }
    result = {}
    for label, module in checks.items():
        try:
            result[label] = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            result[label] = False
    return result


class CancellableProcess:
    """Interrupt a blocked pipe read when a cooperative cancellation is requested.

    Owns no process resources: callers must still wait for the child and close pipes.
    """

    def __init__(self, proc, ctx):
        import threading

        self.proc = proc
        self.ctx = ctx
        self.stopped = threading.Event()
        self.killed = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="wg-process-cancel", daemon=True)

    def _watch(self):
        while not self.stopped.wait(0.05):
            if self.ctx.cancelled and self.proc.poll() is None:
                try:
                    self.proc.kill()
                    self.killed.set()
                except ProcessLookupError:
                    pass
                return
            if self.proc.poll() is not None:
                return

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stopped.set()
        self.thread.join()

    def check_cancelled(self):
        if self.killed.is_set():
            self.ctx.check_cancel()
