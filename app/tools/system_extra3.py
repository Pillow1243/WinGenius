"""System extras: see who is holding a port — and take it back."""
from __future__ import annotations

from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import K_BOOL, K_INT, Field, RunContext, RunResult, ToolError


def _connections(ctx: RunContext):
    try:
        import psutil
    except Exception:
        raise ToolError(ctx.M("کتابخانه‌ی psutil لازم است: pip install psutil",
                              "psutil is required: pip install psutil"))
    try:
        return psutil.net_connections(kind="inet")
    except PermissionError:
        raise ToolError(ctx.M(
            "دسترسی به فهرست اتصال‌ها ممکن نشد؛ برنامه را با دسترسی مدیر اجرا کنید.",
            "Reading the connection table was denied; run WinGenius as administrator."))
    except Exception as exc:                           # noqa: BLE001 - platform differences
        raise ToolError(ctx.M(f"فهرست اتصال‌ها خوانده نشد: {exc}",
                              f"Could not read the connection table: {exc}"))


@tool(
    id="port_release", category="system", name="آزادکردن پورت اشغال‌شده",
    name_en="Free a busy port",
    desc="می‌گوید چه برنامه‌ای پورت را گرفته و در صورت تأیید، همان فرایند را می‌بندد (پورت‌های شلوغ، دشمن توسعه‌دهنده‌اند).",
    desc_en="Shows which program holds a port and, on request, ends that process — the classic dev-machine annoyance.",
    icon="plug", order=60, danger=True,
    keywords=("port", "kill", "process", "busy", "eaddrinuse", "پورت", "اشغال", "بستن", "فرایند"),
    fields=[
        Field("port", K_INT, "پورت", "Port", default=8000, minimum=1, maximum=65535),
        Field("kill", K_BOOL, "فرایند را ببند", "End the process", default=False,
              help="پیش‌فرض فقط گزارش می‌دهد؛ برای بستن، این گزینه را روشن کنید.",
              help_en="The default only reports; switch this on to actually end the process."),
    ],
)
def port_release(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    port = int(v.get("port") or 0)
    if not 1 <= port <= 65535:
        raise ToolError(ctx.M("شماره‌ی پورت باید بین ۱ و ۶۵۵۳۵ باشد.",
                              "The port number must be between 1 and 65535."))
    connections = _connections(ctx)
    holders = [conn for conn in connections if getattr(conn, "laddr", None)
               and conn.laddr.port == port and conn.pid]
    if not holders:
        return RunResult(
            message=ctx.M(f"پورت {port} آزاد است؛ هیچ برنامه‌ای آن را نگرفته.",
                          f"Port {port} is free — nothing is listening."),
            details=ctx.M("اگر برنامه‌ای خطای «پورت در حال استفاده» می‌دهد، "
                          "شاید روی IPv6 یا پورت دیگری باشد.",
                          "If an app still reports 'address in use', check IPv6 or another port."))
    import psutil

    rows: List[str] = []
    seen = set()
    for conn in holders:
        pid = conn.pid
        if pid in seen:
            continue
        seen.add(pid)
        try:
            process = psutil.Process(pid)
            name = process.name()
            exe = process.exe()
        except Exception:                              # noqa: BLE001 - process vanished
            name, exe = "?", ""
        rows.append(f"pid {pid:<7} {name:26} {conn.status:12} {exe}")
    if not v.get("kill"):
        details = "\n".join(rows) + "\n\n" + ctx.M(
            "برای بستن این فرایند، گزینه‌ی «فرایند را ببند» را روشن کنید.",
            "Switch on 'End the process' to close it.")
        return RunResult(
            message=ctx.M(f"پورت {port} در دست {len(rows)} فرایند است.",
                          f"Port {port} is held by {len(rows)} process(es)."),
            details=details)
    stopped: List[str] = []
    failures = 0
    for pid in seen:
        try:
            process = psutil.Process(pid)
            name = process.name()
            process.terminate()
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
            stopped.append(f"{name} (pid {pid})")
        except Exception as exc:                       # noqa: BLE001 - already gone/denied
            failures += 1
            stopped.append(f"pid {pid}: {exc}")
    note = ""
    if failures:
        note = ctx.M(
            "\nاگر فرایند برنگشت، WinGenius را با دسترسی مدیر اجرا کنید.",
            "\nIf a process survived, run WinGenius as administrator.")
    return RunResult(
        message=ctx.M(f"{len(stopped)} فرایند بسته شد؛ پورت {port} آزاد شد.",
                      f"{len(stopped)} process(es) ended; port {port} released."),
        details="\n".join(stopped + rows) + note)
