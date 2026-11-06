"""Developer toolbox: regex, text diff, JSON query, JWT, URL, cron, bases, paths."""
from __future__ import annotations

import base64
import difflib
import json
import re
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILE, K_INT, K_MULTILINE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ._helpers import first_file, read_text_file


# --------------------------------------------------------------------- regex
@tool(
    id="regex_tester", category="dev", name="آزمایش Regex", name_en="Regex tester",
    desc="یک الگوی Regex را روی متن امتحان می‌کند و همه‌ی تطابق‌ها و گروه‌ها را نشان می‌دهد.",
    desc_en="Try a regular expression against text and list every match and group.",
    icon="terminal", order=10,
    keywords=("regex", "regexp", "regular expression", "الگو", "عبارت باقاعده"),
    fields=[
        Field("pattern", K_TEXT, "الگو", "Pattern", required=True, placeholder=r"\b\d{3}-\d{4}\b"),
        Field("text", K_MULTILINE, "متن", "Text", required=True),
        Field("flags", K_CHOICE, "حالت‌ها", "Flags", default="im",
              choices=[("", "هیچ", "None"), ("i", "بی‌توجه به بزرگی حروف", "Ignore case"),
                       ("m", "چندخطی", "Multiline"), ("im", "هر دو", "Both"), ("s", "dot matches newline", "DOTALL")]),
        Field("replace", K_TEXT, "متن جایگزین (اختیاری)", "Replacement (optional)"),
    ],
)
def regex_tester(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pattern = str(v.get("pattern") or "")
    text = str(v.get("text") or "")
    if not pattern:
        raise ToolError(ctx.M("الگو را وارد کنید.", "Enter a pattern."))
    flags = 0
    if "i" in str(v.get("flags") or ""):
        flags |= re.IGNORECASE
    if "m" in str(v.get("flags") or ""):
        flags |= re.MULTILINE
    if "s" in str(v.get("flags") or ""):
        flags |= re.DOTALL
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(ctx.M(f"الگوی نامعتبر: {exc}", f"Invalid pattern: {exc}"))
    matches = list(compiled.finditer(text))
    if not matches:
        return RunResult(message=ctx.M("هیچ تطابقی پیدا نشد.", "No matches found."))
    lines: List[str] = []
    for index, match in enumerate(matches[:200], start=1):
        position = text[: match.start()].count("\n") + 1
        lines.append(f"{index:>3}. [{match.start()}:{match.end()}] {ctx.M('خط', 'line')} {position}: {match.group(0)!r}")
        if match.groups():
            for group_index, group in enumerate(match.groups(), start=1):
                lines.append(f"       ${group_index} = {group!r}")
        if match.groupdict():
            for key, value in match.groupdict().items():
                lines.append(f"       {key} = {value!r}")
    replacement = str(v.get("replace") or "")
    if replacement:
        try:
            result = compiled.sub(replacement, text)
            lines += ["", ctx.M("نتیجه‌ی جایگزینی:", "After replacement:"), result[:2000]]
        except re.error as exc:
            lines.append(f"! {exc}")
    return RunResult(
        message=ctx.M(f"{len(matches)} تطابق پیدا شد.", f"{len(matches)} match(es) found."),
        details="\n".join(lines))


# ---------------------------------------------------------------------- diff
@tool(
    id="text_diff", category="dev", name="مقایسه‌ی دو فایل متنی", name_en="Compare text files",
    desc="تفاوت دو فایل متنی را خط‌به‌خط (unified diff) با شماره‌ی خط نشان می‌دهد.",
    desc_en="Show a line-by-line unified diff between two text files.",
    icon="compare", order=11,
    keywords=("diff", "compare files", "مقایسه فایل", "تفاوت متن"),
    fields=[
        Field("a", K_FILE, "فایل اول", "First file", required=True, filt="همه فایل‌ها (*)"),
        Field("b", K_FILE, "فایل دوم", "Second file", required=True, filt="همه فایل‌ها (*)"),
        Field("context", K_INT, "خطوط زمینه", "Context lines", default=3, minimum=0, maximum=20),
        Field("ignore_space", K_BOOL, "بی‌توجه به فاصله‌ها", "Ignore whitespace", default=False),
    ],
)
def text_diff(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    a_path = first_file(v, "a", ctx)
    b_path = first_file(v, "b", ctx)
    ctx.progress(1, 3, ctx.M("خواندن فایل‌ها", "Reading files"))
    try:
        a_lines = read_text_file(a_path, ctx).splitlines()
        b_lines = read_text_file(b_path, ctx).splitlines()
    except Exception as exc:
        raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read a file: {exc}"))
    if v.get("ignore_space"):
        a_lines = [re.sub(r"\s+", " ", line) for line in a_lines]
        b_lines = [re.sub(r"\s+", " ", line) for line in b_lines]
    ctx.progress(2, 3, ctx.M("محاسبه‌ی تفاوت", "Diffing"))
    diff = list(difflib.unified_diff(a_lines, b_lines, fromfile=a_path.name, tofile=b_path.name,
                                     lineterm="", n=int(v.get("context") or 3)))
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    ctx.progress(3, 3, "")
    if not diff:
        return RunResult(message=ctx.M("فایل‌ها یکسان هستند.", "The files are identical."))
    return RunResult(
        message=ctx.M(f"{added} خط اضافه، {removed} خط حذف — {len(diff)} خط تفاوت.",
                      f"{added} added, {removed} removed — {len(diff)} diff lines."),
        details="\n".join(diff[:1500]) + ("\n…" if len(diff) > 1500 else ""))


# ---------------------------------------------------------------------- json
@tool(
    id="json_query", category="dev", name="پرس‌وجوی JSON", name_en="Query JSON",
    desc="با مسیر نقطه‌ای (مثل data.users[0].name) مقدار داخل JSON را بیرون می‌کشد و قالب‌بندی می‌کند.",
    desc_en="Pull values out of JSON with a dot path (e.g. data.users[0].name) and prettify it.",
    icon="code", order=12,
    keywords=("json query", "jq", "extract json", "پرس و جوی جیسون"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "متن JSON", "JSON text", visible_when=("source", "text")),
        Field("file", K_FILE, "فایل JSON", "JSON file", filt="JSON (*.json);;همه فایل‌ها (*)",
              visible_when=("source", "file")),
        Field("path", K_TEXT, "مسیر", "Path", placeholder="data.items[0].title"),
        Field("pretty", K_BOOL, "خروجی مرتب و خوانا", "Pretty output", default=True),
    ],
)
def json_query(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if str(v.get("source") or "text") == "file":
        raw = read_text_file(first_file(v, "file", ctx), ctx)
    else:
        raw = str(v.get("text") or "")
    if not raw.strip():
        raise ToolError(ctx.M("JSON خالی است.", "The JSON is empty."))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(ctx.M(f"JSON نامعتبر است: خط {exc.lineno}، ستون {exc.colno} — {exc.msg}",
                              f"Invalid JSON: line {exc.lineno}, column {exc.colno} — {exc.msg}"))
    path = str(v.get("path") or "").strip()
    current: Any = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]|\[\]", path):
        if part.startswith("["):
            if not isinstance(current, (list, tuple)):
                raise ToolError(ctx.M(f"در مسیر {part} آرایه انتظار می‌رفت.", f"Expected an array at {part}."))
            if part == "[]":
                continue
            index = int(part[1:-1])
            if index >= len(current):
                raise ToolError(ctx.M(f"اندیس {index} خارج از محدوده است.", f"Index {index} is out of range."))
            current = current[index]
        else:
            if isinstance(current, list):
                current = [item.get(part) for item in current if isinstance(item, dict) and part in item]
            elif isinstance(current, dict):
                if part not in current:
                    available = ", ".join(list(current)[:12])
                    raise ToolError(ctx.M(f"کلید «{part}» نیست. کلیدهای موجود: {available}",
                                          f"Key '{part}' not found. Available: {available}"))
                current = current[part]
            else:
                raise ToolError(ctx.M(f"در {part} نمی‌توان ادامه داد.", f"Cannot descend into {part}."))
    indent = 2 if v.get("pretty", True) else None
    text = json.dumps(current, ensure_ascii=False, indent=indent, default=str)
    kind = type(current).__name__
    size = len(current) if isinstance(current, (list, dict, str)) else 1
    return RunResult(
        message=ctx.M(f"{kind} با {size} عضو", f"{kind} with {size} item(s)"),
        details=text[:20000])


# ----------------------------------------------------------------------- jwt
@tool(
    id="jwt_decode", category="dev", name="رمزگشایی JWT", name_en="Decode JWT",
    desc="هدر و بدنه‌ی یک توکن JWT را بدون کلید می‌خواند و تاریخ انقضا را هشدار می‌دهد.",
    desc_en="Read the header and payload of a JWT without a key, with expiry warnings.",
    icon="key", order=13,
    keywords=("jwt", "token", "توکن", "رمزگشایی jwt"),
    fields=[
        Field("token", K_MULTILINE, "توکن", "Token", required=True),
    ],
)
def jwt_decode(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    token = re.sub(r"\s+", "", str(v.get("token") or ""))
    if token.lower().startswith("bearer "):
        token = token[7:]
    parts = token.split(".")
    if len(parts) < 2:
        raise ToolError(ctx.M("این یک JWT معتبر نیست (باید ۳ بخش با نقطه داشته باشد).",
                              "Not a valid JWT (expected three dot-separated parts)."))

    def decode(segment: str) -> Any:
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))

    try:
        header = decode(parts[0])
        payload = decode(parts[1])
    except Exception as exc:
        raise ToolError(ctx.M(f"رمزگشایی ممکن نشد: {exc}", f"Could not decode: {exc}"))
    lines = [ctx.M("هدر:", "Header:"), json.dumps(header, ensure_ascii=False, indent=2),
             "", ctx.M("بدنه:", "Payload:"), json.dumps(payload, ensure_ascii=False, indent=2)]
    message = ctx.M("JWT رمزگشایی شد.", "JWT decoded.")
    if isinstance(payload, dict):
        now = datetime.now().timestamp()
        if "exp" in payload:
            expired = float(payload["exp"]) < now
            stamp = datetime.fromtimestamp(float(payload["exp"])).strftime("%Y-%m-%d %H:%M")
            message = (ctx.M(f"JWT منقضی شده است (exp: {stamp})", f"JWT is expired (exp: {stamp})")
                       if expired else
                       ctx.M(f"JWT معتبر است تا {stamp}", f"JWT valid until {stamp}"))
        if "iat" in payload:
            lines.append("")
            lines.append(ctx.M(f"صادر شده در: {datetime.fromtimestamp(float(payload['iat'])).strftime('%Y-%m-%d %H:%M')}",
                               f"Issued at: {datetime.fromtimestamp(float(payload['iat'])).strftime('%Y-%m-%d %H:%M')}"))
    if len(parts) >= 3:
        lines += ["", ctx.M("امضا (بدون بررسی):", "Signature (unverified):"), parts[2][:80]]
    return RunResult(message=message, details="\n".join(lines))


