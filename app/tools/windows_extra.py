"""Windows power tools: winget, event log, drivers, restore points, cleanup, features."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_INT, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size
from ._helpers import need_windows, out_dir, require

NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def _cmd(args: List[str], ctx: RunContext, timeout: int = 180) -> tuple[int, str]:
    ctx.check_cancel()
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                   errors="replace", creationflags=NO_WINDOW)
    except FileNotFoundError:
        return 127, f"{args[0]} {ctx.M('نصب نیست یا در PATH نیست.', 'is not installed or not on PATH.')}"
    except subprocess.TimeoutExpired:
        return 124, ctx.M("دستور خیلی طول کشید و متوقف شد.", "The command timed out.")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)
    return completed.returncode, ((completed.stdout or "") + (completed.stderr or "")).strip()


def _powershell(ctx: RunContext, script: str, timeout: int = 180) -> tuple[int, str]:
    return _cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], ctx, timeout)


@tool(
    id="winget_tool", category="windows", name="نصب و به‌روزرسانی برنامه‌ها (winget)",
    name_en="Install apps (winget)",
    desc="برنامه‌ها را با winget جست‌وجو، نصب یا به‌روزرسانی می‌کند — بدون باز کردن فروشگاه.",
    desc_en="Search, install or upgrade apps with winget — no Store needed.",
    icon="download", order=110, windows_only=True, admin=False,
    keywords=("winget", "install app", "نصب برنامه", "آپدیت برنامه", "choco"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="search",
              choices=[("search", "جست‌وجو", "Search"), ("install", "نصب", "Install"),
                       ("upgrade", "به‌روزرسانی همه", "Upgrade all"), ("list", "فهرست نصب‌شده‌ها", "List installed")]),
        Field("query", K_TEXT, "نام برنامه", "App name", placeholder="vscode",
              visible_when=("action", ("search", "install"))),
        Field("silent", K_BOOL, "نصب بی‌صدا", "Silent install", default=True,
              visible_when=("action", "install")),
    ],
)
def winget_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    action = str(v.get("action") or "search")
    query = str(v.get("query") or "").strip()
    if action in ("search", "install") and not query:
        raise ToolError(ctx.M("نام برنامه را وارد کنید.", "Enter the app name."))
    if action == "search":
        code, output = _cmd(["winget", "search", query, "--accept-source-agreements"], ctx)
        if code != 0 and not output:
            raise ToolError(ctx.M("winget در دسترس نیست. آن را از Microsoft Store نصب کنید (App Installer).",
                                  "winget is unavailable. Install 'App Installer' from the Microsoft Store."))
        return RunResult(message=ctx.M(f"نتایج جست‌وجو برای «{query}»", f"Search results for '{query}'"),
                         details=output[:8000])
    if action == "install":
        args = ["winget", "install", "--id", query, "--accept-package-agreements",
                "--accept-source-agreements", "--exact"]
        if v.get("silent", True):
            args.append("--silent")
        ctx.progress(1, 2, ctx.M("در حال نصب…", "Installing…"))
        code, output = _cmd(args, ctx, timeout=900)
        ctx.progress(2, 2, "")
        if code != 0:
            args = ["winget", "install", query, "--accept-package-agreements", "--accept-source-agreements"]
            if v.get("silent", True):
                args.append("--silent")
            code, output = _cmd(args, ctx, timeout=900)
        message = (ctx.M("نصب انجام شد.", "Install finished.") if code == 0
                   else ctx.M("نصب ناموفق بود (شاید به دسترسی مدیر نیاز دارد).",
                              "Install failed (administrator rights may be required)."))
        return RunResult(message=message, details=output[-4000:])
    if action == "upgrade":
        ctx.progress(1, 2, ctx.M("به‌روزرسانی همه‌ی برنامه‌ها…", "Upgrading everything…"))
        code, output = _cmd(["winget", "upgrade", "--all", "--include-unknown",
                             "--accept-package-agreements", "--accept-source-agreements"], ctx, timeout=1800)
        ctx.progress(2, 2, "")
        return RunResult(message=ctx.M("به‌روزرسانی تمام شد.", "Upgrade finished."), details=output[-6000:])
    code, output = _cmd(["winget", "list", "--accept-source-agreements"], ctx)
    return RunResult(message=ctx.M("برنامه‌های نصب‌شده", "Installed applications"), details=output[:10000])


@tool(
    id="event_errors", category="windows", name="خطاهای اخیر ویندوز",
    name_en="Recent Windows errors",
    desc="خطاهای مهم System و Application را از Event Viewer می‌خواند (علت کندی و کرش).",
    desc_en="Read serious System/Application errors from the Event Log — the cause of crashes and slowdowns.",
    icon="alert", order=111, windows_only=True,
    keywords=("event log", "errors", "رویداد", "خطای ویندوز", "کرش", "بلواسکرین"),
    fields=[
        Field("log", K_CHOICE, "لاگ", "Log", default="System",
              choices=[("System", "System", "System"), ("Application", "Application", "Application")]),
        Field("count", K_INT, "تعداد", "Count", default=15, minimum=5, maximum=100),
        Field("hours", K_INT, "چند ساعت اخیر", "Within last hours", default=72, minimum=1, maximum=720),
    ],
)
def event_errors(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    log = str(v.get("log") or "System")
    count = max(5, int(v.get("count") or 15))
    hours = max(1, int(v.get("hours") or 72))
    script = (
        f"$since=(Get-Date).AddHours(-{hours}); "
        f"Get-WinEvent -FilterHashtable @{{LogName='{log}';Level=1,2;StartTime=$since}} "
        f"-MaxEvents {count} -ErrorAction SilentlyContinue | "
        "Select-Object TimeCreated,Id,ProviderName,@{n='Msg';e={($_.Message -split \"`n\")[0]}} | "
        "Format-Table -AutoSize | Out-String -Width 220"
    )
    code, output = _powershell(ctx, script, timeout=120)
    if code != 0 or not output.strip():
        return RunResult(message=ctx.M(f"خطای مهمی در {hours} ساعت اخیر ثبت نشده — خبر خوبی است!",
                                      f"No serious errors in the last {hours} hours — good news!"))
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return RunResult(
        message=ctx.M(f"{max(0, len(lines) - 2)} خطای مهم در {hours} ساعت اخیر لاگ {log}.",
                      f"Serious errors in the last {hours} hours in the {log} log."),
        details="\n".join(lines[:count + 4]))


@tool(
    id="driver_problems", category="windows", name="درایورهای مشکل‌دار",
    name_en="Problem drivers",
    desc="دستگاه‌هایی که درایور آن‌ها خطا دارد (کد مشکل در Device Manager) را پیدا می‌کند.",
    desc_en="List devices whose driver reports an error (Device Manager problem code).",
    icon="chip", order=112, windows_only=True, admin=True,
    keywords=("driver", "device manager", "درایور", "سخت افزار", "کد ۲۸"),
    fields=[
        Field("include_all", K_BOOL, "نمایش همه‌ی دستگاه‌ها", "Show all devices", default=False),
    ],
)
def driver_problems(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    if v.get("include_all"):
        script = ("Get-PnpDevice | Select-Object Status,Class,FriendlyName | "
                  "Format-Table -AutoSize | Out-String -Width 200")
    else:
        script = ("Get-PnpDevice -Status Error | Select-Object Status,Class,FriendlyName,InstanceId | "
                  "Format-Table -AutoSize | Out-String -Width 220")
    code, output = _powershell(ctx, script, timeout=120)
    if not output.strip() or (code != 0 and not output):
        raise ToolError(ctx.M("اطلاعات دستگاه‌ها خوانده نشد (شاید دسترسی مدیر لازم است).",
                              "Could not read device information (administrator rights may be needed)."))
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    return RunResult(
        message=ctx.M(f"{max(0, len(lines) - 2)} دستگاه مشکل‌دار پیدا شد.",
                      f"{max(0, len(lines) - 2)} device(s) with problems."),
        details="\n".join(lines[:120]))


@tool(
    id="restore_point", category="windows", name="ساخت نقطه‌ی بازگردانی",
    name_en="Create restore point",
    desc="قبل از تغییرات بزرگ، یک System Restore Point می‌سازد تا بتوانید ویندوز را برگردانید.",
    desc_en="Create a System Restore Point before big changes so Windows can be rolled back.",
    icon="undo", order=113, windows_only=True, admin=True,
    keywords=("restore point", "system restore", "نقطه بازگردانی", "بازگردانی ویندوز", "بکاپ"),
    fields=[
        Field("description", K_TEXT, "توضیح", "Description", default="WinGenius checkpoint"),
        Field("list", K_BOOL, "فهرست نقاط موجود", "List existing points", default=True),
    ],
)
def restore_point(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    description = str(v.get("description") or "WinGenius checkpoint").replace("'", "")
    ctx.progress(1, 4, ctx.M("فعال‌سازی محافظت سیستم", "Enabling system protection"))
    _powershell(ctx, "Enable-ComputerRestore -Drive $env:SystemDrive", timeout=120)
    ctx.progress(2, 4, ctx.M("ساخت نقطه‌ی بازگردانی", "Creating the restore point"))
    code, output = _powershell(ctx,
                               f"Checkpoint-Computer -Description '{description}' "
                               "-RestorePointType 'MODIFY_SETTINGS'", timeout=600)
    ctx.progress(3, 4, ctx.M("خواندن فهرست", "Reading the list"))
    listing = ""
    if v.get("list", True):
        _code, listing = _powershell(ctx,
                                     "Get-ComputerRestorePoint | Select-Object SequenceNumber,"
                                     "Description,CreationTime | Format-Table -AutoSize | Out-String -Width 160",
                                     timeout=120)
    ctx.progress(4, 4, "")
    if code != 0 and "access" in output.lower():
        raise ToolError(ctx.M("این کار نیازمند اجرای برنامه با دسترسی مدیر است.",
                              "Run the app as administrator for this."))
    if code != 0 and "frequency" in output.lower():
        return RunResult(
            message=ctx.M("ویندوز اجازه نداد نقطه‌ی جدید بسازد (محدودیت ۲۴ ساعته فعال است).",
                          "Windows refused — the 24-hour limit for restore points is active."),
            details=listing)
    return RunResult(message=ctx.M("نقطه‌ی بازگردانی ساخته شد.", "Restore point created."),
                     details=(output + "\n\n" + listing).strip()[:4000])


@tool(
    id="component_cleanup", category="windows", name="پاکسازی مخزن کامپوننت‌ها (WinSxS)",
    name_en="Component store cleanup",
    desc="اجزای قدیمی ویندوز را با DISM پاک می‌کند و چند گیگابایت آزاد می‌کند (کند است).",
    desc_en="Clean superseded Windows components with DISM and free gigabytes (slow but safe).",
    icon="broom", order=114, windows_only=True, admin=True, danger=True,
    keywords=("dism", "winsxs", "cleanup", "پاکسازی ویندوز", "فضای دیسک", "کامپوننت"),
    fields=[
        Field("confirm", K_TEXT, "بنویسید CLEAN برای تأیید", "Type CLEAN to confirm", placeholder="CLEAN"),
        Field("analyze_first", K_BOOL, "اول فقط تحلیل", "Analyse only", default=True),
    ],
)
def component_cleanup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    if v.get("analyze_first", True) or str(v.get("confirm") or "").strip().upper() != "CLEAN":
        code, output = _cmd(["dism", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"], ctx, 900)
        return RunResult(
            message=ctx.M("تحلیل انجام شد. برای پاکسازی، در کادر تأیید CLEAN بنویسید و تحلیل خودکار را خاموش کنید.",
                          "Analysis done. To clean, type CLEAN and turn off auto-analysis."),
            details=output[-4000:])
    ctx.progress(1, 2, ctx.M("پاکسازی مخزن (چند دقیقه طول می‌کشد)…", "Cleaning the store (may take a while)…"))
    code, output = _cmd(["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup",
                         "/ResetBase"], ctx, 3600)
    ctx.progress(2, 2, "")
    if code != 0 and "access" in output.lower():
        raise ToolError(ctx.M("این کار نیازمند دسترسی مدیر است.", "Administrator rights are required."))
    return RunResult(message=ctx.M("پاکسازی مخزن کامل شد.", "Component store cleanup finished."),
                     details=output[-4000:])


@tool(
    id="windows_features", category="windows", name="قابلیت‌های ویندوز",
    name_en="Windows features",
    desc="قابلیت‌های اختیاری ویندوز (WSL، Hyper-V، IIS، .NET 3.5…) را فهرست و فعال/غیرفعال می‌کند.",
    desc_en="List optional Windows features (WSL, Hyper-V, IIS, .NET 3.5…) and enable or disable them.",
    icon="puzzle", order=115, windows_only=True, admin=True,
    keywords=("windows features", "wsl", "hyper-v", "iis", "قابلیت ویندوز", "فعال سازی"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="list",
              choices=[("list", "فهرست", "List"), ("enable", "فعال کردن", "Enable"),
                       ("disable", "غیرفعال کردن", "Disable")]),
        Field("name", K_TEXT, "نام قابلیت", "Feature name", placeholder="Microsoft-Windows-Subsystem-Linux",
              visible_when=("action", ("enable", "disable"))),
    ],
)
def windows_features(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    action = str(v.get("action") or "list")
    name = str(v.get("name") or "").strip()
    if action == "list":
        code, output = _powershell(ctx,
                                   "Get-WindowsOptionalFeature -Online | Select-Object State,FeatureName | "
                                   "Sort-Object State,FeatureName | Format-Table -AutoSize | Out-String -Width 160",
                                   timeout=300)
        if not output.strip():
            raise ToolError(ctx.M("فهرست قابلیت‌ها خوانده نشد (دسترسی مدیر لازم است).",
                                  "Could not read the feature list (administrator rights needed)."))
        enabled = sum(1 for line in output.splitlines() if "Enabled" in line)
        return RunResult(message=ctx.M(f"{enabled} قابلیت فعال است.", f"{enabled} feature(s) enabled."),
                         details=output[:12000])
    if not name:
        raise ToolError(ctx.M("نام دقیق قابلیت را وارد کنید.", "Enter the exact feature name."))
    verb = "Enable" if action == "enable" else "Disable"
    ctx.progress(1, 2, ctx.M("در حال اعمال تغییر…", "Applying the change…"))
    code, output = _powershell(
        ctx, f"{verb}-WindowsOptionalFeature -Online -FeatureName '{name}' -All -NoRestart", timeout=1800)
    ctx.progress(2, 2, "")
    if code != 0:
        raise ToolError(ctx.M(f"تغییر انجام نشد:\n{output[-1500:]}", f"The change failed:\n{output[-1500:]}"))
    return RunResult(
        message=ctx.M(f"«{name}» {'فعال' if action == 'enable' else 'غیرفعال'} شد. برای اثرگذاری راه‌اندازی مجدد لازم است.",
                      f"'{name}' was {'enabled' if action == 'enable' else 'disabled'}. Restart to apply."),
        details=output[-2000:])


@tool(
    id="crash_dumps", category="windows", name="گزارش کرش و مینی‌دامپ",
    name_en="Crash dumps",
    desc="فایل‌های مینی‌دامپ (علت بلواسکرین) و کرش برنامه‌ها را فهرست می‌کند.",
    desc_en="List minidump files (blue-screen cause) and application crash reports.",
    icon="bug", order=116, windows_only=True,
    keywords=("bsod", "minidump", "crash", "بلواسکرین", "کرش برنامه", "ری استارت"),
    fields=[
        Field("open_folder", K_BOOL, "باز کردن پوشه", "Open the folder", default=False),
    ],
)
def crash_dumps(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    roots = [Path(r"C:\Windows\Minidump"),
             Path.home() / "AppData" / "Local" / "CrashDumps",
             Path(r"C:\Windows\MEMORY.DMP")]
    lines: List[str] = []
    total_size = 0
    for root in roots:
        if root.is_file():
            size = root.stat().st_size
            total_size += size
            lines.append(f"{root.name}: {human_size(size)}")
            continue
        if not root.is_dir():
            continue
        files = sorted(root.glob("*"), key=lambda p: -p.stat().st_mtime)[:40]
        if not files:
            continue
        lines.append(f"\n{root} — {len(files)} " + ctx.M("فایل", "file(s)"))
        for path in files:
            stamp = ""
            try:
                from datetime import datetime

                stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            size = path.stat().st_size
            total_size += size
            lines.append(f"  {stamp}  {human_size(size):>10}  {path.name}")
    if v.get("open_folder"):
        try:
            import os

            os.startfile(str(Path(r"C:\Windows\Minidump")))  # type: ignore[attr-defined]
        except Exception:
            pass
    if not lines:
        return RunResult(message=ctx.M("هیچ فایل کرشی پیدا نشد — سیستم پایدار بوده است.",
                                       "No crash dumps found — the system has been stable."))
    return RunResult(
        message=ctx.M(f"فایل‌های کرش پیدا شد (حدود {human_size(total_size)}).",
                      f"Crash files found (about {human_size(total_size)})."),
        details="\n".join(lines[:120]))
