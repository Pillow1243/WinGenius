"""Windows system tools: info, cleanup, startup, processes, services, apps."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_INT, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_duration, human_size, run_cmd
from ..core import winapi
from ._helpers import IS_WINDOWS, need_windows, out_dir, require

RUN_KEYS = [
    (r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
    (r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
    (r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
]


def _winreg():
    return require("winreg", "pywin32", None)


# Shell metacharacters that must never reach a shell — the uninstall string is
# read from HKLM/HKCU and both hives are treated as untrusted input.
_SHELL_METACHARS = ("&", "|", "<", ">", "^")
_SHELL_STARTERS = ("cmd ", "cmd.exe", "cmd/", "powershell", "pwsh", "start ", "rundll32 ")


def _uninstall_argv(command: str) -> List[str]:
    """Convert a registry UninstallString into an argv list for shell=False.

    Follows the Windows CreateProcess quoting rules closely enough for real
    uninstallers (quoted exe path, MSI product codes, switches). Anything that
    looks like a shell construct (``&``, ``|``, ``cmd /c`` …) is refused
    fail-closed instead of being executed.
    """
    import shlex

    text = os.path.expandvars((command or "").strip())
    if not text:
        raise ValueError("empty uninstall command")
    if any(ch in text for ch in _SHELL_METACHARS):
        raise ValueError(f"shell metacharacter in uninstall command: {text!r}")
    lowered = text.lower()
    if lowered.startswith(_SHELL_STARTERS):
        raise ValueError(f"shell starter in uninstall command: {text!r}")
    if text.startswith('"'):
        end = text.find('"', 1)
        if end == -1:
            raise ValueError(f"unterminated quote in uninstall command: {text!r}")
        exe = text[1:end]
        rest = text[end + 1:]
    else:
        parts = text.split(None, 1)
        exe = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # Unquoted path with spaces ("C:\Program Files\App\un.exe" /S): Windows
        # resolves this by trying progressively longer prefixes ending in .exe.
        if " " in exe and not Path(exe).exists():
            tokens = text.split()
            for i in range(1, len(tokens)):
                candidate = " ".join(tokens[:i + 1])
                if candidate.lower().endswith(".exe") and Path(candidate).exists():
                    exe = candidate
                    rest = " ".join(tokens[i + 1:])
                    break
    argv = [exe]
    for token in shlex.split(rest, posix=False):
        token = token.strip()
        if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
            token = token[1:-1]
        if token:
            argv.append(token)
    return argv


def _launch_uninstaller(ctx: RunContext, app: Dict[str, str]) -> None:
    """Launch a registry uninstaller safely: argv only, never shell=True.

    The UninstallString is untrusted input (HKCU included), so it is parsed
    with the Windows quoting rules and anything resembling a shell construct
    is refused fail-closed.  The full command is surfaced to the user.
    """
    command = app.get("uninstall") or ""
    try:
        argv = _uninstall_argv(command)
    except ValueError as exc:
        raise ToolError(ctx.M(
            f"فرمان حذف این برنامه به‌صورت امن قابل‌اجرا نیست و مسدود شد:\n{command}\n({exc})",
            f"This app's uninstall command is not safe to run and was blocked:\n{command}\n({exc})",
        )) from exc
    log_line = f"uninstall {app.get('name', '?')}: {' '.join(argv)}"
    try:
        subprocess.Popen(argv, shell=False, close_fds=True)
    except OSError as exc:
        raise ToolError(str(exc))
    ctx.log(log_line)
    return argv


def _startup_folder(global_scope: bool = False) -> Path:
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    if global_scope:
        return Path(local) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


# ------------------------------------------------------------------ system info
@tool(
    id="system_info", category="system", name="اطلاعات سیستم", name_en="System info",
    desc="خلاصه‌ی کامل CPU، حافظه، دیسک‌ها، کارت گرافیک و نسخه‌ی ویندوز.",
    desc_en="Full summary of CPU, memory, disks, GPU and Windows version.",
    icon="monitor", order=10, keywords=("system info", "مشخصات سیستم", "سی پی یو", "رم", "گرافیک"),
    fields=[Field("show_os", K_BOOL, "نمایش جزئیات ویندوز", "Show OS details", default=True)],
)
def system_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    rows: List[str] = []
    rows.append(f"{'OS':<16}: {platform.system()} {platform.release()} ({platform.version()})")
    rows.append(f"{'Machine':<16}: {platform.machine()}  |  {ctx.M('کاربر', 'user')}: {os.environ.get('USERNAME') or os.environ.get('USER')}")
    rows.append(f"{'Python':<16}: {platform.python_version()}")
    try:
        freq = psutil.cpu_freq()
        rows.append(f"{'CPU':<16}: {platform.processor() or 'CPU'}  ({psutil.cpu_count(logical=True)} threads)")
        if freq:
            rows.append(f"{'CPU speed':<16}: {freq.current / 1000:.2f} GHz (max {freq.max / 1000:.2f} GHz)")
        rows.append(f"{'CPU usage':<16}: {psutil.cpu_percent(interval=0.4):.1f}%")
    except Exception:
        pass
    mem = psutil.virtual_memory()
    rows.append(f"{'RAM':<16}: {human_size(mem.used)} / {human_size(mem.total)} ({mem.percent:.0f}%)")
    try:
        swap = psutil.swap_memory()
        rows.append(f"{'Swap':<16}: {human_size(swap.used)} / {human_size(swap.total)}")
    except Exception:
        pass
    rows.append("")
    rows.append(ctx.M("دیسک‌ها:", "Disks:"))
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            rows.append(
                f"  {part.device:<5} {part.fstype:<6} "
                f"{human_size(usage.used)} / {human_size(usage.total)}  ({usage.percent:.0f}% "
                f"{ctx.M('پر', 'used')})"
            )
        except PermissionError:
            continue
    try:
        boot = psutil.boot_time()
        import datetime

        uptime = datetime.datetime.now().timestamp() - boot
        rows.append("")
        rows.append(f"{ctx.M('روشن بودن سیستم', 'Uptime'):<16}: {human_duration(uptime)}")
        rows.append(f"{ctx.M('زمان روشن شدن', 'Boot time'):<16}: {datetime.datetime.fromtimestamp(boot):%Y-%m-%d %H:%M}")
    except Exception:
        pass
    if IS_WINDOWS and v.get("show_os", True):
        code, out, _err = run_cmd(["wmic", "path", "win32_VideoController", "get", "name"], timeout=20)
        gpus = [line.strip() for line in out.splitlines()[1:] if line.strip()]
        if gpus:
            rows.append("")
            rows.append(ctx.M("کارت گرافیک:", "GPU:"))
            for gpu in gpus:
                rows.append(f"  {gpu}")
        code, out, _err = run_cmd(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).Caption + ' | build ' + (Get-CimInstance Win32_OperatingSystem).BuildNumber"],
            timeout=25,
        )
        caption = out.strip()
        if caption:
            rows.append("")
            rows.append(f"{ctx.M('نسخه ویندوز', 'Windows'):<16}: {caption}")
        admin = ctx.M("بله", "Yes") if winapi.is_admin() else ctx.M("خیر", "No")
        rows.append(f"{ctx.M('دسترسی مدیر', 'Administrator'):<16}: {admin}")
    return RunResult(message=ctx.M("اطلاعات سیستم", "System info"), details="\n".join(rows))


# ------------------------------------------------------------------ disk usage
@tool(
    id="disk_usage", category="system", name="وضعیت دیسک", name_en="Disk usage",
    desc="فضای آزاد و مصرف‌شده‌ی هر درایو را با نمودار متنی نشان می‌دهد.",
    desc_en="Free/used space per drive with a text chart.",
    icon="battery", order=20, keywords=("disk space", "فضای دیسک", "درایو", "هارد"),
    fields=[],
)
def disk_usage(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    rows: List[str] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        filled = int(round(usage.percent / 5))
        bar = "█" * filled + "░" * (20 - filled)
        rows.append(
            f"{part.device:<6} [{bar}] {usage.percent:5.1f}%   "
            f"{ctx.M('آزاد', 'free')}: {human_size(usage.free)}   "
            f"{ctx.M('کل', 'total')}: {human_size(usage.total)}   ({part.fstype})"
        )
    if not rows:
        raise ToolError(ctx.M("درایوی پیدا نشد.", "No drives found."))
    return RunResult(message=ctx.M("وضعیت دیسک‌ها", "Disk usage"), details="\n".join(rows))


# ------------------------------------------------------------------ temp clean
def _scan_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, names in os.walk(path, onerror=lambda _e: None):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _clear(path: Path) -> int:
    freed = 0
    if not path.exists():
        return 0
    for entry in path.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                freed += _scan_size(entry)
                shutil.rmtree(entry, ignore_errors=True)
            else:
                freed += entry.stat().st_size
                entry.unlink(missing_ok=True)
        except (OSError, PermissionError):
            continue
    return freed


@tool(
    id="temp_clean", category="system", name="پاک‌سازی موقت", name_en="Clean temp files",
    desc="فایل‌های موقت ویندوز و مرورگرها را پاک می‌کند و فضای آزادشده را گزارش می‌دهد.",
    desc_en="Delete Windows/browser temp files and report the freed space.",
    icon="trash", order=30, danger=True, admin=True,
    keywords=("clean temp", "پاکسازی", "تمیز کردن", "فضا", "cache"),
    fields=[
        Field("targets", K_CHOICE, "موارد پاک‌سازی", "What to clean", default="temp",
              choices=[("temp", "فایل‌های موقت ویندوز", "Windows temp files"),
                       ("browser", "کش مرورگرها", "Browser caches"),
                       ("all", "همه‌ی موارد بالا", "All of the above")]),
        Field("dry_run", K_BOOL, "فقط محاسبه (بدون حذف)", "Measure only (no delete)", default=True,
              help="پیش‌فرض روشن است؛ خاموش کردن یعنی فایل‌ها واقعاً پاک شوند.",
              help_en="On by default; turning it off means files are really deleted."),
    ],
)
def temp_clean(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import tempfile

    def env_path(name: str) -> Optional[Path]:
        value = os.environ.get(name, "").strip()
        return Path(value) if value else None

    targets: List[Path] = [Path(tempfile.gettempdir())]
    choice = str(v.get("targets") or "temp")
    if choice in ("temp", "all"):
        for env in ("TEMP", "TMP", "LOCALAPPDATA"):
            base = env_path(env)
            if base is None:
                continue
            targets.append(base if env != "LOCALAPPDATA" else base / "Temp")
        if IS_WINDOWS:
            targets += [
                Path(r"C:\Windows\Temp"),
                (env_path("LOCALAPPDATA") or Path.home()) / "Microsoft" / "Windows" / "Explorer",
                (env_path("LOCALAPPDATA") or Path.home()) / "Microsoft" / "Windows" / "INetCache",
            ]
    if choice in ("browser", "all"):
        local = env_path("LOCALAPPDATA")
        if local is not None:
            targets += [
                local / "Google" / "Chrome" / "User Data" / "Default" / "Cache",
                local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
                local / "Mozilla" / "Firefox" / "Profiles",
            ]
    seen = set()
    unique_targets = []
    for target in targets:
        key = str(target).lower()
        if key and key not in seen and target.exists():
            seen.add(key)
            unique_targets.append(target)
    if not unique_targets:
        raise ToolError(ctx.M("پوشه‌ی موقتی برای پاک‌سازی پیدا نشد.", "No temp folders found."))
    dry_run = bool(v.get("dry_run", True))
    total_freed = 0
    rows: List[str] = []
    for index, target in enumerate(unique_targets):
        ctx.progress(index, len(unique_targets), str(target))
        size = _scan_size(target)
        if dry_run:
            rows.append(f"{human_size(size):>10}  {target}")
        else:
            freed = _clear(target)
            total_freed += freed
            rows.append(f"{human_size(freed):>10}  {target}")
    planned = sum(_scan_size(p) for p in unique_targets)
    if dry_run:
        message = ctx.M(
            f"قابل آزادسازی: {human_size(planned)}.\nبرای حذف، تیک «فقط محاسبه» را بردارید.",
            f"Reclaimable: {human_size(planned)}.\nUncheck 'Measure only' to delete.",
        )
    else:
        message = ctx.M(f"{human_size(total_freed)} فضا آزاد شد.", f"{human_size(total_freed)} freed.")
    return RunResult(message=message, details="\n".join(rows))


# ------------------------------------------------------------------ startup
@tool(
    id="startup_manager", category="system", name="مدیریت استارتاپ", name_en="Startup manager",
    desc="برنامه‌هایی که با ویندوز بالا می‌آیند را فهرست و غیرفعال می‌کند.",
    desc_en="List and disable programs that start with Windows.",
    icon="power", order=40, admin=True,
    keywords=("startup", "استارتاپ", "برنامه‌های هنگام روشن شدن", "autorun"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست موارد", "List entries"),
                       ("disable", "غیرفعال کردن یک مورد", "Disable an entry"),
                       ("folder", "باز کردن پوشه‌ی استارتاپ", "Open startup folder")]),
        Field("name", K_TEXT, "نام دقیق مورد", "Exact entry name", visible_when=("action", "disable")),
    ],
)
def startup_manager(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    action = str(v.get("action") or "list")
    if action == "folder":
        folder = _startup_folder(False)
        if not folder.exists():
            raise ToolError(ctx.M("پوشه‌ی استارتاپ پیدا نشد.", "Startup folder not found."))
        from ..core.utils import open_path

        open_path(folder)
        return RunResult(message=ctx.M("پوشه باز شد.", "Folder opened."), open_target=str(folder))
    need_windows(ctx)
    winreg = _winreg()
    entries: List[tuple[str, str, str, str]] = []   # (hive, key, name, command)
    for key_path, hive_name in RUN_KEYS:
        hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ) as key:
                index = 0
                while True:
                    try:
                        name, value, _type = winreg.EnumValue(key, index)
                        entries.append((hive_name, key_path, name, str(value)))
                        index += 1
                    except OSError:
                        break
        except OSError:
            continue
    startup = _startup_folder(False)
    if startup.exists():
        for item in startup.iterdir():
            entries.append(("StartupFolder", str(startup), item.name, str(item)))
    if action == "disable":
        wanted = str(v.get("name") or "").strip()
        if not wanted:
            raise ToolError(ctx.M("نام مورد را وارد کنید.", "Enter the entry name."))
        removed = False
        for hive_name, key_path, name, command in entries:
            if name.lower() != wanted.lower():
                continue
            if hive_name == "StartupFolder":
                try:
                    path = Path(command)
                    backup = out_dir(ctx) / "startup_backup"
                    backup.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(backup / path.name))
                    removed = True
                except OSError as exc:
                    raise ToolError(str(exc))
            else:
                hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
                try:
                    with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                    removed = True
                except OSError as exc:
                    raise ToolError(ctx.M(
                        f"حذف ناموفق (شاید نیاز به دسترسی مدیر دارد): {exc}",
                        f"Delete failed (admin rights?): {exc}",
                    ))
        if not removed:
            raise ToolError(ctx.M("موردی با این نام پیدا نشد.", "No entry with that name."))
        return RunResult(message=ctx.M("مورد از استارتاپ حذف شد.", "Entry removed from startup."))
    if not entries:
        return RunResult(message=ctx.M("موردی در استارتاپ نیست.", "No startup entries."))
    rows = [f"[{hive}] {name}\n        {command}" for hive, _k, name, command in entries]
    return RunResult(
        message=ctx.M(f"{len(entries)} مورد استارتاپ پیدا شد. برای غیرفعال‌سازی، نام دقیق را در حالت «غیرفعال کردن» وارد کنید.",
                      f"{len(entries)} startup entries. To disable one, use 'Disable an entry' with its exact name."),
        details="\n".join(rows),
    )


# ------------------------------------------------------------------ processes
@tool(
    id="process_manager", category="system", name="مدیریت پردازه‌ها", name_en="Process manager",
    desc="پردازه‌های پرمصرف را نشان می‌دهد و امکان بستن آن‌ها را می‌دهد.",
    desc_en="Show resource-hungry processes and terminate them.",
    icon="speed", order=50, keywords=("process", "task manager", "پروسه", "بستن برنامه", "cpu"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="top",
              choices=[("top", "پرمصرف‌ترین‌ها", "Top consumers"),
                       ("search", "جست‌وجوی نام", "Search by name"),
                       ("kill", "بستن پردازه", "Kill process")]),
        Field("query", K_TEXT, "نام / PID", "Name / PID", visible_when=("action", ("search", "kill"))),
        Field("count", K_INT, "تعداد", "Count", default=15, minimum=5, maximum=100),
    ],
)
def process_manager(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    action = str(v.get("action") or "top")
    query = str(v.get("query") or "").strip()
    procs = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            mem = info.get("memory_info")
            procs.append({
                "pid": info["pid"],
                "name": info.get("name") or "?",
                "user": (info.get("username") or "")[-20:],
                "rss": getattr(mem, "rss", 0) or 0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if action == "kill":
        if not query:
            raise ToolError(ctx.M("نام یا PID را وارد کنید.", "Enter a name or PID."))
        killed = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = proc.info.get("name") or ""
                if query.isdigit() and proc.info["pid"] == int(query) or (not query.isdigit() and query.lower() in name.lower()):
                    proc.kill()
                    killed.append(f"{proc.info['pid']} {name}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not killed:
            raise ToolError(ctx.M("پردازه‌ای پیدا/بسته نشد (شاید نیاز به دسترسی مدیر دارد).",
                                  "No process killed (maybe needs admin rights)."))
        return RunResult(message=ctx.M(f"{len(killed)} پردازه بسته شد.", f"{len(killed)} process(es) killed."),
                         details="\n".join(killed))
    if action == "search":
        matches = [p for p in procs if query.lower() in p["name"].lower()]
        if not matches:
            raise ToolError(ctx.M("پیدا نشد.", "Not found."))
        rows = [f"{p['pid']:>7}  {p['name']:<32} {human_size(p['rss']):>10}  {p['user']}" for p in matches[:60]]
        return RunResult(message=ctx.M(f"{len(matches)} نتیجه", f"{len(matches)} match(es)"), details="\n".join(rows))
    top = sorted(procs, key=lambda p: -p["rss"])[: int(v.get("count") or 15)]
    rows = [f"{p['pid']:>7}  {p['name']:<32} {human_size(p['rss']):>10}  {p['user']}" for p in top]
    return RunResult(
        message=ctx.M(f"{len(procs)} پردازه فعال — پرمصرف‌ترین‌ها بر اساس رم:",
                      f"{len(procs)} active processes — top by RAM:"),
        details="\n".join(rows),
    )


# ------------------------------------------------------------------- services
@tool(
    id="services_manager", category="system", name="سرویس‌های ویندوز", name_en="Windows services",
    danger=True,
    desc="سرویس‌ها را فهرست، اجرا یا متوقف می‌کند.",
    desc_en="List, start or stop Windows services.",
    icon="gear", order=60, admin=True, windows_only=True,
    keywords=("service", "سرویس", "start stop", "services.msc"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست سرویس‌ها", "List services"),
                       ("start", "اجرای سرویس", "Start service"),
                       ("stop", "توقف سرویس", "Stop service"),
                       ("restart", "ری‌استارت سرویس", "Restart service")]),
        Field("name", K_TEXT, "نام سرویس", "Service name", visible_when=("action", ("start", "stop", "restart")),
              help="نام سرویس ویندوز (ستون Name در services.msc)، نه نام نمایشی.",
              help_en="The Windows service name (Name column in services.msc), not the display name."),
        Field("filter", K_TEXT, "فیلتر نام", "Name filter", default="", visible_when=("action", "list")),
    ],
)
def services_manager(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    psutil = require("psutil", "psutil", ctx)
    action = str(v.get("action") or "list")
    if action != "list":
        name = str(v.get("name") or "").strip()
        if not name:
            raise ToolError(ctx.M("نام سرویس را وارد کنید.", "Enter a service name."))
        if action in ("stop", "restart"):
            code, out, err = run_cmd(["net", "stop", name], timeout=60)
            if code != 0:
                raise ToolError(ctx.M(f"توقف ناموفق بود:\n{err or out}", f"Stop failed:\n{err or out}"))
        if action in ("start", "restart"):
            code, out, err = run_cmd(["net", "start", name], timeout=60)
            if code != 0:
                raise ToolError(ctx.M(f"اجرا ناموفق بود:\n{err or out}", f"Start failed:\n{err or out}"))
        return RunResult(message=ctx.M(f"سرویس «{name}» با موفقیت پردازش شد.", f"Service '{name}' handled."))
    flt = str(v.get("filter") or "").lower()
    rows: List[str] = []
    total = 0
    try:
        for service in psutil.win_service_iter():
            total += 1
            info = service.as_dict()
            name = info.get("name") or ""
            if flt and flt not in name.lower() and flt not in (info.get("display_name") or "").lower():
                continue
            rows.append(f"{info.get('status', '?'):<10} {name:<28} {info.get('display_name', '')[:48]}")
    except Exception as exc:
        raise ToolError(ctx.M(f"خواندن سرویس‌ها ناموفق بود: {exc}", f"Reading services failed: {exc}"))
    if not rows:
        return RunResult(message=ctx.M("سرویسی پیدا نشد.", "No services found."))
    return RunResult(message=ctx.M(f"{len(rows)} سرویس از {total}", f"{len(rows)} of {total} services"),
                     details="\n".join(rows[:400]))


# --------------------------------------------------------------- recycle bin
@tool(
    id="empty_recycle_bin", category="system", name="خالی کردن سطل بازیافت", name_en="Empty recycle bin",
    desc="سطل بازیافت ویندوز را کاملاً خالی می‌کند.",
    desc_en="Permanently empty the Windows recycle bin.",
    icon="trash", order=70, danger=True, windows_only=True,
    keywords=("recycle bin", "سطل بازیافت", "خالی کردن"),
    fields=[],
)
def empty_recycle_bin(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    try:
        winapi.empty_recycle_bin()
    except Exception as exc:
        raise ToolError(ctx.M(f"خالی کردن سطل ناموفق بود: {exc}", f"Emptying recycle bin failed: {exc}"))
    return RunResult(message=ctx.M("سطل بازیافت خالی شد.", "Recycle bin emptied."))


# ------------------------------------------------------------------ battery
@tool(
    id="battery_report", category="system", name="گزارش باتری", name_en="Battery report",
    desc="وضعیت باتری لپ‌تاپ و گزارش سلامت آن را می‌سازد.",
    desc_en="Show battery status and generate a health report.",
    icon="battery", order=80, windows_only=True, keywords=("battery", "باتری", "شارژ"),
    fields=[Field("make_report", K_BOOL, "ساخت گزارش HTML کامل", "Generate full HTML report", default=False)],
)
def battery_report(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    psutil = require("psutil", "psutil", ctx)
    rows: List[str] = []
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            rows.append(ctx.M("باتری‌ای پیدا نشد (کامپیوتر رومیزی).", "No battery found (desktop)."))
        else:
            rows.append(f"{ctx.M('درصد شارژ', 'Charge'):<16}: {battery.percent:.0f}%")
            rows.append(f"{ctx.M('وصل به برق', 'Plugged in'):<16}: "
                        f"{ctx.M('بله', 'Yes') if battery.power_plugged else ctx.M('خیر', 'No')}")
            if battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
                rows.append(f"{ctx.M('زمان باقی‌مانده', 'Time left'):<16}: {human_duration(battery.secsleft)}")
    except Exception as exc:
        rows.append(str(exc))
    outputs: List[str] = []
    if v.get("make_report"):
        dest = out_dir(ctx) / "battery-report.html"
        code, out, err = run_cmd(["powercfg", "/batteryreport", "/output", str(dest)], timeout=90)
        if dest.exists():
            outputs.append(str(dest))
            rows.append(ctx.M(f"گزارش: {dest}", f"Report: {dest}"))
        else:
            rows.append(err or out)
    return RunResult(message=ctx.M("وضعیت باتری", "Battery status"), details="\n".join(rows), outputs=outputs)


# --------------------------------------------------------------- restart explorer
@tool(
    id="restart_explorer", category="system", name="ری‌استارت اکسپلورر", name_en="Restart Explorer",
    desc="پوسته‌ی ویندوز (Taskbar/Explorer) را بدون ری‌استارت سیستم تازه می‌کند.",
    desc_en="Restart the Windows shell (taskbar/Explorer) without rebooting.",
    icon="window", order=90, windows_only=True, keywords=("restart explorer", "تسکبار", "شل"),
    fields=[],
)
def restart_explorer(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    run_cmd(["taskkill", "/f", "/im", "explorer.exe"], timeout=20)
    import time

    time.sleep(1.2)
    try:
        subprocess.Popen(["explorer.exe"], close_fds=True)
    except OSError as exc:
        raise ToolError(str(exc))
    return RunResult(message=ctx.M("اکسپلورر ری‌استارت شد.", "Explorer restarted."))


# ------------------------------------------------------------- installed apps
@tool(
    id="installed_apps", category="system", name="برنامه‌های نصب‌شده", name_en="Installed apps",
    desc="فهرست برنامه‌های نصب‌شده با حجم و نسخه؛ امکان حذف برنامه.",
    desc_en="List installed programs with size/version; optionally uninstall.",
    icon="list", order=100, windows_only=True, admin=False,
    keywords=("installed apps", "حذف برنامه", "لیست نرم افزار", "uninstall"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست", "List"), ("uninstall", "حذف برنامه", "Uninstall")]),
        Field("name", K_TEXT, "نام دقیق برنامه", "Exact app name", visible_when=("action", "uninstall")),
        Field("filter", K_TEXT, "فیلتر", "Filter", default="", visible_when=("action", "list")),
    ],
)
def installed_apps(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    winreg = _winreg()
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    apps: List[Dict[str, str]] = []
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, sub_name) as sub:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            try:
                                version = winreg.QueryValueEx(sub, "DisplayVersion")[0]
                            except OSError:
                                version = ""
                            try:
                                size = winreg.QueryValueEx(sub, "EstimatedSize")[0]
                            except OSError:
                                size = 0
                            try:
                                uninstall = winreg.QueryValueEx(sub, "UninstallString")[0]
                            except OSError:
                                uninstall = ""
                            apps.append({
                                "name": str(name), "version": str(version),
                                "size": str(int(size) * 1024 if size else 0),
                                "uninstall": str(uninstall),
                            })
                    except OSError:
                        continue
        except OSError:
            continue
    apps.sort(key=lambda a: a["name"].lower())
    if str(v.get("action")) == "uninstall":
        wanted = str(v.get("name") or "").strip().lower()
        match = next((a for a in apps if a["name"].lower() == wanted), None)
        if not match or not match["uninstall"]:
            raise ToolError(ctx.M("برنامه با این نام پیدا نشد یا حذف‌کننده ندارد.",
                                  "App not found or has no uninstaller."))
        argv = _launch_uninstaller(ctx, match)
        return RunResult(
            message=ctx.M(f"حذف‌کننده‌ی «{match['name']}» اجرا شد.", f"Uninstaller for '{match['name']}' launched."),
            details=ctx.M(
                f"برنامه: {match['name']} ({match.get('version') or '؟'})\n"
                f"مسیر فایل اجرایی: {argv[0]}\n"
                f"فرمان کامل: {' '.join(argv)}",
                f"App: {match['name']} ({match.get('version') or '?'})\n"
                f"Executable: {argv[0]}\n"
                f"Full command: {' '.join(argv)}",
            ),
        )
    flt = str(v.get("filter") or "").lower()
    rows = []
    for app in apps:
        if flt and flt not in app["name"].lower():
            continue
        size = human_size(int(app["size"])) if app["size"].isdigit() and int(app["size"]) else "?"
        rows.append(f"{app['name'][:56]:<56} {app['version'][:14]:<14} {size:>9}")
    if not rows:
        return RunResult(message=ctx.M("برنامه‌ای پیدا نشد.", "No apps found."))
    return RunResult(message=ctx.M(f"{len(rows)} برنامه نصب‌شده", f"{len(rows)} installed app(s)"),
                     details="\n".join(rows))
