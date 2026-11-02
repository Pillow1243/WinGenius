"""System intelligence: health score, disks, power plans, tasks, env vars, fonts."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_duration, human_size
from ._helpers import ensure_dir, files_or_raise, need_windows, require

IS_WINDOWS = sys.platform.startswith("win")


def _run(cmd: List[str], timeout: int = 30) -> Tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, errors="replace",
            creationflags=(0x08000000 if IS_WINDOWS else 0),  # CREATE_NO_WINDOW
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


@tool(
    id="system_health", category="system", name="سلامت کلی سیستم", name_en="System health score",
    desc="با یک کلیک: امتیاز سلامت سیستم، مصرف پردازنده/حافظه/دیسک، دما، باتری و کارهای پیشنهادی.",
    desc_en="One click: a health score plus CPU/RAM/disk load, temperature, battery and tips.",
    icon="heart", order=100,
    keywords=("health", "checkup", "system score", "سلامت سیستم", "چکاپ"),
    fields=[
        Field("mode", K_CHOICE, "سطح", "Level", default="quick",
              choices=[("quick", "سریع", "Quick"), ("deep", "عمیق (چند ثانیه)", "Deep (a few seconds)")]),
    ],
)
def system_health(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    deep = str(v.get("mode") or "quick") == "deep"
    ctx.progress(1, 6, ctx.M("خواندن پردازنده", "Reading CPU"))
    cpu_percent = psutil.cpu_percent(interval=1.2 if deep else 0.4)
    cores = psutil.cpu_count(logical=True) or 0
    ctx.progress(2, 6, ctx.M("خواندن حافظه", "Reading memory"))
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    ctx.progress(3, 6, ctx.M("خواندن دیسک", "Reading disks"))
    disk_lines: List[str] = []
    worst_disk = 0.0
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        worst_disk = max(worst_disk, usage.percent)
        disk_lines.append(
            f"   {part.device or part.mountpoint} — {usage.percent:4.1f}% "
            f"({human_size(usage.free)} {ctx.M('آزاد', 'free')})")
    ctx.progress(4, 6, ctx.M("خواندن دما و باتری", "Reading sensors"))
    temperatures: List[float] = []
    try:
        for name, entries in (psutil.sensors_temperatures() or {}).items():
            for entry in entries:
                if entry.current:
                    temperatures.append(entry.current)
    except Exception:
        pass
    battery_text = ""
    battery = None
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery:
        battery_text = ctx.M(
            f"باتری: {battery.percent:.0f}%{' (در حال شارژ)' if battery.power_plugged else ''}",
            f"Battery: {battery.percent:.0f}%{' (charging)' if battery.power_plugged else ''}")
    uptime = human_duration(__import__("time").time() - psutil.boot_time())
    ctx.progress(5, 6, ctx.M("محاسبه‌ی امتیاز", "Scoring"))
    score = 100.0
    deductions: List[str] = []
    if cpu_percent > 85:
        score -= 18
        deductions.append(ctx.M("پردازنده بسیار پرمصرف است", "CPU is heavily loaded"))
    elif cpu_percent > 60:
        score -= 8
        deductions.append(ctx.M("پردازنده نسبتاً پرمصرف است", "CPU is fairly busy"))
    if memory.percent > 90:
        score -= 22
        deductions.append(ctx.M("حافظه تقریباً پر است", "RAM is nearly full"))
    elif memory.percent > 75:
        score -= 10
        deductions.append(ctx.M("حافظه‌ی زیادی اشغال شده", "RAM usage is high"))
    if swap.percent > 50:
        score -= 8
        deductions.append(ctx.M("swap در حال استفاده‌ی سنگین است", "swap is heavily used"))
    if worst_disk > 92:
        score -= 20
        deductions.append(ctx.M("فضای دیسک تقریباً تمام شده", "a disk is almost full"))
    elif worst_disk > 80:
        score -= 8
        deductions.append(ctx.M("فضای دیسک کم است", "disk space is getting low"))
    if temperatures and max(temperatures) > 85:
        score -= 15
        deductions.append(ctx.M("دمای سیستم بالاست", "system temperature is high"))
    if battery and battery.percent < 15 and not battery.power_plugged:
        score -= 6
        deductions.append(ctx.M("باتری کم است", "battery is low"))
    score = max(5, min(100, score))
    verdict = (ctx.M("عالی", "Excellent") if score >= 90 else
               ctx.M("خوب", "Good") if score >= 75 else
               ctx.M("قابل قبول", "Fair") if score >= 55 else ctx.M("نیازمند رسیدگی", "Needs attention"))
    ctx.progress(6, 6, "")
    detail_lines = [
        f"{ctx.M('امتیاز', 'Score')}: {score:.0f}/100 — {verdict}",
        f"CPU: {cpu_percent:.1f}% ({cores} {ctx.M('هسته', 'cores')})",
        f"RAM: {memory.percent:.1f}% — {human_size(memory.used)} / {human_size(memory.total)}",
        f"Swap: {swap.percent:.1f}%",
        ctx.M("دیسک‌ها:", "Disks:"),
        *disk_lines,
    ]
    if temperatures:
        detail_lines.append(ctx.M(f"دما: {max(temperatures):.0f}°C", f"Temperature: {max(temperatures):.0f}°C"))
    if battery_text:
        detail_lines.append(battery_text)
    detail_lines.append(ctx.M(f"روشن بودن سیستم: {uptime}", f"Uptime: {uptime}"))
    if deductions:
        detail_lines.append("")
        detail_lines.append(ctx.M("پیشنهادها:", "Suggestions:"))
        detail_lines.extend(f"• {item}" for item in deductions)
    return RunResult(
        message=ctx.M(f"امتیاز سلامت سیستم: {score:.0f}/100 — {verdict}", f"System health: {score:.0f}/100 — {verdict}"),
        details="\n".join(detail_lines),
    )


@tool(
    id="disk_health", category="system", name="سلامت دیسک‌ها", name_en="Disk health",
    desc="وضعیت SMART و نوع دیسک‌های فیزیکی را از ویندوز می‌خواند.",
    desc_en="Read SMART status and media type of physical drives.",
    icon="disk", order=101,
    keywords=("smart", "disk health", "سلامت دیسک", "هارد"),
    fields=[
        Field("include_partitions", K_BOOL, "نمایش پارتیشن‌ها هم", "Include partitions", default=True),
    ],
)
def disk_health(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    lines: List[str] = []
    if IS_WINDOWS:
        code, output = _run(["powershell", "-NoProfile", "-Command",
                             "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,"
                             "OperationalStatus,@{n='GB';e={[math]::Round($_.Size/1GB)}} | "
                             "Format-Table -AutoSize | Out-String -Width 200"])
        if code == 0 and output.strip():
            lines.append(output.strip())
        else:
            code, output = _run(["wmic", "diskdrive", "get", "model,status,size"])
            lines.append(output.strip() or ctx.M("اطلاعات دیسک خوانده نشد.", "Could not read disk info."))
    else:
        if Path("/sys/block").is_dir():
            for device in sorted(Path("/sys/block").iterdir()):
                name = device.name
                if name.startswith(("loop", "ram", "zram")):
                    continue
                size_file = device / "size"
                size = 0
                try:
                    size = int(size_file.read_text().strip()) * 512
                except Exception:
                    pass
                lines.append(f"{name}: {human_size(size)}")
        else:
            lines.append(ctx.M("خواندن SMART فقط در ویندوز پشتیبانی می‌شود.",
                               "SMART reading is only supported on Windows."))
    if v.get("include_partitions", True):
        lines.append("")
        lines.append(ctx.M("پارتیشن‌ها:", "Partitions:"))
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            lines.append(f"  {part.device or part.mountpoint}  {part.fstype:8}  "
                         f"{usage.percent:5.1f}%  {human_size(usage.free)} {ctx.M('آزاد', 'free')}")
    return RunResult(message=ctx.M("وضعیت دیسک‌ها خوانده شد.", "Disk information collected."),
                     details="\n".join(lines))


@tool(
    id="power_plan", category="system", name="طرح‌های برق", name_en="Power plans",
    desc="طرح‌های مصرف انرژی ویندوز را فهرست یا فعال می‌کند (ذخیره‌ی باتری / حداکثر کارایی).",
    desc_en="List or switch Windows power plans (battery saver vs. max performance).",
    icon="power", order=102, windows_only=True, admin=True,
    keywords=("power plan", "battery saver", "مدیریت انرژی", "مصرف باتری"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست طرح‌ها", "List plans"), ("set", "فعال کردن", "Activate")]),
        Field("plan", K_CHOICE, "طرح", "Plan", default="high",
              choices=[("high", "حداکثر کارایی", "High performance"),
                       ("balanced", "متعادل", "Balanced"),
                       ("saver", "صرفه‌جویی باتری", "Power saver")]),
    ],
)
def power_plan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    code, output = _run(["powercfg", "/list"])
    if code != 0 and not output.strip():
        raise ToolError(ctx.M("powercfg اجرا نشد.", "powercfg could not run."))
    plans: Dict[str, str] = {}
    for line in output.splitlines():
        if "GUID" not in line and "{" not in line:
            continue
        import re

        match = re.search(r"([0-9a-fA-F-]{36})", line)
        if not match:
            continue
        name = line.split("(", 1)[-1].rstrip(")").strip().split(")")[0]
        plans[match.group(1)] = name
    if str(v.get("action") or "list") == "set":
        target = str(v.get("plan") or "high")
        wanted = {"high": "high performance", "balanced": "balanced", "saver": "power saver"}[target]
        chosen: Optional[str] = None
        for guid, name in plans.items():
            lowered = name.lower()
            if target == "balanced" and "balanced" in lowered:
                chosen = guid
            elif target == "saver" and ("saver" in lowered or "battery" in lowered):
                chosen = guid
            elif target == "high" and ("high" in lowered or "performance" in lowered):
                chosen = guid
        if not chosen:
            raise ToolError(ctx.M(f"طرح «{wanted}» پیدا نشد. فهرست طرح‌ها را ببینید.",
                                  f"The '{wanted}' plan was not found. List the plans first."))
        code, output2 = _run(["powercfg", "/setactive", chosen])
        if code != 0:
            raise ToolError(ctx.M("تغییر طرح برق نیازمند دسترسی مدیر است.\n" + output2.strip(),
                                  "Switching the power plan needs administrator rights.\n" + output2.strip()))
        return RunResult(message=ctx.M(f"طرح برق روی «{wanted}» تنظیم شد.", f"Power plan set to '{wanted}'."),
                         details="\n".join(f"{g}  {n}" for g, n in plans.items()))
    return RunResult(
        message=ctx.M(f"{len(plans)} طرح برق پیدا شد.", f"{len(plans)} power plan(s) found."),
        details="\n".join(f"{guid}  {name}" for guid, name in plans.items()),
    )


@tool(
    id="scheduled_tasks", category="system", name="کارهای زمان‌بندی‌شده", name_en="Scheduled tasks",
    desc="کارهای زمان‌بندی‌شده‌ی ویندوز را فهرست می‌کند و امکان اجرا/حذف یک کار را می‌دهد.",
    desc_en="List Windows scheduled tasks and run or delete one.",
    icon="clock", order=103, windows_only=True,
    keywords=("task scheduler", "schtasks", "زمان بندی", "کارهای خودکار"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست", "List"), ("run", "اجرای فوری", "Run now"),
                       ("delete", "حذف کار", "Delete task")]),
        Field("filter", K_TEXT, "فیلتر نام (اختیاری)", "Name filter (optional)"),
        Field("name", K_TEXT, "نام دقیق کار", "Exact task name", visible_when=("action", ("run", "delete"))),
    ],
)
def scheduled_tasks(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    action = str(v.get("action") or "list")
    name = str(v.get("name") or "").strip()
    if action in ("run", "delete"):
        if not name:
            raise ToolError(ctx.M("نام دقیق کار را وارد کنید.", "Enter the exact task name."))
        code, output = _run(["schtasks", "/run" if action == "run" else "/delete", "/tn", name, "/f"])
        if code != 0:
            raise ToolError(ctx.M(f"دستور ناموفق بود:\n{output.strip()}", f"The command failed:\n{output.strip()}"))
        return RunResult(
            message=ctx.M("کار اجرا شد." if action == "run" else "کار حذف شد.",
                          "Task started." if action == "run" else "Task deleted."),
            details=name)
    code, output = _run(["schtasks", "/query", "/fo", "csv", "/nh"])
    if code != 0:
        raise ToolError(ctx.M(f"فهرست کارها خوانده نشد:\n{output.strip()}",
                              f"Could not list tasks:\n{output.strip()}"))
    needle = str(v.get("filter") or "").strip().lower()
    rows: List[str] = []
    for line in output.splitlines():
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) < 3:
            continue
        task_name, next_run, status = parts[0].strip('"'), parts[1], parts[2]
        if needle and needle not in task_name.lower():
            continue
        rows.append(f"{status:<10} {next_run:<22} {task_name}")
    return RunResult(
        message=ctx.M(f"{len(rows)} کار زمان‌بندی‌شده.", f"{len(rows)} scheduled task(s)."),
        details="\n".join(rows[:400]),
    )


@tool(
    id="env_vars", category="system", name="متغیرهای محیطی", name_en="Environment variables",
    desc="متغیرهای محیطی را می‌خواند یا یک متغیر کاربر را تنظیم/حذف می‌کند.",
    desc_en="Read environment variables, or set/remove a user variable.",
    icon="terminal", order=104,
    keywords=("environment", "path variable", "متغیر محیطی", "PATH"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست", "List"), ("get", "نمایش یک متغیر", "Show one"),
                       ("set", "تنظیم متغیر کاربر", "Set user variable"),
                       ("remove", "حذف متغیر کاربر", "Remove user variable")]),
        Field("name", K_TEXT, "نام", "Name", placeholder="JAVA_HOME"),
        Field("value", K_TEXT, "مقدار", "Value", visible_when=("action", "set")),
    ],
)
def env_vars(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    action = str(v.get("action") or "list")
    name = str(v.get("name") or "").strip()
    if action == "list":
        rows = [f"{key}={value}" for key, value in sorted(os.environ.items())]
        return RunResult(message=ctx.M(f"{len(rows)} متغیر محیطی.", f"{len(rows)} environment variables."),
                         details="\n".join(rows))
    if not name:
        raise ToolError(ctx.M("نام متغیر را وارد کنید.", "Enter the variable name."))
    if action == "get":
        value = os.environ.get(name)
        if value is None:
            raise ToolError(ctx.M(f"متغیر {name} تعریف نشده است.", f"{name} is not defined."))
        return RunResult(message=f"{name}={value}")
    need_windows(ctx)
    winreg = require("winreg", "winreg", ctx)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        if action == "set":
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(v.get("value") or ""))
            return RunResult(message=ctx.M(f"متغیر {name} برای کاربر تنظیم شد.",
                                           f"User variable {name} was set."),
                             details=ctx.M("برای دیدن در برنامه‌های باز، نشست را دوباره باز کنید.",
                                           "Restart the session for open programs to see it."))
        try:
            winreg.DeleteValue(key, name)
        except FileNotFoundError:
            raise ToolError(ctx.M(f"متغیر {name} پیدا نشد.", f"Variable {name} was not found."))
    return RunResult(message=ctx.M(f"متغیر {name} حذف شد.", f"Variable {name} removed."))


@tool(
    id="font_install", category="system", name="نصب فونت", name_en="Install fonts",
    desc="فونت‌های TTF/OTF را برای کاربر فعلی نصب می‌کند (بدون نیاز به مدیر).",
    desc_en="Install TTF/OTF fonts for the current user (no admin needed).",
    icon="text", order=105,
    keywords=("install font", "ttf", "نصب فونت", "فونت فارسی"),
    fields=[
        Field("src", K_FILES, "فایل‌های فونت", "Font files", required=True,
              filt="فونت (*.ttf *.otf);;همه فایل‌ها (*)", multiple=True),
    ],
)
def font_install(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    files = files_or_raise(v, "src", ctx)
    fonts_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Fonts"
    ensure_dir(fonts_dir)
    winreg = require("winreg", "winreg", ctx)
    import shutil

    installed: List[str] = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows NT\CurrentVersion\Fonts", 0,
                        winreg.KEY_SET_VALUE) as key:
        for index, path in enumerate(files, start=1):
            ctx.progress(index, len(files), path.name)
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            destination = fonts_dir / path.name
            shutil.copy2(path, destination)
            winreg.SetValueEx(key, path.stem, 0, winreg.REG_SZ, str(destination))
            installed.append(path.name)
    if installed:
        try:
            import ctypes

            ctypes.windll.gdi32.AddFontResourceW(str(fonts_dir / installed[-1]))  # type: ignore[attr-defined]
        except Exception:
            pass
    return RunResult(
        message=ctx.M(f"{len(installed)} فونت نصب شد.", f"{len(installed)} font(s) installed."),
        details="\n".join(installed))


@tool(
    id="startup_add", category="system", name="افزودن برنامه به استارتاپ",
    name_en="Add startup program",
    desc="یک برنامه یا دستور را به لیست برنامه‌هایی که با ویندوز بالا می‌آیند اضافه می‌کند.",
    desc_en="Add a program or command to the list that starts with Windows.",
    icon="power", order=106, windows_only=True,
    keywords=("add startup", "autorun", "افزودن به استارتاپ", "اجرای خودکار"),
    fields=[
        Field("name", K_TEXT, "نام نمایشی", "Display name", required=True),
        Field("command", K_TEXT, "دستور یا مسیر", "Command or path", required=True,
              placeholder="C:\\Tools\\app.exe"),
    ],
)
def startup_add(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    name = str(v.get("name") or "").strip()
    command = str(v.get("command") or "").strip()
    if not name or not command:
        raise ToolError(ctx.M("نام و دستور را پر کنید.", "Fill in both the name and the command."))
    winreg = require("winreg", "winreg", ctx)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
    return RunResult(message=ctx.M(f"«{name}» به استارتاپ اضافه شد.", f"'{name}' was added to startup."),
                     details=command)
