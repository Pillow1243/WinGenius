"""Network extras: TLS inspection, HTTP security headers, subnet math, staged timing."""
from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import K_INT, K_TEXT, K_BOOL, Field, RunContext, RunResult, ToolError
from ._helpers import validate_host


# ------------------------------------------------------------------ subnet math
@tool(
    id="subnet_calc", category="network", name="ماشین‌حساب زیرشبکه (CIDR)",
    name_en="Subnet calculator (CIDR)",
    desc="از یک CIDR شبکه، ماسک، بازه‌ی میزبان‌ها، تعداد میزبان و تقسیم به زیرشبکه‌ها را حساب می‌کند.",
    desc_en="From a CIDR: network, mask, host range, usable hosts and a clean subnet split.",
    icon="network", order=50,
    keywords=("subnet", "cidr", "netmask", "ip", "vlsm", "زیرشبکه", "ماسک", "آی‌پی", "تقسیم"),
    fields=[
        Field("cidr", K_TEXT, "آدرس یا CIDR", "Address or CIDR", required=True,
              placeholder="192.168.1.10/24"),
        Field("split_prefix", K_INT, "تقسیم به پیشوند", "Split into prefix", default=0,
              minimum=0, maximum=32,
              help="۰ یعنی تقسیم نکن؛ مثلاً ۲۶ شبکه‌ی /۲۴ را به چهار /۲۶ می‌شکند.",
              help_en="0 means no split; 26 turns a /24 into four /26 networks."),
        Field("limit", K_INT, "حداکثر تعداد ردیف", "Max rows", default=64, minimum=1, maximum=512),
    ],
)
def subnet_calc(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("cidr") or "").strip()
    if not raw:
        raise ToolError(ctx.M("یک آدرس مثل 192.168.1.0/24 بدهید.",
                              "Give an address such as 192.168.1.0/24."))
    try:
        if "/" in raw:
            network = ipaddress.ip_network(raw, strict=False)
        else:
            address = ipaddress.ip_address(raw)
            network = ipaddress.ip_network(f"{address}/{'128' if address.version == 6 else '32'}",
                                           strict=False)
    except ValueError as exc:
        raise ToolError(ctx.M(f"آدرس نامعتبر است: {exc}", f"Invalid address: {exc}"))
    hosts = list(network.hosts()) if network.num_addresses <= 4096 and network.version == 4 else []
    lines = [
        f"network      {network.network_address}/{network.prefixlen}",
        f"netmask      {network.netmask}",
        f"hostmask     {network.hostmask}",
        f"broadcast    {getattr(network, 'broadcast_address', '—')}",
        f"addresses    {network.num_addresses}",
        f"usable hosts {max(0, network.num_addresses - (2 if network.num_addresses > 2 else 0))}",
    ]
    if hosts:
        lines.append(f"host range   {hosts[0]} … {hosts[-1]}")
    flags = []
    if network.is_private:
        flags.append("private (RFC1918/ULA)")
    if network.is_loopback:
        flags.append("loopback")
    if network.is_multicast:
        flags.append("multicast")
    if network.is_link_local:
        flags.append("link-local")
    if network.is_reserved:
        flags.append("reserved")
    lines.append("flags        " + (", ".join(flags) if flags else "public"))
    split_prefix = int(v.get("split_prefix") or 0)
    subnets: List[str] = []
    if split_prefix and split_prefix > network.prefixlen:
        limit = int(v.get("limit") or 64)
        try:
            for index, subnet in enumerate(network.subnets(new_prefix=split_prefix)):
                if index >= limit:
                    subnets.append(f"… (+{2 ** (split_prefix - network.prefixlen) - limit} more)")
                    break
                first = subnet.network_address + (1 if subnet.num_addresses > 2 else 0)
                last = subnet.network_address + subnet.num_addresses - (2 if subnet.num_addresses > 2 else 1)
                subnets.append(f"{subnet}   hosts {first} … {last}")
        except ValueError as exc:
            raise ToolError(ctx.M(f"تقسیم ممکن نیست: {exc}", f"Cannot split: {exc}"))
    details = "\n".join(lines)
    if subnets:
        details += "\n\n" + ("زیرشبکه‌ها:" if ctx.lang != "en" else "Subnets:") + "\n" + "\n".join(subnets)
    return RunResult(
        message=ctx.M(
            f"{network} · {network.num_addresses} آدرس" +
            (f" · {len(subnets)} زیرشبکه" if subnets else ""),
            f"{network} · {network.num_addresses} addresses" +
            (f" · {len(subnets)} subnet(s)" if subnets else "")),
        details=details)


