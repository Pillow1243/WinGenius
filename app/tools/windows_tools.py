"""Direct Windows integrations: applets, power actions, screenshot, wallpaper."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, run_cmd, unique_path
from ..core import winapi
from ._helpers import IS_WINDOWS, need_windows, out_dir, require

APPLETS = [
    ("settings", "تنظیمات ویندوز", "Windows Settings", "ms-settings:"),
    ("display", "نمایشگر", "Display", "ms-settings:display"),
    ("sound", "صدا", "Sound", "ms-settings:sound"),
    ("bluetooth", "بلوتوث و دستگاه‌ها", "Bluetooth & devices", "ms-settings:bluetooth"),
    ("wifi_settings", "شبکه و وای‌فای", "Network & Wi-Fi", "ms-settings:network-wifi"),
    ("apps", "برنامه‌ها و حذف نصب", "Apps & features", "ms-settings:appsfeatures"),
    ("update", "Windows Update", "Windows Update", "ms-settings:windowsupdate"),
    ("storage", "حافظه (Storage Sense)", "Storage", "ms-settings:storagesense"),
    ("privacy", "حریم خصوصی", "Privacy", "ms-settings:privacy"),
    ("about", "درباره‌ی سیستم", "About device", "ms-settings:about"),
    ("control", "کنترل پنل", "Control Panel", "control"),
    ("devmgmt", "مدیریت دستگاه‌ها", "Device Manager", "devmgmt.msc"),
    ("services", "سرویس‌ها", "Services", "services.msc"),
    ("taskmgr", "مدیر وظایف", "Task Manager", "taskmgr"),
    ("diskmgmt", "مدیریت دیسک", "Disk Management", "diskmgmt.msc"),
    ("eventvwr", "Event Viewer", "Event Viewer", "eventvwr.msc"),
    ("regedit", "ویرایشگر رجیستری", "Registry Editor", "regedit"),
    ("msconfig", "پیکربندی سیستم", "System Configuration", "msconfig"),
    ("resmon", "مانیتور منابع", "Resource Monitor", "resmon"),
    ("sysdm", "تنظیمات سیستم", "System Properties", "sysdm.cpl"),
    ("powercfg", "تنظیمات برق", "Power Options", "powercfg.cpl"),
    ("programs", "برنامه‌ها و ویژگی‌ها", "Programs & Features", "appwiz.cpl"),
    ("datetime", "تاریخ و زمان", "Date & Time", "timedate.cpl"),
    ("sound_panel", "دستگاه‌های صوتی", "Sound devices", "mmsys.cpl"),
    ("network_panel", "اتصال‌های شبکه", "Network Connections", "ncpa.cpl"),
    ("firewall", "فایروال ویندوز", "Windows Firewall", "firewall.cpl"),
    ("mouse", "ماوس", "Mouse", "main.cpl"),
    ("region", "زبان و منطقه", "Language & Region", "ms-settings:regionlanguage"),
    ("fonts", "فونت‌ها", "Fonts", r"shell:fonts"),
    ("startup_folder", "پوشه‌ی استارتاپ", "Startup folder", "shell:startup"),
    ("temp_folder", "پوشه‌ی فایل‌های موقت", "Temp folder", "shell:temporary internet files"),
    ("downloads", "پوشه‌ی دانلودها", "Downloads folder", "shell:downloads"),
    ("dxdiag", "ابزار DirectX (dxdiag)", "DirectX diagnostic", "dxdiag"),
    ("cmd", "Command Prompt", "Command Prompt", "cmd"),
    ("powershell", "PowerShell", "PowerShell", "powershell"),
    ("calc", "ماشین‌حساب", "Calculator", "calc"),
    ("notepad", "Notepad", "Notepad", "notepad"),
    ("paint", "Paint", "Paint", "mspaint"),
    ("snipping", "ابزار برش تصویر", "Snipping Tool", "snippingtool"),
    ("magnifier", "ذره‌بین", "Magnifier", "magnify"),
    ("osk", "صفحه‌کلید روی صفحه", "On-Screen Keyboard", "osk"),
    ("charmap", "نقشه‌ی کاراکترها", "Character Map", "charmap"),
    ("optional", "ویژگی‌های اختیاری ویندوز", "Optional features", "optionalfeatures"),
]


@tool(
    id="windows_applets", category="windows", name="مرکز ابزارهای ویندوز", name_en="Windows toolbox",
    desc="دسترسی یک‌کلیکی به ۴۰+ ابزار داخلی ویندوز: تنظیمات، مدیریت دستگاه، سرویس‌ها و…",
    desc_en="One-click access to 40+ built-in Windows tools and settings pages.",
    icon="window", order=10, windows_only=True,
    keywords=("windows tools", "control panel", "device manager", "task manager", "ابزار ویندوز"),
    fields=[
        Field("applet", K_CHOICE, "ابزار", "Tool", default="settings",
              choices=[(key, fa, en) for key, fa, en, _cmd in APPLETS]),
    ],
)
def windows_applets(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    key = str(v.get("applet") or "settings")
    match = next((item for item in APPLETS if item[0] == key), None)
    if not match:
        raise ToolError(ctx.M("ابزار نامعتبر است.", "Unknown tool."))
    _key, fa, en, command = match
    name = en if ctx.lang == "en" else fa
    try:
        if command.startswith(("cmd", "powershell")):
            subprocess.Popen(command, shell=True)
        else:
            os.startfile(command)  # type: ignore[attr-defined]
    except Exception:
        subprocess.Popen(command, shell=True)
    return RunResult(message=ctx.M(f"«{name}» باز شد.", f"Opened {name}."))


@tool(
    id="power_actions", category="windows", name="کنترل توان", name_en="Power control",
    desc="قفل، خواب، خاموشی یا ری‌استارت با تایمر، بدون جست‌وجو در منوها.",
    desc_en="Lock, sleep, shut down or restart (with delay) from one place.",
    icon="power", order=20, windows_only=True, danger=True,
    keywords=("shutdown", "restart", "sleep", "lock", "خاموش", "ریستارت", "خواب", "قفل"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="lock",
              choices=[("lock", "قفل صفحه", "Lock screen"),
                       ("sleep", "خواب (Sleep)", "Sleep"),
                       ("hibernate", "هایبرنیت", "Hibernate"),
                       ("shutdown", "خاموش کردن", "Shut down"),
                       ("restart", "ری‌استارت", "Restart"),
                       ("logoff", "خروج از حساب", "Sign out"),
                       ("cancel", "لغو تایمر خاموشی", "Cancel pending shutdown")]),
        Field("delay", K_INT, "تأخیر (ثانیه)", "Delay (seconds)", default=0, minimum=0, maximum=3600,
              visible_when=("action", ("shutdown", "restart", "logoff"))),
    ],
)
def power_actions(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    action = str(v.get("action") or "lock")
    delay = max(0, int(v.get("delay") or 0))
    commands = {
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "hibernate": ["shutdown", "/h"],
        "shutdown": ["shutdown", "/s", "/t", str(delay)],
        "restart": ["shutdown", "/r", "/t", str(delay)],
        "logoff": ["shutdown", "/l"],
        "cancel": ["shutdown", "/a"],
    }
    cmd = commands.get(action)
    if not cmd:
        raise ToolError(ctx.M("عملیات نامعتبر است.", "Invalid action."))
    try:
        subprocess.Popen(cmd, close_fds=True)
    except Exception as exc:
        raise ToolError(str(exc))
    label = ctx.M("عملیات اجرا شد", "Action triggered")
    if delay and action in ("shutdown", "restart"):
        label = ctx.M(f"سیستم تا {delay} ثانیه دیگر خاموش/ری‌استارت می‌شود.",
                      f"The system will shut down/restart in {delay} seconds.")
    return RunResult(message=label)


@tool(
    id="screenshot", category="windows", name="اسکرین‌شات", name_en="Screenshot",
    desc="از تمام صفحه یا نمایشگر اصلی عکس می‌گیرد (با تأخیر قابل تنظیم).",
    desc_en="Capture the full screen or primary display, with optional delay.",
    icon="camera", order=30, keywords=("screenshot", "اسکرین شات", "عکس از صفحه", "capture"),
    fields=[
        Field("monitor", K_CHOICE, "نمایشگر", "Monitor", default="all",
              choices=[("all", "همه‌ی نمایشگرها", "All monitors"),
                       ("primary", "نمایشگر اصلی", "Primary display")]),
        Field("delay", K_INT, "تأخیر (ثانیه)", "Delay (seconds)", default=0, minimum=0, maximum=60),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PNG (*.png);;JPG (*.jpg)"),
    ],
)
def screenshot(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    delay = int(v.get("delay") or 0)
    if delay:
        for remaining in range(delay, 0, -1):
            ctx.progress(delay - remaining, delay, ctx.M(f"{remaining}…", f"{remaining}…"))
            time.sleep(1)
    import datetime

    default_name = f"screenshot_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    dest = Path(str(v.get("out") or "")).expanduser() if str(v.get("out") or "").strip() else unique_path(
        out_dir(ctx) / default_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        mss = require("mss", "mss", ctx)
        mss_tools = require("mss.tools", "mss", ctx)
        with mss.mss() as sct:
            index = 0 if str(v.get("monitor") or "all") == "all" else 1
            shot = sct.grab(sct.monitors[index])
            _png, _size = mss_tools.to_png(shot.rgb, shot.size, output=str(dest))
    except ToolError:
        # Fallback: PowerShell screen capture on Windows
        need_windows(ctx)
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$bmp.Save('{dest}')"
        )
        code, out, err = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=60)
        if not dest.exists():
            raise ToolError(ctx.M(f"گرفتن اسکرین‌شات ناموفق بود: {err or out}",
                                  f"Screenshot failed: {err or out}"))
    except Exception as exc:
        raise ToolError(ctx.M(f"گرفتن اسکرین‌شات ناموفق بود: {exc}", f"Screenshot failed: {exc}"))
    return RunResult(message=ctx.M(f"اسکرین‌شات ذخیره شد ({human_size(dest.stat().st_size)}).",
                                  f"Screenshot saved ({human_size(dest.stat().st_size)})."),
                     outputs=[str(dest)], open_target=str(dest))


@tool(
    id="set_wallpaper", category="windows", name="تنظیم تصویر پس‌زمینه", name_en="Set wallpaper",
    desc="یک تصویر را پس‌زمینه‌ی ویندوز می‌کند.",
    desc_en="Set an image as the Windows desktop wallpaper.",
    icon="image", order=40, windows_only=True, keywords=("wallpaper", "پس زمینه", "دیوار"),
    fields=[Field("src", K_FILE, "تصویر", "Image", required=True,
                  filt="تصاویر (*.png *.jpg *.jpeg *.bmp);;همه فایل‌ها (*)")],
)
def set_wallpaper(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    path = Path(str(v.get("src") or ""))
    if not path.exists():
        raise ToolError(ctx.M("تصویر پیدا نشد.", "Image not found."))
    try:
        winapi.set_wallpaper(str(path))
    except Exception as exc:
        raise ToolError(ctx.M(f"تنظیم پس‌زمینه ناموفق بود: {exc}", f"Setting wallpaper failed: {exc}"))
    return RunResult(message=ctx.M("تصویر پس‌زمینه تنظیم شد.", "Wallpaper applied."))


@tool(
    id="dark_mode", category="windows", name="حالت تیره ویندوز", name_en="Windows dark mode",
    desc="حالت تیره/روشن ویندوز را با یک کلیک عوض می‌کند.",
    desc_en="Toggle the Windows dark/light theme in one click.",
    icon="color", order=50, windows_only=True, keywords=("dark mode", "حالت شب", "تم ویندوز", "روشن"),
    fields=[
        Field("mode", K_CHOICE, "حالت", "Mode", default="dark",
              choices=[("dark", "تیره", "Dark"), ("light", "روشن", "Light")]),
    ],
)
def dark_mode(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    winreg = require("winreg", "pywin32", None)
    value = 0 if str(v.get("mode") or "dark") == "dark" else 1
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, value)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, value)
    except OSError as exc:
        raise ToolError(str(exc))
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                                                 "ImmersiveColorSet", 0x0002, 5000, None)
    except Exception:
        pass
    return RunResult(message=ctx.M("تم ویندوز عوض شد.", "Windows theme switched."))


@tool(
    id="create_shortcut", category="windows", name="ساخت میان‌بر", name_en="Create shortcut",
    desc="میان‌بر دسکتاپ یا استارتاپ برای هر فایل/برنامه/پوشه می‌سازد.",
    desc_en="Create a Desktop or Startup shortcut for any file/app/folder.",
    icon="folder", order=60, windows_only=True, keywords=("shortcut", "میانبر", "دسکتاپ"),
    fields=[
        Field("target", K_FILE, "فایل / پوشه‌ی هدف", "Target file / folder", required=True,
              filt="همه فایل‌ها (*)"),
        Field("name", K_TEXT, "نام میان‌بر", "Shortcut name", default="My Shortcut"),
        Field("where", K_CHOICE, "مکان", "Location", default="desktop",
              choices=[("desktop", "دسکتاپ", "Desktop"), ("startup", "استارتاپ", "Startup"),
                       ("startmenu", "منوی استارت", "Start menu")]),
    ],
)
def create_shortcut(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    target = Path(str(v.get("target") or ""))
    if not target.exists():
        raise ToolError(ctx.M("مسیر هدف پیدا نشد.", "Target path not found."))
    name = str(v.get("name") or "Shortcut")
    if not name.lower().endswith(".lnk"):
        name += ".lnk"
    where = str(v.get("where") or "desktop")
    if where == "desktop":
        folder = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    elif where == "startup":
        folder = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
                  / "Start Menu" / "Programs" / "Startup")
    else:
        folder = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows"
                  / "Start Menu" / "Programs")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    script = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut('{dest}');"
        f"$s.TargetPath = '{target}';"
        f"$s.WorkingDirectory = '{target.parent}';"
        "$s.Save()"
    )
    code, out, err = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=40)
    if not dest.exists():
        raise ToolError(ctx.M(f"ساخت میان‌بر ناموفق بود: {err or out}", f"Shortcut creation failed: {err or out}"))
    return RunResult(message=ctx.M("میان‌بر ساخته شد.", "Shortcut created."),
                     outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------- Explorer / SendTo
@tool(
    id="sendto_install", category="windows", name="افزودن به منوی «ارسال به» ویندوز",
    name_en="Add to the Windows “Send to” menu",
    desc=("یک میان‌بر در پوشه‌ی «Send to» می‌سازد تا با راست‌کلیک روی هر فایل، "
          "«Send to → WinGenius» را ببینید و همان فایل‌ها داخل برنامه باز شوند."),
    desc_en=("Creates a shortcut in the Windows “Send to” folder, so right-clicking any file "
             "shows “Send to → WinGenius” and opens those files inside the app."),
    icon="link", order=190,
    keywords=("send to", "context menu", "right click", "ارسال به", "راست کلیک",
              "explorer", "shortcut", "میان‌بر"),
    fields=[
        Field("action", K_CHOICE, "کار", "Action", default="install",
              choices=[("install", "نصب میان‌بر", "Install the shortcut"),
                       ("remove", "حذف میان‌بر", "Remove it"),
                       ("status", "بررسی وضعیت", "Check status")]),
    ],
)
def sendto_install(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    action = str(v.get("action") or "install")
    if not IS_WINDOWS:
        raise ToolError(ctx.M(
            "این ابزار مخصوص ویندوز است؛ «Send to» بخشی از Explorer ویندوز است.",
            "This tool is Windows-only — “Send to” is part of Windows Explorer.",
        ))
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    folder = Path(appdata) / "Microsoft" / "Windows" / "SendTo"
    shortcut = folder / "WinGenius.lnk"

    if action == "status":
        exists = shortcut.exists()
        return RunResult(
            message=ctx.M("میان‌بر نصب است." if exists else "میان‌بر نصب نیست.",
                          "The shortcut is installed." if exists else "The shortcut is not installed."),
            details=str(shortcut),
        )

    if action == "remove":
        if not shortcut.exists():
            return RunResult(message=ctx.M("از قبل وجود نداشت.", "It was not there."))
        shortcut.unlink()
        return RunResult(message=ctx.M("میان‌بر حذف شد.", "The shortcut was removed."),
                         details=str(shortcut))

    if abstract_target() is None:
        raise ToolError(ctx.M("مسیر اجرای برنامه پیدا نشد.", "The program path could not be resolved."))
    folder.mkdir(parents=True, exist_ok=True)
    target, arguments = abstract_target()
    _make_shortcut(shortcut, target, arguments, ctx)
    if not shortcut.exists():
        raise ToolError(ctx.M("میان‌بر ساخته نشد (دسترسی به پوشه‌ی SendTo نبود).",
                              "Could not create the shortcut (no access to the SendTo folder)."))
    return RunResult(
        message=ctx.M("میان‌بر «Send to → WinGenius» نصب شد.",
                      "“Send to → WinGenius” is installed."),
        details=f"{shortcut} → {target} {arguments}",
        outputs=[str(shortcut)],
    )


def abstract_target():
    """(exe, arguments) the shortcut should run — frozen exe or python + main.py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), "--sendto"
    entry = Path(__file__).resolve().parents[2] / "main.py"
    if not entry.exists():
        return None
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return (pythonw if pythonw.exists() else python), f'"{entry}" --sendto'


def _make_shortcut(shortcut: Path, target: Path, arguments: str, ctx: RunContext) -> None:
    """Prefer COM (pywin32); fall back to PowerShell so it works on a bare install."""
    try:
        import pythoncom
        from win32com.client import Dispatch

        pythoncom.CoInitialize()
        shell = Dispatch("WScript.Shell")
        link = shell.CreateShortCut(str(shortcut))
        link.Targetpath = str(target)
        link.Arguments = arguments
        link.WorkingDirectory = str(target.parent)
        link.IconLocation = str(target)
        link.Description = "WinGenius — open these files in the toolbox"
        link.save()
        return
    except Exception as exc:
        ctx.log(f"pywin32 shortcut failed ({exc}); trying PowerShell")
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(shortcut) + "');"
        "$s.TargetPath='" + str(target) + "';"
        "$s.Arguments='" + arguments.replace("'", "''") + "';"
        "$s.WorkingDirectory='" + str(target.parent) + "';"
        "$s.IconLocation='" + str(target) + "';"
        "$s.Description='WinGenius';"
        "$s.Save()"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise ToolError(f"PowerShell shortcut creation failed: {result.stderr[:400]}")