# ----------------------------------------------------------------------- url
@tool(
    id="url_tool", category="dev", name="ابزار URL", name_en="URL tools",
    desc="آدرس‌ها را کد/رمز می‌کند، پارامترها را می‌شکند و بخش‌های یک لینک را جدا نشان می‌دهد.",
    desc_en="Encode/decode URLs, split query parameters and dissect a link.",
    icon="link", order=14,
    keywords=("url encode", "query string", "لینک", "کد کردن آدرس"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="encode",
              choices=[("encode", "کد کردن (URL encode)", "Encode"),
                       ("decode", "رمزگشایی", "Decode"),
                       ("parse", "تحلیل لینک", "Parse link")]),
        Field("value", K_MULTILINE, "مقدار", "Value", required=True),
    ],
)
def url_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    value = str(v.get("value") or "")
    action = str(v.get("action") or "encode")
    if action == "encode":
        return RunResult(message=ctx.M("کد شد.", "Encoded."),
                         details=urllib.parse.quote(value, safe=""))
    if action == "decode":
        return RunResult(message=ctx.M("رمزگشایی شد.", "Decoded."),
                         details=urllib.parse.unquote(value))
    parsed = urllib.parse.urlparse(value.strip())
    rows = [
        f"scheme:   {parsed.scheme}",
        f"host:     {parsed.hostname}",
        f"port:     {parsed.port}",
        f"path:     {parsed.path}",
        f"params:   {parsed.params}",
        f"query:    {parsed.query}",
        f"fragment: {parsed.fragment}",
    ]
    if parsed.query:
        rows += ["", ctx.M("پارامترها:", "Query parameters:")]
        for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
            rows.append(f"  {key} = {item}")
    return RunResult(message=ctx.M("لینک تحلیل شد.", "Link parsed."), details="\n".join(rows))