# ------------------------------------------------------------- TLS certificate
@tool(
    id="tls_certificate", category="network", name="گواهی SSL سایت",
    name_en="Website TLS certificate",
    desc="گواهی HTTPS یک سایت را می‌خواند: صادرکننده، دامنه‌ها، تاریخ انقضا و روزهای باقی‌مانده.",
    desc_en="Reads a site's HTTPS certificate: issuer, covered names, expiry and days remaining.",
    icon="lock", order=51,
    keywords=("ssl", "tls", "certificate", "https", "expiry", "گواهی", "ssl", "انقضا", "امنیت"),
    fields=[
        Field("host", K_TEXT, "دامنه", "Host", required=True, placeholder="example.com"),
        Field("port", K_INT, "پورت", "Port", default=443, minimum=1, maximum=65535),
    ],
)
def tls_certificate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    host = validate_host(str(v.get("host") or ""), ctx)
    port = int(v.get("port") or 443)
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                protocol = tls.version() or "?"
                cipher = tls.cipher()
    except ssl.SSLCertVerificationError as exc:
        raise ToolError(ctx.M(
            f"گواهی معتبر نیست: {exc.verify_message if hasattr(exc, 'verify_message') else exc}",
            f"The certificate is not trusted: {exc}"))
    except (socket.timeout, TimeoutError):
        raise ToolError(ctx.M(f"اتصال به {host}:{port} زمان‌بندی شد.",
                              f"Connecting to {host}:{port} timed out."))
    except OSError as exc:
        raise ToolError(ctx.M(f"اتصال برقرار نشد: {exc}", f"Connection failed: {exc}"))
    if not der:
        raise ToolError(ctx.M("سرور گواهی نفرستاد.", "The server sent no certificate."))
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        cert = x509.load_der_x509_certificate(der)
    except Exception as exc:                          # noqa: BLE001 - crypto missing/broken
        raise ToolError(ctx.M(f"گواهی خوانده نشد: {exc}", f"Cannot parse the certificate: {exc}"))
    not_after = cert.not_valid_after_utc
    not_before = cert.not_valid_before_utc
    now = time.time()
    days_left = (not_after.timestamp() - now) / 86400
    common = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    issuer_cn = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    issuer_org = cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = san.value.get_values_for_type(x509.DNSName)
        san_extension = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        ca = bool(san_extension.value.ca)
    except Exception:                                 # noqa: BLE001 - extension optional
        names, ca = [], False
    key = cert.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        key_text = f"RSA {key.key_size} bit"
    elif isinstance(key, ec.EllipticCurvePublicKey):
        key_text = f"EC {key.curve.name}"
    else:                                             # pragma: no cover - ed25519 etc.
        key_text = type(key).__name__
    minutes = days_left * 24 * 60
    if days_left < 0:
        state_fa, state_en = "منقضی شده", "EXPIRED"
    elif days_left < 14:
        state_fa, state_en = "بحرانی — به‌زودی منقضی می‌شود", "critical — expiring soon"
    elif days_left < 30:
        state_fa, state_en = "نزدیک انقضا", "expiring soon"
    else:
        state_fa, state_en = "سالم", "healthy"
    details = "\n".join([
        f"host            {host}:{port}",
        f"protocol        {protocol} · {cipher[0] if cipher else '?'}",
        f"subject CN      {common[0].value if common else '—'}",
        f"issuer          {(issuer_org[0].value + ' / ' if issuer_org else '')}"
        f"{issuer_cn[0].value if issuer_cn else '—'}",
        f"valid from      {not_before:%Y-%m-%d %H:%M UTC}",
        f"valid until     {not_after:%Y-%m-%d %H:%M UTC}",
        f"days remaining  {days_left:.1f}",
        f"public key      {key_text}",
        f"signature       {cert.signature_algorithm_oid.dotted_string}",
        f"is CA           {ca}",
        "names           " + (", ".join(names[:20]) if names else "—"),
        f"serial          {cert.serial_number:x}",
    ])
    return RunResult(
        message=ctx.M(
            f"گواهی {host} {state_fa} — {days_left:.0f} روز اعتبار باقی است.",
            f"The {host} certificate is {state_en} — {days_left:.0f} day(s) left."),
        details=details)


