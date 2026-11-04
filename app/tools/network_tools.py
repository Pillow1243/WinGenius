"""Network tools: info, ping/traceroute, DNS, port scan, Wi-Fi passwords, download."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, run_cmd, safe_filename, unique_path
from ._helpers import (IS_WINDOWS, ensure_dir, need_windows, out_dir, require, save_target,
                       validate_host)

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB safety ceiling


# -------------------------------------------------------------------- network info
@tool(
    id="network_info", category="network", name="اطلاعات شبکه", name_en="Network info",
    desc="آی‌پی، مک‌آدرس، گیت‌وی، DNS و وضعیت اتصال هر آداپتور را نشان می‌دهد.",
    desc_en="Show IP, MAC, gateway, DNS and link status for every adapter.",
    icon="globe", order=10, keywords=("ip", "network info", "آی پی", "مک آدرس", "dns"),
    fields=[],
)
def network_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    rows: List[str] = []
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    for name, entries in addrs.items():
        stat = stats.get(name)
        state = ctx.M("وصل", "up") if (stat and stat.isup) else ctx.M("قطع", "down")
        rows.append(f"■ {name}  [{state}]")
        for entry in entries:
            family = str(entry.family)
            if "AF_INET6" in family:
                continue
            label = "MAC" if "AF_LINK" in family or "AF_PACKET" in family else "IPv4"
            rows.append(f"    {label:<5}: {entry.address}"
                        + (f" / {entry.netmask}" if entry.netmask else ""))
    rows.append("")
    if IS_WINDOWS:
        code, out, _ = run_cmd(["ipconfig", "/all"], timeout=30)
        for line in out.splitlines():
            if re.search(r"(Default Gateway|DNS Servers|پیش‌فرض|DNS)", line, re.I) or ":" in line and re.search(r"\d+\.\d+\.\d+\.\d+", line):
                if re.search(r"\d+\.\d+\.\d+\.\d+", line):
                    rows.append("  " + line.strip())
    else:
        code, out, _ = run_cmd(["ip", "route"], timeout=10)
        rows.append(out.strip()[:800])
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        rows.append("")
        rows.append(f"{ctx.M('نام دستگاه', 'Hostname')}: {hostname}   |   LAN IP: {local_ip}")
    except Exception:
        pass
    return RunResult(message=ctx.M("اطلاعات شبکه", "Network info"), details="\n".join(rows))


# ------------------------------------------------------------------ wifi passwords
@tool(
    id="wifi_passwords", category="network", name="رمز وای‌فای‌ها", name_en="Wi-Fi passwords",
    desc="رمز شبکه‌های وای‌فای ذخیره‌شده روی این ویندوز را نشان می‌دهد (برای بازیابی رمز خودتان).",
    desc_en="Show saved Wi-Fi passwords on this PC (recover your own networks).",
    icon="wifi", order=20, windows_only=True, admin=False,
    keywords=("wifi password", "رمز وای فای", "netsh", "بازیابی رمز"),
    fields=[],
)
def wifi_passwords(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    code, out, err = run_cmd(["netsh", "wlan", "show", "profiles"], timeout=30)
    if code != 0:
        raise ToolError(ctx.M(f"خواندن پروفایل‌های وای‌فای ناموفق بود:\n{err or out}",
                              f"Reading Wi-Fi profiles failed:\n{err or out}"))
    names = re.findall(r":\s*(.+)\s*$", out, re.M)
    profiles = [n.strip() for n in names if n.strip() and not n.strip().lower().startswith("user profile")]
    if not profiles:
        return RunResult(message=ctx.M("هیچ شبکه‌ی ذخیره‌شده‌ای پیدا نشد.", "No saved Wi-Fi network found."))
    rows: List[str] = []
    for index, name in enumerate(profiles):
        ctx.progress(index, len(profiles), name)
        code, out, _ = run_cmd(["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"], timeout=20)
        password = ""
        match = re.search(r"(?:Key Content|محتوای کلید)\s*:\s*(.*)", out)
        if match:
            password = match.group(1).strip()
        auth = re.search(r"(?:Authentication|احراز هویت)\s*:\s*(.*)", out)
        rows.append(f"{name}\n    {ctx.M('رمز', 'password')}: {password or ctx.M('(ندارد / باز)', '(none / open)')}"
                    + (f"\n    {auth.group(1).strip()}" if auth else ""))
    return RunResult(
        message=ctx.M(f"{len(profiles)} شبکه‌ی ذخیره‌شده پیدا شد.",
                      f"{len(profiles)} saved network(s) found."),
        details="\n".join(rows),
    )


# --------------------------------------------------------------------- net diag
@tool(
    id="net_diag", category="network", name="عیب‌یابی شبکه", name_en="Network diagnostics",
    desc="پینگ، مسیر‌یابی (tracert) و رزولوشن DNS را برای هر آدرس اجرا می‌کند.",
    desc_en="Run ping, traceroute and DNS resolution for any host.",
    icon="speed", order=30, keywords=("ping", "traceroute", "nslookup", "پینگ", "مسیریابی"),
    fields=[
        Field("host", K_TEXT, "آدرس / دامنه", "Host / domain", default="8.8.8.8", required=True),
        Field("mode", K_CHOICE, "نوع تست", "Test", default="ping",
              choices=[("ping", "پینگ", "Ping"), ("trace", "مسیر‌یابی", "Traceroute"),
                       ("dns", "رزولوشن DNS", "DNS lookup")]),
        Field("count", K_INT, "تعداد پینگ", "Ping count", default=6, minimum=1, maximum=50,
              visible_when=("mode", "ping")),
    ],
)
def net_diag(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    host = validate_host(str(v.get("host") or "").strip(), ctx)
    mode = str(v.get("mode") or "ping")
    if mode == "dns":
        started = time.time()
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ToolError(ctx.M(f"رزولوشن ناموفق بود: {exc}", f"DNS resolution failed: {exc}"))
        addresses = sorted({info[4][0] for info in infos})
        elapsed = (time.time() - started) * 1000
        return RunResult(
            message=ctx.M(f"{host} → {len(addresses)} آدرس در {elapsed:.0f} میلی‌ثانیه",
                          f"{host} → {len(addresses)} address(es) in {elapsed:.0f} ms"),
            details="\n".join(addresses),
        )
    if mode == "trace":
        cmd = ["tracert", "-d", "-w", "1000", host] if IS_WINDOWS else ["traceroute", "-n", "-w", "1", host]
        ctx.progress(0, 0, ctx.M("در حال مسیریابی…", "Tracing route…"))
        code, out, err = run_cmd(cmd, timeout=120)
        return RunResult(message=ctx.M(f"مسیر تا {host}", f"Route to {host}"),
                         details=(out or err).strip()[:8000])
    count = int(v.get("count") or 6)
    cmd = ["ping", "-n", str(count), host] if IS_WINDOWS else ["ping", "-c", str(count), host]
    code, out, err = run_cmd(cmd, timeout=90)
    body = (out or err).strip()
    summary = ""
    match = re.search(r"(Average|میانگین)[^\n]*", body)
    if match:
        summary = match.group(0)
    if code != 0 and not body:
        raise ToolError(ctx.M("پینگ ناموفق بود.", "Ping failed."))
    return RunResult(message=ctx.M(f"پینگ {host}", f"Ping {host}") + (f" — {summary}" if summary else ""),
                     details=body[:6000])


# --------------------------------------------------------------------- port scan
@tool(
    id="port_scan", category="network", name="اسکن پورت", name_en="Port scanner",
    desc="پورت‌های باز یک دستگاه در شبکه‌ی محلی را بررسی می‌کند (عیب‌یابی سرور/دوربین).",
    desc_en="Check open TCP ports on a local host (server/camera troubleshooting).",
    icon="scan", order=40, keywords=("port scan", "پورت باز", "اسکن", "nmap"),
    fields=[
        Field("host", K_TEXT, "آدرس", "Host", default="192.168.1.1", required=True),
        Field("start", K_INT, "از پورت", "From port", default=1, minimum=1, maximum=65535),
        Field("end", K_INT, "تا پورت", "To port", default=1024, minimum=1, maximum=65535),
        Field("timeout", K_INT, "زمان انتظار (ms)", "Timeout (ms)", default=250, minimum=50, maximum=3000),
    ],
)
def port_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    host = str(v.get("host") or "").strip()
    host = validate_host(host, ctx)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            host = socket.gethostbyname(host)
        except socket.gaierror as exc:
            raise ToolError(ctx.M(f"آدرس نامعتبر است: {exc}", f"Invalid host: {exc}"))
    start = int(v.get("start") or 1)
    end = int(v.get("end") or 1024)
    if end < start:
        start, end = end, start
    timeout = max(0.05, int(v.get("timeout") or 250) / 1000.0)
    ports = list(range(start, end + 1))
    if len(ports) > 20000:
        raise ToolError(ctx.M("محدوده‌ی پورت خیلی بزرگ است (حداکثر ۲۰۰۰۰).",
                              "Port range too large (max 20000)."))
    open_ports: List[int] = []
    lock = threading.Lock()
    counter = {"done": 0}

    def worker(port: int) -> None:
        if ctx.cancelled:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((host, port)) == 0:
                with lock:
                    open_ports.append(port)
        except OSError:
            pass
        finally:
            sock.close()
        with lock:
            counter["done"] += 1
            if counter["done"] % 40 == 0:
                ctx.progress(counter["done"], len(ports), f"{counter['done']}/{len(ports)}")

    threads: List[threading.Thread] = []
    for port in ports:
        thread = threading.Thread(target=worker, args=(port,), daemon=True)
        thread.start()
        threads.append(thread)
        if len(threads) >= 120:
            for t in threads:
                t.join(timeout=5)
            threads = []
            ctx.progress(counter["done"], len(ports), "")
    for t in threads:
        t.join(timeout=5)
    open_ports.sort()
    names = {22: "SSH", 21: "FTP", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3",
             135: "RPC", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS", 445: "SMB", 3389: "RDP",
             5900: "VNC", 8080: "HTTP-alt", 8443: "HTTPS-alt"}
    if not open_ports:
        return RunResult(message=ctx.M(f"هیچ پورت بازی روی {host} پیدا نشد.",
                                       f"No open ports found on {host}."))
    rows = [f"{port:<6} {names.get(port, '')}" for port in open_ports]
    return RunResult(
        message=ctx.M(f"{len(open_ports)} پورت باز روی {host}:", f"{len(open_ports)} open port(s) on {host}:"),
        details="\n".join(rows),
    )


# ---------------------------------------------------------------------- dns flush
@tool(
    id="network_repair", category="network", name="تعمیر اتصال", name_en="Fix connection",
    desc="کش DNS را پاک و اتصال شبکه را تازه می‌کند (رفع بسیاری از مشکلات اینترنت).",
    desc_en="Flush DNS and renew the network connection (fixes many issues).",
    icon="wifi", order=50, windows_only=True, admin=True,
    keywords=("flush dns", "ipconfig", "تعمیر اینترنت", "renew ip"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="flush",
              choices=[("flush", "پاک کردن کش DNS", "Flush DNS cache"),
                       ("renew", "گرفتن IP جدید (release/renew)", "Release & renew IP"),
                       ("reset_winsock", "ریست Winsock (نیاز به ری‌استارت)", "Reset Winsock (needs reboot)")]),
    ],
)
def network_repair(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    need_windows(ctx)
    action = str(v.get("action") or "flush")
    outputs: List[str] = []
    if action == "flush":
        cmds = [["ipconfig", "/flushdns"]]
    elif action == "renew":
        cmds = [["ipconfig", "/release"], ["ipconfig", "/renew"]]
    else:
        cmds = [["netsh", "winsock", "reset"], ["netsh", "int", "ip", "reset"]]
    for index, cmd in enumerate(cmds):
        ctx.progress(index, len(cmds), " ".join(cmd))
        code, out, err = run_cmd(cmd, timeout=90)
        outputs.append(f"$ {' '.join(cmd)}\n{(out or err).strip()}")
    note = ""
    if action == "reset_winsock":
        note = ctx.M("\n\nبرای اعمال، سیستم را ری‌استارت کنید.", "\n\nReboot to apply.")
    return RunResult(message=ctx.M("عملیات شبکه انجام شد.", "Network operation done.") + note,
                     details="\n\n".join(outputs))


# ----------------------------------------------------------------- download file
@tool(
    id="download_file", category="network", name="دانلود فایل", name_en="Download file",
    desc="یک فایل را از اینترنت با نمایش پیشرفت دانلود می‌کند.",
    desc_en="Download a file with a progress indicator.",
    icon="download", order=60, keywords=("download", "دانلود", "url"),
    fields=[
        Field("url", K_TEXT, "آدرس فایل", "URL", required=True,
              placeholder="https://example.com/file.zip", placeholder_en="https://example.com/file.zip"),
        Field("out", K_SAVE, "ذخیره در", "Save as"),
        Field("verify", K_BOOL, "بررسی گواهی SSL", "Verify SSL certificate", default=True),
    ],
)
def download_file(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    requests = require("requests", "requests", ctx)
    url = str(v.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")) or any(c in url for c in ("\r", "\n", "\x00")):
        raise ToolError(ctx.M("آدرس باید با http:// یا https:// شروع شود.",
                              "URL must start with http:// or https://"))
    raw_name = Path(url.split("?")[0].rstrip("/")).name or "download.bin"
    name = safe_filename(raw_name, fallback="download.bin")
    dest = save_target(ctx, v.get("out", ""), name)
    try:
        with requests.get(url, stream=True, timeout=30, verify=bool(v.get("verify", True))) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_DOWNLOAD_BYTES:
                raise ToolError(ctx.M("حجم فایل بیش از حد مجاز (۲ گیگابایت) است.",
                                      "File exceeds the 2 GiB download safety limit."))
            done = 0
            with open(dest, "wb") as fh:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    done += len(chunk)
                    if done > MAX_DOWNLOAD_BYTES:
                        raise ToolError(ctx.M("حجم دانلود از سقف مجاز گذشت.",
                                              "Download exceeded the 2 GiB safety limit."))
                    fh.write(chunk)
                    ctx.progress(done, total, f"{human_size(done)} / {human_size(total) if total else '?'}")
    except ToolError:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise ToolError(ctx.M(f"دانلود ناموفق بود: {exc}", f"Download failed: {exc}"))
    return RunResult(message=ctx.M(f"دانلود کامل شد ({human_size(dest.stat().st_size)}).",
                                  f"Download complete ({human_size(dest.stat().st_size)})."),
                     outputs=[str(dest)], open_target=str(dest))


# -------------------------------------------------------------------- site check
@tool(
    id="site_check", category="network", name="بررسی سایت", name_en="Site status",
    desc="کد وضعیت HTTP و زمان پاسخ چند سایت را بررسی می‌کند.",
    desc_en="Check HTTP status and response time of websites.",
    icon="globe", order=70, keywords=("http status", "بررسی سایت", "ping site", "ssl"),
    fields=[
        Field("urls", K_TEXT, "آدرس‌ها (با کاما)", "URLs (comma separated)", required=True,
              default="https://google.com, https://github.com"),
    ],
)
def site_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    requests = require("requests", "requests", ctx)
    urls = [u.strip() for u in str(v.get("urls") or "").split(",") if u.strip()]
    if not urls:
        raise ToolError(ctx.M("آدرسی وارد نشد.", "No URL given."))
    rows: List[str] = []
    for index, url in enumerate(urls):
        ctx.progress(index, len(urls), url)
        if not url.lower().startswith("http"):
            url = "https://" + url
        started = time.time()
        try:
            with requests.get(url, timeout=15, allow_redirects=True, stream=True,
                              headers={"User-Agent": "WinGenius/1.0"}) as response:
                elapsed = (time.time() - started) * 1000
                rows.append(f"{response.status_code}  {elapsed:7.0f} ms  {url}")
        except Exception as exc:
            elapsed = (time.time() - started) * 1000
            rows.append(f"{'ERR':>3}  {elapsed:7.0f} ms  {url}  ({type(exc).__name__}: {exc})")
    return RunResult(message=ctx.M("وضعیت سایت‌ها", "Site status"), details="\n".join(rows))