# ---------------------------------------------------------------------- cron
@tool(
    id="cron_parse", category="dev", name="توضیح Cron و اجراهای بعدی",
    name_en="Explain cron schedule",
    desc="یک عبارت Cron را به زبان ساده توضیح می‌دهد و پنج زمان اجرای بعدی را حساب می‌کند.",
    desc_en="Explain a cron expression in plain language and compute the next five runs.",
    icon="clock", order=15,
    keywords=("cron", "schedule", "زمان بندی", "کرون"),
    fields=[
        Field("expression", K_TEXT, "عبارت Cron", "Cron expression", default="0 9 * * *",
              placeholder="*/15 * * * *"),
    ],
)
def cron_parse(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    expression = str(v.get("expression") or "").strip()
    fields = expression.split()
    if len(fields) != 5:
        raise ToolError(ctx.M("عبارت Cron باید ۵ بخش داشته باشد: دقیقه ساعت روز ماه روزِهفته",
                              "A cron expression needs five fields: minute hour day month weekday"))
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]

    def expand(field: str, low: int, high: int) -> Optional[List[int]]:
        values: List[int] = []
        for chunk in field.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            step = 1
            if "/" in chunk:
                chunk, _, step_text = chunk.partition("/")
                try:
                    step = max(1, int(step_text))
                except ValueError:
                    return None
            if chunk in ("*", "?"):
                start, end = low, high
            elif "-" in chunk:
                start_text, _, end_text = chunk.partition("-")
                try:
                    start, end = int(start_text), int(end_text)
                except ValueError:
                    return None
            else:
                try:
                    start = end = int(chunk)
                except ValueError:
                    return None
            if start < low or end > high or start > end:
                return None
            values.extend(range(start, end + 1, step))
        return sorted(set(values))

    expanded: List[List[int]] = []
    for field_text, (low, high) in zip(fields, ranges, strict=True):   # len(fields)==5 checked above
        values = expand(field_text, low, high)
        if values is None:
            raise ToolError(ctx.M(f"بخش «{field_text}» معتبر نیست.", f"The field '{field_text}' is invalid."))
        expanded.append(values)
    minutes, hours, days, months, weekdays = expanded
    weekday_names_fa = ["یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه"]
    weekday_names_en = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    names = weekday_names_en if ctx.lang == "en" else weekday_names_fa
    runs: List[str] = []
    moment = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    guard = 0
    while len(runs) < 5 and guard < 600000:
        guard += 1
        if (moment.minute in minutes and moment.hour in hours and moment.day in days
                and moment.month in months
                and (((moment.weekday() + 1) % 7) in weekdays)):
            runs.append(moment.strftime("%Y-%m-%d %H:%M") + f"  ({names[(moment.weekday() + 1) % 7]})")
        moment += timedelta(minutes=1)
    if not runs:
        raise ToolError(ctx.M("این عبارت هیچ‌وقت اجرا نمی‌شود.", "This expression never fires."))
    lines = [
        ctx.M(f"دقیقه: {_short(minutes, 60)}", f"Minute: {_short(minutes, 60)}"),
        ctx.M(f"ساعت: {_short(hours, 24)}", f"Hour: {_short(hours, 24)}"),
        ctx.M(f"روز ماه: {_short(days, 32)}", f"Day of month: {_short(days, 32)}"),
        ctx.M(f"ماه: {_short(months, 13)}", f"Month: {_short(months, 13)}"),
        ctx.M(f"روز هفته: {', '.join(names[d] for d in weekdays)}",
              f"Weekday: {', '.join(names[d] for d in weekdays)}"),
        "", ctx.M("پنج اجرای بعدی:", "Next five runs:"), *runs,
    ]
    return RunResult(message=ctx.M(f"اجرای بعدی: {runs[0]}", f"Next run: {runs[0]}"),
                     details="\n".join(lines))