# ------------------------------------------------------------ security headers
SECURITY_HEADERS = (
    ("content-security-policy", "CSP", 25,
     "جلوگیری از تزریق اسکریپت", "blocks script injection"),
    ("strict-transport-security", "HSTS", 20,
     "اجبار HTTPS", "forces HTTPS"),
    ("x-content-type-options", "X-Content-Type-Options", 10,
     "جلوگیری از حدس نوع فایل", "stops MIME sniffing"),
    ("x-frame-options", "X-Frame-Options", 10,
     "جلوگیری از clickjacking", "stops clickjacking"),
    ("referrer-policy", "Referrer-Policy", 10,
     "کنترل نشت آدرس صفحه", "controls referrer leakage"),
    ("permissions-policy", "Permissions-Policy", 10,
     "کنترل دوربین/میکروفن/موقعیت", "controls powerful browser APIs"),
    ("cross-origin-opener-policy", "COOP", 5,
     "جداسازی پنجره‌ها", "isolates windows"),
)


@tool(
    id="security_headers", category="network", name="نمره‌ی امنیتی هدرهای سایت",
    name_en="Website security-header grade",
    desc="هدرهای امنیتی یک سایت را می‌خواند، نمره و نمره‌ی حرفی می‌دهد و می‌گوید چه چیزی کم است.",
    desc_en="Reads a site's security headers, scores them, gives a letter grade and lists what is missing.",
    icon="shield", order=52,
    keywords=("security", "headers", "csp", "hsts", "grade", "هدر", "امنیت", "نمره", "csp"),
    fields=[
        Field("url", K_TEXT, "آدرس سایت", "Site URL", required=True, placeholder="https://example.com"),
        Field("timeout", K_INT, "زمان انتظار (ثانیه)", "Timeout (seconds)", default=15,
              minimum=3, maximum=60),
    ],
)
def security_headers(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    url = str(v.get("url") or "").strip()
    if not url:
        raise ToolError(ctx.M("آدرس سایت را وارد کنید.", "Enter the site URL."))
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import requests
    except Exception:
        raise ToolError(ctx.M("کتابخانه‌ی requests لازم است.", "The requests package is required."))
    try:
        with requests.get(url, timeout=int(v.get("timeout") or 15),
                          allow_redirects=True, stream=True,
                          headers={"User-Agent": "WinGenius security audit"}) as response:
            resp_url = response.url
            resp_status = response.status_code
            headers = {key.lower(): value for key, value in response.headers.items()}
            cookies = list(response.cookies)
    except Exception as exc:                          # noqa: BLE001 - network is hostile
        raise ToolError(ctx.M(f"سایت پاسخ نداد: {exc}", f"The site did not answer: {exc}"))
    score = 0
    maximum = 0
    lines = [f"URL      {resp_url}",
             f"status   {resp_status}",
             ""]
    missing: List[str] = []
    weak: List[str] = []
    for key, label, weight, fa, en in SECURITY_HEADERS:
        maximum += weight
        value = headers.get(key)
        if value is None:
            missing.append(fa if ctx.lang != "en" else en)
            lines.append(f"✖ {label:26} " + ("غایب" if ctx.lang != "en" else "missing"))
            continue
        points, note = _header_value_score(key, value, headers, ctx.lang)
        score += points
        if points >= weight:
            lines.append(f"✓ {label:26} {value[:90]}")
        elif points > 0:
            weak.append(fa if ctx.lang != "en" else en)
            lines.append(f"◐ {label:26} {value[:70]}  — "
                         + (note if note else ("ضعیف" if ctx.lang != "en" else "weak")))
        else:
            weak.append(fa if ctx.lang != "en" else en)
            lines.append(f"! {label:26} {value[:70]}  — "
                         + (note if note else ("نامعتبر" if ctx.lang != "en" else "invalid")))
    is_https = resp_url.lower().startswith("https://")
    if not is_https:
        lines.append("")
        lines.append(("! سایت روی HTTPS نیست؛ امتیاز و سطح نهایی محدود می‌شود."
                      if ctx.lang != "en" else
                      "! The site is not on HTTPS; score and grade are capped."))
    cookie_notes = _cookie_report(cookies, ctx.lang)
    if cookie_notes:
        lines.append("")
        lines.extend(cookie_notes)
    percent = score * 100 / max(1, maximum)
    if not is_https:
        # plain HTTP can never earn a top grade regardless of headers
        percent = min(percent, 39.0)
    if percent >= 95:
        grade = "A+"
    elif percent >= 85:
        grade = "A"
    elif percent >= 70:
        grade = "B"
    elif percent >= 55:
        grade = "C"
    elif percent >= 40:
        grade = "D"
    else:
        grade = "F"
    verdict = (f"نمره {score}/{maximum} ({percent:.0f}%) — سطح {grade}"
               if ctx.lang != "en" else
               f"Score {score}/{maximum} ({percent:.0f}%) — grade {grade}")
    if missing:
        verdict += ("\nکم است: " + "، ".join(missing)) if ctx.lang != "en" else \
            ("\nMissing: " + ", ".join(missing))
    if weak:
        verdict += ("\nحاضر اما ضعیف/نامعتبر: " + "، ".join(weak)) if ctx.lang != "en" else \
            ("\nPresent but weak/invalid: " + ", ".join(weak))
    verdict += ctx.M("\n(ارزیابی heuristic؛ ممیزی کامل امنیتی سایت نیست.)",
                     "\n(heuristic check — not a full security audit of the site.)")
    return RunResult(message=verdict, details="\n".join(lines))


def _csp_has_unsafe(value: str) -> List[str]:
    """Directives that make a CSP weak: wildcard sources, unsafe-inline/eval."""
    problems = []
    for directive in value.split(";"):
        parts = directive.split()
        if not parts:
            continue
        name = parts[0].lower()
        sources = {p.lower() for p in parts[1:]}
        if name in ("default-src", "script-src", "style-src"):
            if "*" in sources:
                problems.append(f"{name} *")
            if "'unsafe-inline'" in sources:
                problems.append(f"{name} 'unsafe-inline'")
            if "'unsafe-eval'" in sources:
                problems.append(f"{name} 'unsafe-eval'")
    return problems


def _header_value_score(key: str, value: str, headers: Dict[str, str], lang: str) -> tuple:
    """Score one header by its effective value, not mere presence (audit F-08).

    Returns (points, note) where note explains a weak/invalid value.
    """
    fa = lang != "en"

    def N(fa_text: str, en_text: str) -> str:
        return fa_text if fa else en_text

    if key == "content-security-policy":
        weight = 25
        problems = _csp_has_unsafe(value)
        has_frame = any(d.strip().lower().startswith("frame-ancestors")
                        for d in value.split(";"))
        if not problems:
            return weight, ""
        # a restrictive frame-ancestors partly compensates a weak default-src
        return (10 if has_frame else 5), N("دارای wildcard/unsafe: " + ", ".join(problems),
                                           "wildcard/unsafe sources: " + ", ".join(problems))
    if key == "strict-transport-security":
        weight = 20
        max_age = 0
        for part in value.split(";"):
            part = part.strip().lower()
            if part.startswith("max-age="):
                try:
                    max_age = int(part.split("=", 1)[1])
                except ValueError:
                    max_age = 0
        if max_age >= 15_768_000:            # >= ~6 months
            return weight, ""
        if max_age > 0:
            return 8, N(f"max-age={max_age} کوتاه است", f"max-age={max_age} is short")
        return 0, N("max-age=0 یعنی غیرفعال", "max-age=0 means disabled")
    if key == "x-content-type-options":
        return (10, "") if value.strip().lower() == "nosniff" else \
            (0, N(f"مقدار نامعتبر: {value.strip()}", f"invalid value: {value.strip()}"))
    if key == "x-frame-options":
        allowed = {"deny", "sameorigin"}
        if value.strip().lower() in allowed:
            return 10, ""
        # frame-ancestors in a CSP is the modern alternative
        csp = headers.get("content-security-policy", "")
        if any(d.strip().lower().startswith("frame-ancestors") and "'none'" in d.lower() or
               (d.strip().lower().startswith("frame-ancestors") and "'self'" in d.lower())
               for d in csp.split(";")):
            return 10, ""
        return 0, N(f"مقدار نامعتبر: {value.strip()}", f"invalid value: {value.strip()}")
    if key == "referrer-policy":
        safe_values = {"no-referrer", "same-origin", "strict-origin",
                       "strict-origin-when-cross-origin"}
        normalized = value.strip().lower()
        if normalized in safe_values:
            return 10, ""
        if normalized == "unsafe-url":
            return 0, N("unsafe-url کل URL را افشا می‌کند", "unsafe-url leaks the full URL")
        return 4, N(f"مقدار konservativ نیست: {normalized}", f"not privacy-preserving: {normalized}")
    if key == "permissions-policy":
        # presence with at least one disabled feature counts; an allow-all is weak
        if "*" in value and "=()" not in value:
            return 3, N("همه‌ی доступ featureها باز است", "allows everything")
        return 10, ""
    if key == "cross-origin-opener-policy":
        normalized = value.strip().lower()
        if normalized in ("same-origin", "same-origin-allow-popups"):
            return 5, ""
        return 1, N(f"مقدار ضعیف: {normalized}", f"weak value: {normalized}")
    return 0, ""


def _cookie_report(cookies, lang: str) -> List[str]:
    """Per-cookie Secure / HttpOnly / SameSite flags (audit F-08)."""
    fa = lang != "en"
    if not cookies:
        return []
    lines = [(f"کوکی‌ها ({len(cookies)}):" if fa else f"Cookies ({len(cookies)}):")]
    for cookie in cookies:
        flags = []
        if cookie.secure:
            flags.append("Secure")
        else:
            flags.append("!Secure" if fa is False else "!Secure")
        jar = getattr(cookie, "_rest", {}) or {}
        httponly = bool(jar.get("HttpOnly"))
        flags.append("HttpOnly" if httponly else "!HttpOnly")
        samesite = jar.get("SameSite") or ""
        flags.append(f"SameSite={samesite}" if samesite else "!SameSite")
        lines.append(f"  {cookie.name}: " + ", ".join(flags))
    return lines


# ------------------------------------------------------------------- timings
@tool(
    id="http_timing", category="network", name="زمان‌بندی دقیق اتصال سایت",
    name_en="Detailed HTTP timing",
    desc="اتصال به یک سایت را مرحله‌به‌مرحله زمان می‌گیرد: DNS، TCP، TLS و اولین بایت.",
    desc_en="Times a request stage by stage: DNS, TCP connect, TLS handshake and first byte.",
    icon="speed", order=53,
    keywords=("timing", "latency", "dns", "tcp", "ttfb", "speed", "زمان", "تأخیر", "سرعت", "اتصال"),
    fields=[
        Field("url", K_TEXT, "آدرس", "URL", required=True, placeholder="https://example.com"),
        Field("request", K_BOOL, "درخواست HTTP هم بفرست", "Send an HTTP request too", default=True),
    ],
)
def http_timing(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    url = str(v.get("url") or "").strip()
    if not url:
        raise ToolError(ctx.M("آدرس را وارد کنید.", "Enter a URL."))
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in url):
        raise ToolError(ctx.M("آدرس شامل نویسه‌های کنترلی یا فاصله‌ی غیرمجاز است.",
                              "URL contains control characters or unencoded whitespace."))
    if "://" not in url:
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(ctx.M("فقط پروتکل http و https پشتیبانی می‌شود.",
                              "Only http:// and https:// URLs are supported."))
    host = validate_host(parsed.hostname or "", ctx)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ToolError(ctx.M(f"پورت نامعتبر است: {exc}", f"Invalid port: {exc}"))
    timings: List[Tuple[str, float]] = []
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ToolError(ctx.M(f"DNS ناموفق بود: {exc}", f"DNS failed: {exc}"))
    timings.append(("dns", (time.perf_counter() - started) * 1000))
    family, socktype, proto, _canon, address = infos[0]
    raw = socket.socket(family, socktype, proto)
    raw.settimeout(12)
    stream: socket.socket = raw
    try:
        mark = time.perf_counter()
        try:
            raw.connect(address)
        except OSError as exc:
            raise ToolError(ctx.M(f"اتصال TCP برقرار نشد: {exc}", f"TCP connect failed: {exc}"))
        timings.append(("tcp", (time.perf_counter() - mark) * 1000))
        if parsed.scheme == "https":
            mark = time.perf_counter()
            try:
                context = ssl.create_default_context()
                stream = context.wrap_socket(raw, server_hostname=host)
            except Exception as exc:                      # noqa: BLE001 - TLS is external
                raise ToolError(ctx.M(f"دست‌دادن TLS ناموفق بود: {exc}", f"TLS handshake failed: {exc}"))
            timings.append(("tls", (time.perf_counter() - mark) * 1000))
        first_byte = 0.0
        total_payload = 0
        if v.get("request", True):
            path = urllib.parse.quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
            if parsed.query:
                path += "?" + urllib.parse.quote(parsed.query, safe="/?:@!$&'()*+,;=-._~%")
            if any(ch in path for ch in ("\r", "\n", "\x00", " ")):
                raise ToolError(ctx.M("مسیر URL نامعتبر است.", "Unsafe URL request target."))
            host_header = f"[{host}]" if ":" in host else host
            if (parsed.scheme == "http" and port != 80) or (parsed.scheme == "https" and port != 443):
                host_header = f"{host_header}:{port}"
            request = (f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n"
                       "User-Agent: WinGenius timing\r\nConnection: close\r\n\r\n")
            mark = time.perf_counter()
            try:
                stream.sendall(request.encode("ascii"))
                first = stream.recv(1)
                first_byte = (time.perf_counter() - mark) * 1000
                chunk = first
                while chunk and total_payload < 512 * 1024:
                    chunk = stream.recv(65536)
                    total_payload += len(chunk)
            except OSError as exc:
                raise ToolError(ctx.M(f"درخواست ناموفق بود: {exc}", f"The request failed: {exc}"))
            timings.append(("first byte", first_byte))
    finally:
        try:
            stream.close()
        except OSError:
            pass
        try:
            raw.close()
        except OSError:
            pass
    total = (time.perf_counter() - started) * 1000
    labels = {"dns": "DNS", "tcp": "TCP", "tls": "TLS", "first byte": "TTFB"}
    lines = [f"{labels[key]:10} {value:8.1f} ms" for key, value in timings]
    lines.append(f"{'total':10} {total:8.1f} ms")
    lines.append(f"host     {host}:{port}  ip {address[0]}")
    if total_payload:
        lines.append(f"received {total_payload} bytes")
    slowest = max(timings, key=lambda item: item[1])
    verdict = (f"{total:.0f} ms تا اولین بایت · کندترین مرحله: {labels[slowest[0]]}"
               if ctx.lang != "en" else
               f"{total:.0f} ms total · slowest stage: {labels[slowest[0]]}")
    return RunResult(message=verdict, details="\n".join(lines))
