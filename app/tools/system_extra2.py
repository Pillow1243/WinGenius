"""Deeper system insight: memory hogs, port owners, startup impact, updates, printers."""
from __future__ import annotations

import os
import platform
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import (ensure_dir, human_duration, human_size, run_cmd, unique_path,
                          walk_files)
from ._helpers import need_windows, out_dir

IS_WINDOWS = os.name == "nt"


def _psutil(ctx: RunContext):
    try:
        import psutil

        return psutil
    except Exception:
        raise ToolError(ctx.M("کتابخانه‌ی psutil نصب نیست: pip install psutil",
                              "psutil is missing: pip install psutil"))


def _powershell(script: str, timeout: float = 45) -> Tuple[int, str]:
    code, out, err = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)
    return code, (out or err or "").strip()


# ------------------------------------------------------------------ ram hogs
@tool(
    id="ram_hogs", category="system", name="پرمصرف‌های حافظه", name_en="Memory hogs",
    desc="پردازه‌هایی که بیشترین RAM و CPU را می‌گیرند (با مسیر فایل) را نشان می‌دهد.",
    desc_en="Lists the processes eating the most RAM and CPU, with their executable paths.",
    icon="chart", order=50, keywords=("ram", "memory", "cpu", "حافظه", "رم", "کند", "پردازه"),
    fields=[Field("count", K_INT, "تعداد", "How many", default=15, minimum=5, maximum=60),
            Field("sort", K_CHOICE, "مرتب بر اساس", "Sort by", default="memory",
                  choices=[("memory", "حافظه", "Memory"), ("cpu", "پردازنده", "CPU")])],
)
def ram_hogs(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = _psutil(ctx)
    rows: List[Tuple[str, float, float, str]] = []
    for process in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "exe"]):
        try:
            info = process.info
            memory = (info["memory_info"].rss / (1024 * 1024)) if info.get("memory_info") else 0.0
            rows.append((info.get("name") or "?", memory, process.cpu_percent(interval=None),
                         info.get("exe") or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    key = (lambda row: -row[1]) if str(v.get("sort") or "memory") == "memory" else (lambda row: -row[2])
    top = sorted(rows, key=key)[: int(v.get("count") or 15)]
    total = psutil.virtual_memory()
    lines = [f"{'process':<28} {'RAM MB':>9} {'CPU %':>7}  path"]
    for name, memory, cpu, exe in top:
        lines.append(f"{name[:28]:<28} {memory:>9.1f} {cpu:>7.1f}  {exe[:70]}")
    lines.append("")
    lines.append(f"RAM: {human_size(total.used)} / {human_size(total.total)} "
                 f"({total.percent}%) · free {human_size(total.available)}")
    return RunResult(message=ctx.M(f"{len(top)} پردازه‌ی پرمصرف · RAM {total.percent}% آزاد",
                                   f"{len(top)} heavy processes · RAM at {total.percent}%"),
                     details="\n".join(lines))


# ------------------------------------------------------------ port ownership
@tool(
    id="open_ports_owner", category="system", name="پورت‌ها و برنامه‌ی صاحبشان", name_en="Port owners",
    desc="پورت‌های باز سیستم را با برنامه‌ای که آن‌ها را باز کرده نشان می‌دهد.",
    desc_en="Shows the open ports on this machine and which program owns each one.",
    icon="network", order=51, keywords=("port", "listen", "netstat", "پورت", "برنامه", "اتصال"),
    fields=[Field("listening_only", K_BOOL, "فقط پورت‌های شنود", "Listening only", default=True)],
)
def open_ports_owner(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = _psutil(ctx)
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:
        raise ToolError(ctx.M(f"دسترسی به اطلاعات اتصال‌ها ممکن نشد: {exc}",
                              f"Cannot read connections: {exc}"))
    listening_only = bool(v.get("listening_only", True))
    names: Dict[int, str] = {}
    rows: List[str] = []
    seen = set()
    for connection in connections:
        if listening_only and connection.status != psutil.CONN_LISTEN:
            continue
        address = connection.laddr
        endpoint = f"{address.ip}:{address.port}" if address else "-"
        remote = f"{connection.raddr.ip}:{connection.raddr.port}" if connection.raddr else ""
        key = (endpoint, remote, connection.pid)
        if key in seen:
            continue
        seen.add(key)
        pid = connection.pid or 0
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except Exception:
                names[pid] = "?"
        rows.append(f"{endpoint:<24} {remote:<24} {names.get(pid, '-'):<22} pid {pid}")
    rows.sort()
    if not rows:
        raise ToolError(ctx.M("پورت بازی پیدا نشد.", "No open ports found."))
    return RunResult(message=ctx.M(f"{len(rows)} پورت باز", f"{len(rows)} open ports"),
                     details="\n".join(rows[:300]))


# ------------------------------------------------------------- startup impact
@tool(
    id="startup_impact", category="system", name="اثر برنامه‌های استارتاپ", name_en="Startup impact",
    desc="برنامه‌های هنگام روشن‌شدن ویندوز را با حجم فایل و تأخیر تخمینی نشان می‌دهد تا کندی بوت را پیدا کنید.",
    desc_en="Lists Windows startup entries with file size and an estimated delay, so you can spot slow boots.",
    icon="power", order=52, windows_only=True,
    keywords=("startup", "boot", "slow", "استارتاپ", "بوت", "کندی"),
    fields=[Field("count", K_INT, "تعداد", "How many", default=25, minimum=5, maximum=200)],
)
def startup_impact(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    import winreg

    runs = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM32"),
    ]
    entries: List[Tuple[str, str, str, int]] = []
    for hive, path, hive_name in runs:
        try:
            with winreg.OpenKey(hive, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    size = 0
                    candidate = str(value).strip('"').split('"')[0].split(" /")[0]
                    if Path(candidate).exists():
                        try:
                            size = Path(candidate).stat().st_size
                        except OSError:
                            size = 0
                    entries.append((name, str(value)[:90], hive_name, size))
        except OSError:
            continue
    if not entries:
        return RunResult(message=ctx.M("موردی در استارتاپ نبود.", "No startup entries."), details="—")
    entries.sort(key=lambda item: -item[3])
    lines = [f"{'name':<26} {'MB':>7} {'hive':<7} command"]
    for name, command, hive_name, size in entries[: int(v.get("count") or 25)]:
        delay = 0.2 + (size / (1024 * 1024)) * 0.4
        lines.append(f"{name[:26]:<26} {size / 1048576:>7.1f} {hive_name:<7} "
                     f"{command[:60]}  (تأخیر تخمینی {delay:.1f}s)")
    total = sum(item[3] for item in entries)
    return RunResult(message=ctx.M(f"{len(entries)} برنامه در استارتاپ · {human_size(total)}",
                                   f"{len(entries)} startup entries · {human_size(total)}"),
                     details="\n".join(lines))


# ------------------------------------------------------------- windows update
@tool(
    id="windows_update_status", category="windows", name="وضعیت به‌روزرسانی ویندوز", name_en="Windows update status",
    desc="آخرین بسته‌های به‌روزرسانی نصب‌شده و اینکه ویندوز چند وقت است آپدیت نشده را می‌گوید.",
    desc_en="Shows the most recent installed updates and how long Windows has gone unpatched.",
    icon="refresh", order=53, windows_only=True,
    keywords=("update", "hotfix", "patch", "KB", "به‌روزرسانی", "آپدیت", "وصله"),
)
def windows_update_status(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    code, output = _powershell(
        "Get-HotFix | Sort-Object InstalledOn -Descending | "
        "Select-Object -First 20 HotFixID, Description, InstalledOn | Format-Table -AutoSize | Out-String -Width 200")
    if code != 0 or not output:
        raise ToolError(ctx.M("خواندن فهرست به‌روزرسانی‌ها ممکن نشد.", "Cannot read the update list."))
    newest = ""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].upper().startswith("KB"):
            newest = parts[-1]
            break
    advice = ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            when = datetime.strptime(newest, fmt)
            age = datetime.now() - when
            advice = (f"\nآخرین به‌روزرسانی {age.days} روز پیش بود." if age.days > 45
                      else f"\nآخرین به‌روزرسانی {age.days} روز پیش — وضعیت خوب است.")
            break
        except ValueError:
            continue
    return RunResult(message=ctx.M("فهرست به‌روزرسانی‌ها آماده است.", "Update list ready."),
                     details=output + advice)


# -------------------------------------------------------------------- printers
@tool(
    id="printer_list", category="windows", name="پرینترها", name_en="Printers",
    desc="پرینترهای نصب‌شده، پیش‌فرض و وضعیت آن‌ها را نشان می‌دهد.",
    desc_en="Lists installed printers, the default one and their status.",
    icon="print", order=54, windows_only=True,
    keywords=("printer", "print", "پرینتر", "چاپگر", "چاپ"),
)
def printer_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    code, output = _powershell(
        "Get-Printer | Select-Object Name, DriverName, PortName, PrinterStatus | "
        "Format-Table -AutoSize | Out-String -Width 200")
    if code != 0 or not output:
        code, output = _powershell("wmic printer get Name,Default,PortName /format:list")
    if code != 0 or not output:
        raise ToolError(ctx.M("پرینتری پیدا نشد.", "No printers found."))
    return RunResult(message=ctx.M("فهرست پرینترها آماده است.", "Printer list ready."), details=output)


# ----------------------------------------------------------------------- fonts
@tool(
    id="fonts_list", category="system", name="فونت‌های نصب‌شده", name_en="Installed fonts",
    desc="فونت‌های نصب‌شده روی سیستم را فهرست می‌کند (برای بررسی پشتیبانی فارسی).",
    desc_en="Lists installed fonts and checks which ones look Persian-capable.",
    icon="text", order=55, keywords=("font", "typeface", "فونت", "قلم", "فارسی"),
)
def fonts_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folders = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"] if IS_WINDOWS else []
    folders += [Path.home() / ".fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    families: List[str] = []
    for folder in folders:
        if not folder.exists():
            continue
        for path in walk_files(folder):
            if path.suffix.lower() in (".ttf", ".otf", ".ttc", ".woff", ".woff2"):
                families.append(path.stem)
    if not families:
        raise ToolError(ctx.M("فونتی پیدا نشد.", "No fonts found."))
    unique = sorted(set(families), key=str.lower)
    persian_hints = [name for name in unique
                     if any(hint in name.lower() for hint in
                            ("vazir", "iran", "nazanin", "tahoma", "segoe", "noto", "dejavu",
                             "arial", "kalameh", "shabnam", "sahel", "yekan"))]
    details = (f"{len(unique)} فونت\n\n"
               + "\n".join(unique[:200])
               + ("\n\nمناسب فارسی: " + ", ".join(persian_hints[:20]) if persian_hints else ""))
    return RunResult(message=ctx.M(f"{len(unique)} فونت نصب است", f"{len(unique)} fonts installed"),
                     details=details)


# --------------------------------------------------------------- failed services
@tool(
    id="failed_services", category="system", name="سرویس‌های مشکل‌دار", name_en="Problem services",
    desc="سرویس‌های خودکار که متوقف شده‌اند یا در حال اجرا نیستند را نشان می‌دهد.",
    desc_en="Shows automatic services that are stopped or not running properly.",
    icon="monitor", order=56, keywords=("service", "stopped", "سرویس", "متوقف", "خراب"),
)
def failed_services(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if IS_WINDOWS:
        need_windows(ctx)
        code, output = _powershell(
            "Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and $_.Status -ne 'Running'} | "
            "Select-Object Name, DisplayName, Status | Format-Table -AutoSize | Out-String -Width 200")
        if code != 0:
            raise ToolError(ctx.M("خواندن سرویس‌ها ممکن نشد (شاید نیاز به دسترسی مدیر دارد).",
                                  "Cannot read services (admin rights may be required)."))
        return RunResult(message=ctx.M("سرویس‌های متوقف‌شده آماده است.", "Stopped services listed."),
                         details=output or "—")
    psutil = _psutil(ctx)
    running = sum(1 for _ in psutil.process_iter())
    return RunResult(message=ctx.M(f"{running} پردازه در حال اجراست (فهرست سرویس‌ها مخصوص ویندوز است).",
                                   f"{running} processes running (service list is Windows-only)."),
                     details=f"processes: {running}")


# -------------------------------------------------------------------- time info
@tool(
    id="time_info", category="system", name="زمان، منطقه و آپ‌تایم", name_en="Time, timezone & uptime",
    desc="زمان دقیق سیستم، منطقه‌ی زمانی، زمان روشن‌شدن و مدت آپ‌تایم را می‌گوید.",
    desc_en="Shows exact system time, timezone, boot time and uptime.",
    icon="clock", order=57, keywords=("time", "timezone", "uptime", "زمان", "آپ‌تایم", "روشن"),
)
def time_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    now = datetime.now()
    boot = datetime.fromtimestamp(time.time() - time.monotonic()) if hasattr(time, "monotonic") else now
    try:
        import psutil

        boot = datetime.fromtimestamp(psutil.boot_time())
    except Exception:
        pass
    uptime = now - boot
    offset = -time.timezone / 3600 if not time.daylight else -time.altzone / 3600
    details = (f"اکنون: {now:%Y-%m-%d %H:%M:%S}\n"
               f"منطقه‌ی زمانی: {time.tzname[0]} (UTC{offset:+.1f})\n"
               f"روشن‌شدن سیستم: {boot:%Y-%m-%d %H:%M}\n"
               f"آپ‌تایم: {human_duration(uptime.total_seconds())}\n"
               f"سیستم: {platform.system()} {platform.release()} ({platform.machine()})\n"
               f"پایتون: {platform.python_version()}")
    message = (f"آپ‌تایم: {human_duration(uptime.total_seconds())}" if ctx.lang != "en"
               else f"Uptime: {human_duration(uptime.total_seconds())}")
    return RunResult(message=message, details=details)


# ------------------------------------------------------------------ screen info
@tool(
    id="screen_info", category="system", name="اطلاعات نمایشگر", name_en="Screen information",
    desc="تعداد نمایشگرها، رزولوشن، DPI و مقیاس نمایش را نشان می‌دهد.",
    desc_en="Shows monitor count, resolutions, DPI and display scaling.",
    icon="monitor", order=58, keywords=("screen", "monitor", "resolution", "dpi", "نمایشگر", "رزولوشن"),
)
def screen_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if IS_WINDOWS:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        count = user32.GetSystemMetrics(80)
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
        details = (f"نمایشگرها: {count}\nرزولوشن اصلی: {width}×{height}\n"
                   f"DPI: {dpi} (مقیاس {dpi / 96 * 100:.0f}%)")
        return RunResult(message=f"{width}×{height} · {count} نمایشگر", details=details)
    size = shutil.get_terminal_size((0, 0))
    details = (f"سیستم: {platform.system()} {platform.release()}\n"
               f"میزبان: {platform.node()}\n"
               f"ترمینال: {size.columns}×{size.lines}\n"
               "(اطلاعات دقیق نمایشگر فقط روی ویندوز خوانده می‌شود.)")
    return RunResult(message=platform.platform()[:40], details=details)


# -------------------------------------------------------------------- temp size
@tool(
    id="temp_size", category="system", name="حجم پوشه‌های موقت", name_en="Temp folder sizes",
    desc="پوشه‌های موقت ویندوز و کاربر را اندازه می‌گیرد تا بدانید چقدر فضا آزاد می‌شود.",
    desc_en="Measures the Windows and user temp folders so you know how much space you can free.",
    icon="trash", order=59, keywords=("temp", "size", "space", "موقت", "حجم", "فضا"),
)
def temp_size(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    candidates: List[Path] = []
    for env in ("TEMP", "TMP"):
        value = os.environ.get(env)
        if value:
            candidates.append(Path(value))
    if IS_WINDOWS:
        candidates.append(Path(os.environ.get("WINDIR", "C:/Windows")) / "Temp")
        candidates.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "INetCache")
    else:
        candidates.append(Path("/tmp"))
    seen = set()
    total = 0
    lines: List[str] = []
    for folder in candidates:
        if not folder or not folder.exists() or str(folder) in seen:
            continue
        seen.add(str(folder))
        size = 0
        count = 0
        try:
            for path in walk_files(folder):
                try:
                    size += path.stat().st_size
                    count += 1
                except OSError:
                    continue
        except OSError:
            continue
        total += size
        lines.append(f"{human_size(size):>10}  {count:>7} فایل  {folder}")
    if not lines:
        raise ToolError(ctx.M("پوشه‌ی موقتی پیدا نشد.", "No temp folder found."))
    lines.append("")
    lines.append(f"جمع: {human_size(total)}")
    return RunResult(message=ctx.M(f"پوشه‌های موقت: {human_size(total)}",
                                   f"Temp folders hold {human_size(total)}"),
                     details="\n".join(lines))


# ---------------------------------------------------------------- disk tree
@tool(
    id="disk_tree_usage", category="system", name="سنگین‌ترین پوشه‌ها", name_en="Heaviest folders",
    desc="کدام زیرپوشه‌ها بیشترین فضا را گرفته‌اند (تا دو سطح) — برای پیدا کردن منبع پر شدن دیسک.",
    desc_en="Ranks subfolders by size (two levels deep) so you can find what filled the disk.",
    icon="chart", order=60, keywords=("disk", "space", "folders", "دیسک", "فضا", "پوشه", "حجم"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("count", K_INT, "تعداد", "How many", default=15, minimum=3, maximum=60),
    ],
)
def disk_tree_usage(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    root = Path(str(v.get("folder") or "").strip())
    if not root.is_dir():
        raise ToolError(ctx.M("پوشه را انتخاب کنید.", "Choose a folder."))
    children = [child for child in root.iterdir() if child.is_dir()]
    results: List[Tuple[int, Path, int]] = []
    for index, child in enumerate(children, start=1):
        ctx.progress(index, len(children), child.name)
        size = 0
        files = 0
        try:
            for path in walk_files(child):
                try:
                    size += path.stat().st_size
                    files += 1
                except OSError:
                    continue
        except OSError:
            continue
        results.append((size, child, files))
    if not results:
        raise ToolError(ctx.M("زیرپوشه‌ای نبود.", "No subfolders."))
    results.sort(reverse=True, key=lambda item: item[0])
    top = results[: int(v.get("count") or 15)]
    total = sum(item[0] for item in results)
    lines = [f"{human_size(size):>10}  {files:>7} فایل  {path.name}"
             f"  ({size * 100 / max(1, total):.1f}%)" for size, path, files in top]
    return RunResult(message=ctx.M(f"{len(results)} زیرپوشه · {human_size(total)}",
                                   f"{len(results)} subfolders · {human_size(total)}"),
                     details="\n".join(lines))


# ------------------------------------------------------------- crash reporting
SENSITIVE_HINTS = ("token", "password", "secret", "cookie", "authorization", "api_key")


def _redact(text: str) -> str:
    """Strip the paths and tokens a stranger should never receive."""
    import re

    home = str(Path.home())
    text = text.replace(home, "~")
    text = re.sub(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+", r"C:\\Users\\<user>", text)
    for hint in SENSITIVE_HINTS:
        text = re.sub(rf"(?im)^(?P<lead>[^\n]*{hint}[^\n]*)$",
                      lambda m: re.sub(r"(=|:)\s*\S+", r"\1 <redacted>", m.group(0)), text)
    return text


@tool(
    id="crash_report", category="system", name="گزارش خطا برای پشتیبانی",
    name_en="Crash report bundle",
    desc=("یک فایل ZIP می‌سازد که نسخه‌ی برنامه، وضعیت وابستگی‌ها و انتهای لاگ را دارد — "
          "بدون مسیرهای شخصی و بدون رمز. برای فرستادن به پشتیبانی یا ضمیمه‌کردن به گزارش مشکل."),
    desc_en=("Builds a ZIP with the app version, dependency status and the tail of the log — "
             "with personal paths and tokens removed. Ready to attach to a bug report."),
    icon="bug", order=195,
    keywords=("crash report", "bug report", "گزارش خطا", "لاگ", "log bundle",
              "support", "پشتیبانی", "diagnostics"),
    fields=[
        Field("action", K_CHOICE, "کار", "Action", default="create",
              choices=[("create", "ساخت فایل ZIP", "Build the ZIP"),
                       ("preview", "فقط نشان بده", "Preview only")]),
        Field("lines", K_INT, "چند خط آخر لاگ", "Log lines to include", default=400,
              minimum=50, maximum=5000, step=50),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def crash_report(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import platform
    import zipfile
    from datetime import datetime

    from ..core import diagnostics
    from ..core.paths import APP_NAME, APP_VERSION, data_dir, logs_dir

    wanted = max(50, min(5000, int(v.get("lines") or 400)))
    report: List[str] = [
        f"{APP_NAME} {APP_VERSION}",
        f"generated: {datetime.now().isoformat(timespec='seconds')}",
        f"platform: {platform.platform()}",
        f"python: {platform.python_version()} ({platform.architecture()[0]})",
        "",
        "--- diagnostics ---",
        _redact(diagnostics.diagnostics_text(full=True)),
    ]
    log_files = sorted(logs_dir().glob("*.log*"))[-3:]
    for log_file in log_files:
        try:
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-wanted:]
        except OSError as exc:
            tail = [f"(could not read: {exc})"]
        report.extend(["", f"--- {log_file.name} (last {wanted} lines) ---", _redact("\n".join(tail))])
    body = "\n".join(report)

    if str(v.get("action") or "create") == "preview":
        return RunResult(
            message=ctx.M(f"بسته‌ی گزارش آماده است ({len(body)} نویسه، {len(log_files)} فایل لاگ).",
                          f"The report is ready ({len(body)} characters, {len(log_files)} log file(s))."),
            details=body[:4000],
        )

    target_dir = out_dir(ctx, v.get("out_dir"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle = unique_path(target_dir / f"wingenius-report-{stamp}.zip")
    manifest = "\n".join([
        f"version: {APP_VERSION}",
        f"data version: {diagnostics._data_version()}",
        f"data folder: {str(data_dir()).replace(str(Path.home()), '~')}",
        "contains: report.txt, settings-redacted.ini (if present), log tails",
    ])
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", body)
        archive.writestr("manifest.txt", manifest)
        settings_path = data_dir() / "settings.ini"
        if settings_path.exists():
            try:
                archive.writestr("settings-redacted.ini", _redact(settings_path.read_text(
                    encoding="utf-8", errors="replace")))
            except OSError:
                pass
    size = human_size(bundle.stat().st_size)
    return RunResult(
        message=ctx.M(f"گزارش ساخته شد · {size}", f"Report bundle created · {size}"),
        details=manifest,
        outputs=[str(bundle)],
        open_target=str(bundle.parent),
    )