def _short(values: List[int], limit: int) -> str:
    if len(values) >= min(limit, 24):
        return "همه/All"
    text = ", ".join(str(value) for value in values[:20])
    return text + ("…" if len(values) > 20 else "")


# --------------------------------------------------------------------- bases
@tool(
    id="number_base", category="dev", name="تبدیل مبنا", name_en="Number base converter",
    desc="عدد را بین دودویی، هشت‌هشتی، ده‌دهی و شانزده‌شانزدهی تبدیل می‌کند (با نمایش بیتی).",
    desc_en="Convert numbers between binary, octal, decimal and hex, with a bit view.",
    icon="hash", order=16,
    keywords=("hex", "binary", "base64 number", "مبنا", "هگز", "باینری"),
    fields=[
        Field("value", K_TEXT, "عدد", "Number", required=True, default="255"),
        Field("base", K_CHOICE, "مبنای ورودی", "Input base", default="dec",
              choices=[("dec", "ده‌دهی", "Decimal"), ("hex", "شانزده‌شانزدهی", "Hex"),
                       ("bin", "دودویی", "Binary"), ("oct", "هشت‌هشتی", "Octal")]),
        Field("bits", K_INT, "عرض بیت (برای نمایش)", "Bit width", default=32, minimum=8, maximum=64),
    ],
)
def number_base(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("value") or "").strip().replace(" ", "")
    base = {"dec": 10, "hex": 16, "bin": 2, "oct": 8}.get(str(v.get("base") or "dec"), 10)
    try:
        number = int(raw, base)
    except ValueError:
        raise ToolError(ctx.M(f"«{raw}» یک عدد مبنای {base} نیست.", f"'{raw}' is not a base-{base} number."))
    bits = max(8, int(v.get("bits") or 32))
    mask = (1 << bits) - 1
    wrapped = number & mask
    bit_text = format(wrapped, f"0{bits}b")
    grouped = " ".join(bit_text[index:index + 4] for index in range(0, len(bit_text), 4))
    lines = [
        f"DEC: {number:,}",
        f"HEX: 0x{number:X}",
        f"OCT: 0o{number:o}",
        f"BIN: {bin(number)[2:]}",
        "",
        ctx.M(f"نمایش {bits} بیتی:", f"{bits}-bit view:"),
        grouped,
        ctx.M(f"مکمل دو: {wrapped - (1 << bits) if wrapped >> (bits - 1) else wrapped}",
              f"Two's complement: {wrapped - (1 << bits) if wrapped >> (bits - 1) else wrapped}"),
        ctx.M(f"بایت‌ها: {wrapped.to_bytes(bits // 8, 'big').hex(' ')}",
              f"Bytes: {wrapped.to_bytes(bits // 8, 'big').hex(' ')}"),
    ]
    return RunResult(message=f"DEX {number:,} · 0x{number:X} · 0b{bin(number)[2:]}", details="\n".join(lines))


