"""Text and date smarts: PII redaction, and date arithmetic across two calendars."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILE, K_INT, K_MULTILINE, K_SAVE, K_TEXT, Field, RunContext, RunResult,
    ToolError,
)
from ._helpers import read_text_file, save_target

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def to_ascii_digits(text: str) -> str:
    """Map Persian/Arabic digits to ASCII *without changing the string length*."""
    table = {ord(fa): str(index) for index, fa in enumerate(PERSIAN_DIGITS)}
    table.update({ord(ar): str(index) for index, ar in enumerate(ARABIC_DIGITS)})
    return text.translate(table)


# --------------------------------------------------------------------- redaction
PATTERNS: Dict[str, Tuple[str, str, str]] = {
    # key: (regex, fa label, en label)
    "email": (r"[\w.+-]+@[\w-]+\.[\w.-]+", "ایمیل", "e-mail"),
    "url": (r"https?://[^\s<>\"']+", "نشانی وب", "URL"),
    "iran_mobile": (r"(?:\+98|0098|0)9\d{2}[ \-]?\d{3}[ \-]?\d{4}", "موبایل", "mobile number"),
    "iran_phone": (r"0\d{2}[ \-]?\d{3,4}[ \-]?\d{4}", "تلفن ثابت", "phone number"),
    "ip": (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "آی‌پی", "IP address"),
    "iban": (r"\bIR\d{24}\b", "شماره شبا", "IBAN"),
    "card": (r"\b(?:\d[ -]?){13,19}\b", "کارت بانکی", "card number"),
    "national_id": (r"\b\d{10}\b", "کد ملی", "national ID"),
    "postal": (r"\b\d{5}[ -]?\d{5}\b", "کد پستی", "postal code"),
}

REDACT_KINDS = (
    ("email", "ایمیل", "E-mail"),
    ("iran_mobile", "موبایل", "Mobile"),
    ("iran_phone", "تلفن ثابت", "Landline"),
    ("card", "کارت بانکی", "Card"),
    ("national_id", "کد ملی", "National ID"),
    ("iban", "شبا", "IBAN"),
    ("postal", "کد پستی", "Postal code"),
    ("ip", "آی‌پی", "IP"),
    ("url", "نشانی وب", "URL"),
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for index, char in enumerate(reverse):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _national_id_ok(digits: str) -> bool:
    if len(digits) != 10 or len(set(digits)) == 1:
        return False
    check = int(digits[9])
    total = sum(int(digits[index]) * (10 - index) for index in range(9))
    remainder = total % 11
    return check == remainder or (remainder == 10 and check == 0)


def _mask(value: str, mode: str, kind: str, keep_last: bool) -> str:
    if mode == "placeholder":
        return f"[{kind.upper()}]"
    if mode == "hash":
        return f"[{kind.upper()}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]}]"
    compact = re.sub(r"\s", "", value)
    if kind in ("email",) and "@" in compact:
        local, _, domain = compact.partition("@")
        return (local[0] + "*" * max(1, len(local) - 1) + "@" + domain)
    if keep_last and len(compact) > 4:
        return "*" * (len(compact) - 4) + compact[-4:]
    return "*" * max(3, len(compact))


@tool(
    id="text_redact", category="convert", name="پاک‌کردن داده‌های شخصی از متن",
    name_en="Redact personal data from text",
    desc="ایمیل، موبایل، کد ملی، کارت بانکی، شبا، کد پستی، آی‌پی و لینک را از متن پاک می‌کند و لیست می‌دهد.",
    desc_en="Removes e-mails, phones, national IDs, card numbers, IBANs, postal codes, IPs and links — and lists them.",
    icon="shield", order=70,
    keywords=("redact", "pii", "privacy", "mask", "anonymise", "پاک‌سازی", "داده شخصی",
              "مخفی", "ناشناس", "حساس"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text (*.txt *.log *.csv *.md *.json)"),
        Field("mode", K_CHOICE, "روش پوشاندن", "Masking style", default="placeholder",
              choices=[("placeholder", "[EMAIL]", "[EMAIL]"),
                       ("mask", "ستاره", "Asterisks"),
                       ("hash", "اثر انگشت", "Fingerprint")]),
        Field("keep_last", K_BOOL, "۴ رقم آخر بماند", "Keep the last 4 digits", default=False),
        Field("strict", K_BOOL, "اعتبارسنجی دقیق (کد ملی/کارت)", "Validate IDs and cards",
              default=True),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def text_redact(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    if not text.strip():
        path = str(v.get("file") or "").strip()
        if path:
            text = read_text_file(path, ctx)
    if not text.strip():
        raise ToolError(ctx.M("متن یا فایل را بدهید.", "Provide text or a file."))
    mode = str(v.get("mode") or "placeholder")
    keep_last = bool(v.get("keep_last"))
    strict = bool(v.get("strict", True))
    normalized = to_ascii_digits(text)
    counts: Dict[str, int] = {}
    spans: List[Tuple[int, int, str]] = []
    for kind, (pattern, _fa, _en) in PATTERNS.items():
        for match in re.finditer(pattern, normalized, flags=re.I):
            value = match.group(0)
            stripped = re.sub(r"[^\d]", "", value)
            if kind == "card":
                if len(stripped) < 13 or not _luhn_ok(stripped):
                    continue
            elif kind == "national_id":
                if strict and not _national_id_ok(stripped):
                    continue
            elif kind == "postal":
                if len(stripped) != 10:
                    continue
            elif kind == "iran_phone":
                if len(stripped) < 10:
                    continue
            spans.append((match.start(), match.end(), kind))
    spans.sort(key=lambda item: (item[0], -item[1]))
    cleaned: List[str] = []
    cursor = 0
    for start, end, kind in spans:
        if start < cursor:
            continue
        cleaned.append(text[cursor:start])
        cleaned.append(_mask(text[start:end], mode, kind, keep_last))
        cursor = end
        counts[kind] = counts.get(kind, 0) + 1
    cleaned.append(text[cursor:])
    result = "".join(cleaned)
    if not counts:
        return RunResult(
            message=ctx.M("چیزی برای پاک‌کردن پیدا نشد؛ متن تمیز است.",
                          "Nothing to redact — the text looks clean."),
            details=result[:4000])
    summary = " · ".join(
        f"{dict((key, fa) for key, fa, _en in REDACT_KINDS).get(kind, kind)}: {count}"
        if ctx.lang != "en" else
        f"{dict((key, en) for key, _fa, en in REDACT_KINDS).get(kind, kind)}: {count}"
        for kind, count in counts.items())
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, "redacted.txt")
        target.write_text(result, encoding="utf-8")
        saved.append(str(target))
    return RunResult(
        message=ctx.M(f"{sum(counts.values())} مورد پاک شد — {summary}",
                      f"{sum(counts.values())} item(s) redacted — {summary}"),
        details=result[:8000], outputs=saved)


# ---------------------------------------------------------------------- date math
WEEKDAYS_FA = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")
JALALI_MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")


def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    """Civil (Gregorian) → Jalali, the same arithmetic the Persian tools use."""
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for index in range(gm2):
        g_day_no += g_days_in_month[index]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    g_day_no += gd2
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    jm = 0
    while jm < 11 and j_day_no >= j_days_in_month[jm]:
        j_day_no -= j_days_in_month[jm]
        jm += 1
    return jy, jm + 1, j_day_no + 1


def _jalali_to_gregorian(jy: int, jm: int, jd: int) -> Tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30 + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    month_days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    for length in month_days:
        if gd <= length:
            break
        gd -= length
        gm += 1
    return gy, gm, gd


def _parse_date(raw: str, calendar: str) -> date:
    text = to_ascii_digits(raw).strip().replace("-", "/").replace(".", "/")
    if not text:
        return date.today()
    parts = [part for part in re.split(r"[/\s:]+", text) if part]
    if len(parts) < 3:
        raise ToolError(f"cannot parse date: {raw}")
    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        raise ToolError(f"cannot parse date: {raw}")
    if calendar in ("auto", "jalali") and 1200 <= year <= 1600:
        year, month, day = _jalali_to_gregorian(year, month, day)
    return date(year, month, day)


def _looks_jalali(raw: str) -> bool:
    text = to_ascii_digits(raw).strip()
    match = re.match(r"(\d{4})", text)
    return bool(match) and 1200 <= int(match.group(1)) <= 1600


def _jalali_is_leap(jy: int) -> bool:
    """A Jalali year is leap when Esfand has 30 days (derived from the conversion)."""
    gy, gm, gd = _jalali_to_gregorian(jy, 12, 30)
    return _gregorian_to_jalali(gy, gm, gd) == (jy, 12, 30)


def _jalali_month_length(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if _jalali_is_leap(jy) else 29


def _business_days(start: date, end: date) -> int:
    if start > end:
        start, end = end, start
    days = (end - start).days
    weeks, remainder = divmod(days, 7)
    count = weeks * 5
    weekday = start.weekday()
    for offset in range(remainder):
        if (weekday + offset) % 7 < 5:
            count += 1
    return count


def _describe(target: date, lang: str) -> str:
    jy, jm, jd = _gregorian_to_jalali(target.year, target.month, target.day)
    weekday_en = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    weekday_index = target.weekday()
    weekday_fa = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")[weekday_index]
    if lang == "en":
        return (f"{target.isoformat()} ({weekday_en[weekday_index]}) · "
                f"Jalali {jy}/{jm:02d}/{jd:02d}")
    return (f"{target.isoformat()} ({weekday_fa}) · شمسی "
            f"{jy}/{jm:02d}/{jd:02d} ({JALALI_MONTHS[jm - 1]})")


@tool(
    id="date_math", category="convert", name="محاسبه‌ی تاریخ (شمسی و میلادی)",
    name_en="Date arithmetic (Jalali & Gregorian)",
    desc="روز/ماه/سال به یک تاریخ اضافه می‌کند، اختلاف دو تاریخ (و روزهای کاری) را می‌گوید و هر دو تقویم را نشان می‌دهد.",
    desc_en="Adds days/months/years to a date, measures the gap between two dates (business days too) in both calendars.",
    icon="calendar", order=71,
    keywords=("date", "jalali", "days between", "add days", "تاریخ", "شمسی", "اختلاف",
              "روز کاری", "محاسبه"),
    fields=[
        Field("date", K_TEXT, "تاریخ پایه", "Base date", placeholder="2026-11-03 یا 1405/08/12",
              placeholder_en="2026-11-03 or 1405/08/12",
              help="خالی بگذارید تا امروز در نظر گرفته شود.",
              help_en="Leave empty to use today."),
        Field("calendar", K_CHOICE, "تقویم ورودی", "Input calendar", default="auto",
              choices=[("auto", "تشخیص خودکار", "Detect automatically"),
                       ("gregorian", "میلادی", "Gregorian"), ("jalali", "شمسی", "Jalali")]),
        Field("add", K_INT, "اضافه/کم کن", "Add or subtract", default=0,
              minimum=-100000, maximum=100000),
        Field("unit", K_CHOICE, "واحد", "Unit", default="days",
              choices=[("days", "روز", "days"), ("weeks", "هفته", "weeks"),
                       ("months", "ماه", "months"), ("years", "سال", "years"),
                       ("hours", "ساعت", "hours")]),
        Field("other", K_TEXT, "تاریخ دوم (برای اختلاف)", "Second date (for the gap)"),
    ],
)
def date_math(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    calendar = str(v.get("calendar") or "auto")
    try:
        base = _parse_date(str(v.get("date") or ""), calendar)
    except ToolError:
        raise ToolError(ctx.M("تاریخ پایه نامفهوم است؛ نمونه: 2026-11-03 یا 1405/08/12",
                              "Cannot read the base date; try 2026-11-03 or 1405/08/12"))
    unit = str(v.get("unit") or "days")
    amount = int(v.get("add") or 0)
    jalali_input = calendar == "jalali" or (calendar == "auto" and _looks_jalali(str(v.get("date") or "")))
    if unit == "weeks":
        target = base + timedelta(weeks=amount)
    elif unit == "hours":
        target = base + timedelta(hours=amount)
    elif unit in ("months", "years") and jalali_input:
        # a Jalali date moves by Jalali months/years (1405/06/31 + 1 month → 1405/07/30)
        jy, jm, jd = _gregorian_to_jalali(base.year, base.month, base.day)
        if unit == "months":
            month_index = jm - 1 + amount
            jy, jm = jy + month_index // 12, month_index % 12 + 1
        else:
            jy += amount
        jd = min(jd, _jalali_month_length(jy, jm))
        target = date(*_jalali_to_gregorian(jy, jm, jd))
    elif unit == "months":
        month_index = base.month - 1 + amount
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        day = min(base.day, [31, 29 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
                             else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        target = date(year, month, day)
    elif unit == "years":
        year = base.year + amount
        try:
            target = base.replace(year=year)
        except ValueError:  # 29 Feb → 28 Feb
            target = base.replace(year=year, day=28)
    else:
        target = base + timedelta(days=amount)
    lines = [
        ("پایه:  " if ctx.lang != "en" else "base:   ") + _describe(base, ctx.lang),
        ("نتیجه: " if ctx.lang != "en" else "result: ") + _describe(target, ctx.lang),
        f"ISO week   {target.isocalendar().year}-W{target.isocalendar().week:02d}",
        f"day of year {target.timetuple().tm_yday}",
        f"timestamp  {int(time.mktime(target.timetuple()))}",
        f"leap year  {target.year % 4 == 0 and (target.year % 100 != 0 or target.year % 400 == 0)}",
    ]
    other_raw = str(v.get("other") or "").strip()
    message_extra = ""
    if other_raw:
        try:
            other = _parse_date(other_raw, calendar)
        except ToolError:
            raise ToolError(ctx.M(f"تاریخ دوم نامفهوم است: {other_raw}",
                                  f"Cannot read the second date: {other_raw}"))
        days = (other - base).days
        business = _business_days(base, other)
        weeks = days / 7
        lines.append("")
        lines.append(("اختلاف با " if ctx.lang != "en" else "gap to ") + other.isoformat())
        lines.append(f"  days      {days:+d}")
        lines.append(f"  weeks     {weeks:+.2f}")
        lines.append(f"  business  {business} " + ("روز کاری" if ctx.lang != "en" else "working day(s)"))
        years = abs(days) / 365.25
        lines.append(f"  years     {years:.2f}")
        message_extra = ctx.M(f" · اختلاف {days:+d} روز", f" · gap of {days:+d} day(s)")
    delta = (target - base).days
    return RunResult(
        message=ctx.M(
            _describe(target, "fa") + (f" ({delta:+d} روز)" if delta else "") + message_extra,
            _describe(target, "en") + (f" ({delta:+d} days)" if delta else "") + message_extra),
        details="\n".join(lines))
