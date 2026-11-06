"""Network intelligence: DNS, HTTP, LAN scan, Wi-Fi, ports, WHOIS, hosts file."""
from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_INT, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size
from ._helpers import require, validate_host

IS_WINDOWS = sys.platform.startswith("win")

# A hosts-file entry must be a bare domain: no newline (which would inject a
# second mapping), no spaces, no URL, no wildcard.
_DOMAIN_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")


def valid_domain(value: str) -> bool:
    """True only for a syntactically safe, single hostname."""
    value = str(value or "").strip()
    return bool(value) and len(value) <= 253 and _DOMAIN_RE.fullmatch(value) is not None


@tool(
    id="dns_lookup", category="network", name="جست‌وجوی DNS", name_en="DNS lookup",
    desc="آدرس IP دامنه، رکوردهای MX/NS/TXT و مسیر معکوس IP را پیدا می‌کند.",
    desc_en="Resolve a domain to IPs, read MX/NS/TXT records and reverse-resolve IPs.",
    icon="globe", order=100,
    keywords=("dns", "resolve", "mx record", "دامنه", "آی پی", "nslookup"),
    fields=[
        Field("host", K_TEXT, "دامنه یا IP", "Domain or IP", required=True, placeholder="example.com"),
        Field("record", K_CHOICE, "نوع رکورد", "Record type", default="auto",
              choices=[("auto", "خودکار (A/AAAA)", "Auto (A/AAAA)"), ("mx", "MX (ایمیل)", "MX (mail)"),
                       ("ns", "NS (نیم‌سرور)", "NS (nameservers)"), ("txt", "TXT", "TXT"),
                       ("cname", "CNAME", "CNAME")]),
    ],
)
def dns_lookup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    host = str(v.get("host") or "").strip()
    if not host:
        raise ToolError(ctx.M("دامنه یا IP را وارد کنید.", "Enter a domain or IP."))
    host = validate_host(host, ctx)
    record = str(v.get("record") or "auto")
    lines: List[str] = []
    ctx.progress(1, 3, ctx.M("پاسخ DNS", "Resolving"))
    try:
        infos = socket.getaddrinfo(host, None)
        addresses = sorted({info[4][0] for info in infos})
        lines.append(ctx.M("آدرس‌ها:", "Addresses:"))
        lines.extend(f"  • {address}" for address in addresses)
    except socket.gaierror as exc:
        lines.append(ctx.M(f"دامنه حل نشد: {exc}", f"Could not resolve: {exc}"))
    if record == "auto":
        try:
            ctx.progress(2, 3, ctx.M("نام معکوس", "Reverse lookup"))
            lines.append("")
            lines.append(ctx.M("نام معکوس:", "Reverse name:"))
            lines.append(f"  • {socket.gethostbyaddr(host)[0]}")
        except Exception:
            pass
        return RunResult(message=ctx.M(f"DNS برای {host} بررسی شد.", f"DNS for {host} checked."),
                         details="\n".join(lines))
    try:
        dnspython = require("dns.resolver", "dnspython", ctx)
        ctx.progress(2, 3, ctx.M("خواندن رکوردها", "Reading records"))
        answers = dnspython.resolve(host, record.upper(), lifetime=6)
        lines.append(f"{record.upper()}:")
        for answer in answers:
            lines.append(f"  • {answer.to_text()}")
    except ToolError:
        code_helper = "nslookup"
        cmd = [code_helper, "-type=" + record, host] if IS_WINDOWS else [code_helper, "-type=" + record, host]
        try:
            output = subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace").stdout
            lines.append(output.strip())
        except Exception as exc:
            lines.append(ctx.M("نصب dnspython پیشنهاد می‌شود: pip install dnspython",
                               "Install dnspython for full records: pip install dnspython") + f"\n{exc}")
    return RunResult(message=ctx.M(f"رکوردهای {record.upper()} برای {host}", f"{record.upper()} records for {host}"),
                     details="\n".join(lines))