# --------------------------------------------------------------------- paths
@tool(
    id="path_tool", category="dev", name="ابزار مسیر فایل", name_en="Path tools",
    desc="مسیرها را پاک‌سازی می‌کند، طول و اجزا را می‌گوید و مسیر ویندوزی/لینوکسی می‌سازد.",
    desc_en="Normalise paths, show their parts and length, and convert Windows ↔ POSIX style.",
    icon="folder", order=17,
    keywords=("path", "normalize path", "مسیر ویندوز", "طول مسیر"),
    fields=[
        Field("path", K_TEXT, "مسیر", "Path", required=True, placeholder=r"C:\Users\me\file.txt"),
    ],
)
def path_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("path") or "").strip().strip('"')
    if not raw:
        raise ToolError(ctx.M("مسیر را وارد کنید.", "Enter a path."))
    windows_path = raw.replace("/", "\\")
    posix_path = raw.replace("\\", "/")
    path = Path(windows_path) if ":" in windows_path[:3] else Path(posix_path)
    parent = str(path.parent)
    lines = [
        ctx.M("مسیر پاک‌سازی‌شده:", "Normalised:"),
        str(path),
        "",
        f"name:   {path.name}",
        f"stem:   {path.stem}",
        f"suffix: {path.suffix}",
        f"parent: {parent}",
        ctx.M(f"طول مسیر: {len(raw)} نویسه", f"Length: {len(raw)} characters"),
        f"windows: {windows_path}",
        f"posix:   {posix_path}",
    ]
    if len(raw) > 240:
        lines.append(ctx.M("⚠ مسیر بلندتر از ۲۴۰ نویسه است و ممکن است در ویندوز مشکل بسازد.",
                           "⚠ Longer than 240 characters — this can break on Windows."))
    return RunResult(message=path.name or str(path), details="\n".join(lines))