@tool(
    id="http_request", category="network", name="درخواست HTTP", name_en="HTTP request",
    desc="یک درخواست GET/HEAD/POST می‌فرستد و کد وضعیت، هدرها و بخشی از پاسخ را نشان می‌دهد.",
    desc_en="Send a GET/HEAD/POST request and show status, headers and a body preview.",
    icon="cloud", order=101,
    keywords=("http", "api test", "curl", "درخواست وب", "تست api"),
    fields=[
        Field("url", K_TEXT, "آدرس", "URL", required=True, placeholder="https://api.example.com/health"),
        Field("method", K_CHOICE, "متد", "Method", default="GET",
              choices=[("GET", "GET", "GET"), ("HEAD", "HEAD", "HEAD"), ("POST", "POST", "POST")]),
        Field("body", K_TEXT, "بدنه (برای POST)", "Body (for POST)", placeholder='{"key": "value"}'),
        Field("timeout", K_INT, "مهلت (ثانیه)", "Timeout (seconds)", default=15, minimum=1, maximum=120),
    ],
)
def http_request(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    requests = require("requests", "requests", ctx)
    url = str(v.get("url") or "").strip()
    if not url:
        raise ToolError(ctx.M("آدرس را وارد کنید.", "Enter a URL."))
    if "://" in url and not url.lower().startswith(("http://", "https://")):
        raise ToolError(ctx.M("فقط پروتکل‌های http و https مجازند.", "Only http:// and https:// URLs are allowed."))
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    method = str(v.get("method") or "GET").upper()
    if method not in ("GET", "HEAD", "POST"):
        method = "GET"
    ctx.progress(1, 2, ctx.M("ارسال درخواست", "Sending request"))
    started = time.time()
    max_body_bytes = 10 * 1024 * 1024
    buf = bytearray()
    truncated = False
    try:
        with requests.request(
            method, url, data=(str(v.get("body") or "") or None),
            timeout=max(1, int(v.get("timeout") or 15)),
            headers={"User-Agent": "WinGenius/7.1"},
            stream=True,
        ) as response:
            status_code = response.status_code
            reason = response.reason
            final_url = response.url
            headers_list = list(response.headers.items())[:25]
            encoding = response.encoding or "utf-8"
            if method != "HEAD":
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    remaining = max_body_bytes - len(buf)
                    if len(chunk) > remaining:
                        buf.extend(chunk[:remaining])
                        truncated = True
                        break
                    buf.extend(chunk)
    except Exception as exc:
        raise ToolError(ctx.M(f"درخواست ناموفق بود:\n{exc}", f"Request failed:\n{exc}"))
    elapsed = (time.time() - started) * 1000
    ctx.progress(2, 2, "")
    size_label = human_size(len(buf)) + ("+" if truncated else "")
    lines = [f"{method} {final_url}",
             f"{ctx.M('کد وضعیت', 'Status')}: {status_code} {reason}",
             f"{ctx.M('زمان', 'Time')}: {elapsed:.0f} ms",
             f"{ctx.M('حجم', 'Size')}: {size_label}",
             "", ctx.M("هدرها:", "Headers:")]
    lines.extend(f"  {key}: {value}" for key, value in headers_list)
    if method != "HEAD":
        preview = bytes(buf[:8192]).decode(encoding, errors="replace")[:1200]
        lines += ["", ctx.M("بخشی از پاسخ:", "Body preview:"), preview]
    return RunResult(
        message=ctx.M(f"{status_code} {reason} — {elapsed:.0f} میلی‌ثانیه",
                      f"{status_code} {reason} — {elapsed:.0f} ms"),
        details="\n".join(lines))


@tool(
    id="lan_scan", category="network", name="اسکن شبکه‌ی محلی", name_en="Scan local network",
    desc="دستگاه‌های زنده‌ی شبکه‌ی محلی را پیدا می‌کند (IP، نام، تأخیر) و آدرس روتر را می‌گوید.",
    desc_en="Discover live devices on your LAN (IP, name, latency) and find the router.",
    icon="radar", order=102,
    keywords=("lan scan", "devices", "شبکه محلی", "دستگاه‌های شبکه", "روتر"),
    fields=[
        Field("subnet", K_TEXT, "زیرشبکه (خالی = خودکار)", "Subnet (blank = auto)",
              placeholder="192.168.1"),
        Field("timeout", K_INT, "مهلت هر پینگ (میلی‌ثانیه)", "Ping timeout (ms)", default=400,
              minimum=100, maximum=3000, step=100),
    ],
)
def lan_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    prefix = str(v.get("subnet") or "").strip()
    if not prefix:
        candidates: List[str] = []
        for _name, addresses in (psutil.net_if_addrs() or {}).items():
            for address in addresses:
                if address.family == socket.AF_INET and not address.address.startswith("127."):
                    candidates.append(address.address)
        if not candidates:
            raise ToolError(ctx.M("شبکه‌ی فعالی پیدا نشد.", "No active network found."))
        prefix = ".".join(candidates[0].split(".")[:3])
    prefix = prefix.split("/")[0].strip().rstrip(".")
    parts = prefix.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        parts = parts[:3]
    if len(parts) not in (2, 3) or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise ToolError(ctx.M(
            "زیرشبکه باید اعداد معتبر IPv4 مثل 192.168.1 باشد.",
            "Subnet prefix must be valid IPv4 octets such as 192.168.1.",
        ))
    prefix = ".".join(str(int(p)) for p in parts)
    timeout_ms = max(100, int(v.get("timeout") or 400))
    hosts = [f"{prefix}.{index}" for index in range(1, 255)]

    def ping(host: str) -> Optional[Tuple[str, float, str]]:
        if IS_WINDOWS:
            cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
        started = time.time()
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=max(1.5, timeout_ms / 1000 + 1),
                errors="replace",
                creationflags=(0x08000000 if IS_WINDOWS else 0))
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        latency = (time.time() - started) * 1000
        name = host
        try:
            name = socket.gethostbyaddr(host)[0]
        except Exception:
            pass
        return host, latency, name

    live: List[Tuple[str, float, str]] = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        for result in pool.map(ping, hosts):
            done += 1
            if done % 16 == 0:
                ctx.progress(done, len(hosts), ctx.M(f"اسکن {done}/{len(hosts)}", f"Scanning {done}/{len(hosts)}"))
            if result:
                live.append(result)
    ctx.progress(len(hosts), len(hosts), "")
    live.sort(key=lambda item: tuple(int(part) for part in item[0].split(".")))
    lines = [f"{ip:<16}{latency:6.0f} ms   {name}" for ip, latency, name in live]
    router = f"{prefix}.1"
    note = ctx.M(f"\nروتر احتمالی: {router}", f"\nLikely router: {router}")
    return RunResult(
        message=ctx.M(f"{len(live)} دستگاه زنده در {prefix}.0/24 پیدا شد.",
                      f"{len(live)} live device(s) on {prefix}.0/24") + note,
        details="\n".join(lines) if lines else ctx.M("هیچ دستگاهی پاسخ نداد.", "No device answered."),
    )


@tool(
    id="wifi_scan", category="network", name="شبکه‌های وای‌فای اطراف", name_en="Nearby Wi-Fi networks",
    desc="شبکه‌های وای‌فای اطراف، قدرت سیگنال، کانال و نوع رمزنگاری را فهرست می‌کند.",
    desc_en="List nearby Wi-Fi networks with signal strength, channel and encryption.",
    icon="wifi", order=103,
    keywords=("wifi scan", "networks", "وای فای اطراف", "سیگنال", "کانال"),
    fields=[
        Field("save_profile", K_BOOL, "نمایش پروفایل‌های ذخیره‌شده هم", "Also show saved profiles",
              default=False),
    ],
)
def wifi_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if IS_WINDOWS:
        output = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=30, errors="replace",
            creationflags=0x08000000).stdout
    elif Path("/usr/bin/nmcli").exists() or Path("/bin/nmcli").exists():
        completed = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,CHAN,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=30, errors="replace")
        code, output = completed.returncode, completed.stdout
        if code == 0:
            rows = [ctx.M("سیگنال  کانال  امنیت  SSID", "SIGNAL  CHAN  SEC  SSID")]
            for line in output.splitlines():
                parts = line.split(":")
                if len(parts) >= 4:
                    rows.append(f"{parts[1]:>6}%  {parts[2]:>5}  {parts[3]:<14} {parts[0]}")
            return RunResult(message=ctx.M(f"{len(rows) - 1} شبکه پیدا شد.", f"{len(rows) - 1} network(s) found."),
                             details="\n".join(rows))
    else:
        raise ToolError(ctx.M("اسکن وای‌فای روی این سیستم پشتیبانی نمی‌شود.",
                              "Wi-Fi scanning is not supported on this system."))
    networks: List[str] = []
    current: Dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r"^(SSID|Authentication|Encryption|Signal|Channel|BSSID)\s*:\s*(.*)$", stripped)
        if match:
            key, value = match.group(1), match.group(2).strip()
            if key == "SSID":
                if current:
                    networks.append(self_describe(current, ctx))
                current = {"SSID": value}
            else:
                current[key] = value
        elif stripped.startswith("SSID") and ":" in stripped:
            value = stripped.split(":", 1)[1].strip()
            if current:
                networks.append(self_describe(current, ctx))
            current = {"SSID": value}
    if current:
        networks.append(self_describe(current, ctx))
    networks = [line for line in networks if line.strip()]
    if v.get("save_profile"):
        try:
            saved = subprocess.run(["netsh", "wlan", "show", "profiles"], capture_output=True, text=True,
                                   timeout=20, errors="replace", creationflags=0x08000000).stdout
            networks += ["", ctx.M("پروفایل‌های ذخیره‌شده:", "Saved profiles:"),
                         saved.strip()]
        except Exception:
            pass
    return RunResult(
        message=ctx.M(f"{len(networks)} شبکه پیدا شد.", f"{len(networks)} network(s) found."),
        details="\n".join(networks))


def self_describe(info: Dict[str, str], ctx: RunContext) -> str:
    ssid = info.get("SSID") or ctx.M("(بدون نام)", "(hidden)")
    signal = info.get("Signal", "")
    channel = info.get("Channel", "")
    auth = info.get("Authentication", "")
    ssid_display = ssid if len(ssid) <= 28 else ssid[:28] + "…"
    return (f"{ssid_display:<30} "
            f"{ctx.M('سیگنال', 'signal')}: {signal:<6} "
            f"{ctx.M('کانال', 'ch')}: {channel:<4} {auth}")


@tool(
    id="port_listen", category="network", name="پورت‌های باز سیستم", name_en="Listening ports",
    desc="پورت‌های باز و برنامه‌ی صاحب هر پورت را نشان می‌دهد (تشخیص نفوذ و اشکال‌زدایی).",
    desc_en="Show listening ports and the program owning each one.",
    icon="plug", order=104,
    keywords=("listening ports", "netstat", "پورت باز", "پورت در حال گوش دادن"),
    fields=[
        Field("filter", K_TEXT, "فیلتر پورت یا برنامه", "Filter by port or program"),
        Field("include_remote", K_BOOL, "نمایش اتصال‌های خارجی", "Include outbound connections",
              default=False),
    ],
)
def port_listen(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    psutil = require("psutil", "psutil", ctx)
    want_remote = bool(v.get("include_remote", False))
    needle = str(v.get("filter") or "").strip().lower()
    rows: List[str] = []
    seen = set()
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:
        raise ToolError(ctx.M(f"فهرست اتصال‌ها خوانده نشد (شاید نیازمند مدیر باشد): {exc}",
                              f"Could not read connections (admin may be required): {exc}"))
    for connection in connections:
        if not want_remote and connection.status != psutil.CONN_LISTEN:
            continue
        address = connection.laddr
        port = getattr(address, "port", None) if address else None
        ip = getattr(address, "ip", "") if address else ""
        name = ""
        if connection.pid:
            try:
                name = psutil.Process(connection.pid).name()
            except Exception:
                name = f"pid {connection.pid}"
        line = f"{connection.status:<12} {ip:<16}:{port:<6} {name}"
        key = (ip, port, name, connection.status)
        if key in seen:
            continue
        seen.add(key)
        if needle and needle not in line.lower():
            continue
        if want_remote and connection.raddr:
            remote = connection.raddr
            line += f"  →  {getattr(remote, 'ip', '')}:{getattr(remote, 'port', '')}"
        rows.append(line)
    return RunResult(
        message=ctx.M(f"{len(rows)} ردیف اتصال/پورت.", f"{len(rows)} connection/port row(s)."),
        details="\n".join(sorted(rows)[:400]))


@tool(
    id="whois_lookup", category="network", name="اطلاعات WHOIS دامنه", name_en="WHOIS lookup",
    desc="ثبت‌کننده، تاریخ انقضا و سرورهای نام یک دامنه را از WHOIS می‌خواند.",
    desc_en="Read registrar, expiry and nameservers for a domain via WHOIS.",
    icon="info", order=105,
    keywords=("whois", "domain owner", "مالک دامنه", "تاریخ انقضا دامنه"),
    fields=[
        Field("domain", K_TEXT, "دامنه", "Domain", required=True, placeholder="example.com"),
    ],
)
def whois_lookup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import ipaddress

    domain = str(v.get("domain") or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    if "." not in domain or not valid_domain(domain):
        raise ToolError(ctx.M("دامنه‌ی کامل و معتبر وارد کنید (مثل example.com).",
                              "Enter a valid full domain (e.g. example.com)."))

    def _safe_whois_host(server: str) -> str:
        clean = server.strip().lower().rstrip(".")
        if not valid_domain(clean):
            raise ValueError(f"invalid WHOIS server: {server}")
        infos = socket.getaddrinfo(clean, 43, proto=socket.IPPROTO_TCP)
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
                raise ValueError(f"refusing non-public WHOIS referral: {clean}")
        return clean

    def query(server: str, text: str) -> str:
        safe_srv = _safe_whois_host(server)
        with socket.create_connection((safe_srv, 43), timeout=12) as sock:
            sock.sendall((text + "\r\n").encode("utf-8"))
            chunks = []
            total = 0
            while total < 262_144:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                total += len(data)
            return b"".join(chunks).decode("utf-8", errors="replace")

    ctx.progress(1, 3, ctx.M("پرسش از سرور WHOIS", "Asking the WHOIS server"))
    tld = domain.rsplit(".", 1)[-1]
    try:
        iana = query("whois.iana.org", tld)
        match = re.search(r"refer:\s*(\S+)", iana)
        server = match.group(1) if match else "whois.iana.org"
        ctx.progress(2, 3, f"whois: {server}")
        raw = query(server, domain)
    except Exception as exc:
        raise ToolError(ctx.M(f"اتصال به WHOIS ممکن نشد (شاید اینترنت یا فایروال مانع است): {exc}",
                              f"Could not reach WHOIS (check the internet/firewall): {exc}"))
    ctx.progress(3, 3, "")
    keep = ("domain", "registrar", "creation", "created", "expiry", "expiration", "updated",
            "name server", "nameserver", "status", "registrant", "org")
    lines = [line for line in raw.splitlines()
             if line.strip() and any(word in line.lower() for word in keep)]
    trimmed = lines[:60] or raw.splitlines()[:40]
    return RunResult(message=ctx.M(f"اطلاعات WHOIS برای {domain}", f"WHOIS for {domain}"),
                     details="\n".join(trimmed))


@tool(
    id="hosts_file", category="network", name="ویرایش فایل hosts", name_en="Edit hosts file",
    desc="فایل hosts را می‌خواند، یک دامنه را مسدود می‌کند یا مسدودی را برمی‌دارد (با پشتیبان‌گیری).",
    desc_en="Read the hosts file, block a domain or undo a block (with a backup first).",
    icon="shield", order=106, admin=True, danger=True,
    keywords=("hosts", "block domain", "مسدود کردن سایت", "فایل هاست"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="view",
              choices=[("view", "نمایش", "View"), ("block", "مسدود کردن دامنه", "Block a domain"),
                       ("unblock", "رفع مسدودی", "Unblock"), ("open", "باز کردن با Notepad", "Open in Notepad")]),
        Field("domain", K_TEXT, "دامنه", "Domain", placeholder="ads.example.com",
              placeholder_en="ads.example.com",
              help="دامنه‌ی خام بدون http؛ مثل ads.example.com. ابتدا از فایل hosts پشتیبان گرفته می‌شود.",
              help_en="Bare domain, no http:// — e.g. ads.example.com. A backup of the hosts file is made first.",
              visible_when=("action", ("block", "unblock"))),
    ],
)
def hosts_file(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    hosts = Path(r"C:\Windows\System32\drivers\etc\hosts") if IS_WINDOWS else Path("/etc/hosts")
    if not hosts.exists():
        raise ToolError(ctx.M("فایل hosts پیدا نشد.", "The hosts file was not found."))
    action = str(v.get("action") or "view")
    if action == "open":
        if IS_WINDOWS:
            subprocess.Popen(["notepad.exe", str(hosts)])
            return RunResult(message=ctx.M("فایل hosts در Notepad باز شد.", "hosts opened in Notepad."))
        raise ToolError(ctx.M("این عملیات مخصوص ویندوز است.", "This action is Windows-only."))
    text = hosts.read_text(encoding="utf-8", errors="replace")
    if action == "view":
        entries = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        return RunResult(
            message=ctx.M(f"{len(entries)} ردیف فعال در hosts.", f"{len(entries)} active hosts entries."),
            details="\n".join(entries) or ctx.M("فایل hosts پیش‌فرض و خالی است.", "The hosts file is default/empty."),
        )
    domain = str(v.get("domain") or "").strip()
    if not domain:
        raise ToolError(ctx.M("دامنه را وارد کنید.", "Enter the domain."))
    if not valid_domain(domain):
        raise ToolError(ctx.M(
            "نام دامنه معتبر نیست: فقط حروف، عدد، خط تیره و نقطه؛ بدون فاصله، "
            "خط جدید یا آدرس کامل.",
            "Invalid domain: letters, digits, hyphens and dots only; no whitespace, "
            "newlines or full URLs."))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = hosts.with_name(hosts.name + f".wingenius-{stamp}.bak")
    try:
        backup.write_text(text, encoding="utf-8")
        # keep a short history instead of overwriting the only backup each run
        old = sorted(hosts.parent.glob(hosts.name + ".wingenius-*.bak"))
        for stale in old[:-5]:
            stale.unlink(missing_ok=True)
    except Exception as exc:
        raise ToolError(ctx.M(f"ساخت پشتیبان ممکن نشد (دسترسی مدیر لازم است): {exc}",
                              f"Could not create a backup (admin rights needed): {exc}"))
    lines = text.splitlines()
    if action == "block":
        marker = f"0.0.0.0 {domain}"
        if any(marker in line for line in lines):
            return RunResult(message=ctx.M(f"{domain} از قبل مسدود است.", f"{domain} is already blocked."))
        lines.append(marker)
        lines.append(f"0.0.0.0 www.{domain}" if not domain.startswith("www.") else marker)
    else:
        # Only remove exact hostnames, not any line that happens to contain the
        # substring (e.g. "bank.com" must not delete "notbank.com").
        lines = [line for line in lines
                 if not (line.strip() and not line.strip().startswith("#")
                         and domain.lower() in [token.lower() for token in line.split()[1:]])]
    try:
        hosts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:
        raise ToolError(ctx.M(f"نوشتن فایل hosts ممکن نشد (برنامه را با دسترسی مدیر اجرا کنید): {exc}",
                              f"Could not write the hosts file (run as administrator): {exc}"))
    if IS_WINDOWS:
        # `creationflags` is Windows-only and raises ValueError on POSIX.
        try:
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=20,
                           creationflags=0x08000000)
        except OSError:
            pass
    return RunResult(
        message=ctx.M(f"{'مسدود شد' if action == 'block' else 'مسدودی برداشته شد'}: {domain}\nپشتیبان: {backup}",
                      f"{'Blocked' if action == 'block' else 'Unblocked'}: {domain}\nBackup: {backup}"),
        outputs=[str(backup)],
    )
