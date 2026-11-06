"""Persian text intelligence: normalising, digits, Jalali calendar, sorting, keyboard."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core import persian_pdf
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILE, K_INT, K_MULTILINE, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ._helpers import first_file, out_dir, save_target

ARABIC_TO_PERSIAN = {
    "\u064A": "\u06CC",  # ي → ی
    "\u0643": "\u06A9",  # ك → ک
    "\u0629": "\u0647",  # ة → ه
    "\u06C0": "\u0647\u0654",
    "\u0640": "",        # tatweel
    "\u0622": "\u0622",
    "\u200C": "\u200C",
}
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
LATIN_DIGITS = "0123456789"

KEYBOARD_MAP_EN_TO_FA = {
    "q": "ض", "w": "ص", "e": "ث", "r": "ق", "t": "ف", "y": "غ", "u": "ع", "i": "ه",
    "o": "خ", "p": "ح", "[": "ج", "]": "چ", "a": "ش", "s": "س", "d": "ی", "f": "ب",
    "g": "ل", "h": "ا", "j": "ت", "k": "ن", "l": "م", ";": "ک", "'": "گ", "z": "ظ",
    "x": "ط", "c": "ز", "v": "ر", "b": "ذ", "n": "د", "m": "ئ", ",": "و", ".": ".",
    "/": "/", "\\": "\\", "`": "`", "1": "۱", "2": "۲", "3": "۳", "4": "۴", "5": "۵",
    "6": "۶", "7": "۷", "8": "۸", "9": "۹", "0": "۰", "-": "-", "=": "=",
}
KEYBOARD_MAP_FA_TO_EN = {value: key for key, value in KEYBOARD_MAP_EN_TO_FA.items() if len(value) == 1}


def _read_text(ctx: RunContext, value: Any, path_key: str = "file") -> str:
    source = str(value.get("source") or "text")
    if source == "file":
        path = first_file(value, path_key, ctx)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read the file: {exc}"))
    return str(value.get("text") or "")


def _emit(ctx: RunContext, v: Dict[str, Any], text: str, suffix: str = "_fixed") -> RunResult:
    requested_out = str(v.get("out") or "").strip()
    if v.get("write_file") or requested_out:
        dest = save_target(ctx, requested_out, f"text{suffix}.txt")
        dest.write_text(text, encoding="utf-8")
        return RunResult(
            message=ctx.M(f"متن اصلاح‌شده ذخیره شد.\n{dest}", f"Fixed text saved.\n{dest}"),
            outputs=[str(dest)], open_target=str(dest), details=text[:12000])
    return RunResult(message=ctx.M(f"متن اصلاح شد ({len(text)} نویسه).", f"Text normalised ({len(text)} characters)."),
                     details=text[:12000])


@tool(
    id="persian_fix", category="convert", name="اصلاح متن فارسی", name_en="Fix Persian text",
    desc="«ي/ك» عربی را به «ی/ک» تبدیل، نیم‌فاصله را مرتب و فاصله‌های اضافی و نویسه‌های پنهان را پاک می‌کند.",
    desc_en="Fix Arabic ي/ك to Persian ی/ک, clean zero-width characters, spacing and hidden marks.",
    icon="persian", order=200,
    keywords=("فارسی", "اصلاح متن", "نیم فاصله", "persian fix", "arabic letters", "ی ك"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "متن", "Text", visible_when=("source", "text")),
        Field("file", K_FILE, "فایل متنی", "Text file", filt="متن (*.txt *.md *.csv);;همه فایل‌ها (*)",
              visible_when=("source", "file")),
        Field("zero_width", K_BOOL, "اصلاح نیم‌فاصله و حذف نویسه‌های پنهان", "Fix ZWNJ / hidden chars",
              default=True),
        Field("numbers", K_CHOICE, "ارقام", "Digits", default="keep",
              choices=[("keep", "دست نزن", "Keep"), ("fa", "فارسی", "Persian"), ("en", "لاتین", "Latin")]),
        Field("write_file", K_BOOL, "ذخیره در فایل", "Save to file", default=False),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="متن (*.txt)", visible_when=("write_file", True)),
    ],
)
def persian_fix(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = _read_text(ctx, v)
    if not text:
        raise ToolError(ctx.M("متنی وارد نشده.", "No text provided."))
    before = text
    digits = str(v.get("numbers") or "keep")
    # presentation forms, reversed legacy lines, Arabic letters, mirrored brackets, spacing
    text = persian_pdf.normalize_persian(text, digits=digits if digits in ("fa", "en") else None,
                                         zwnj=bool(v.get("zero_width", True)))
    text = text.replace("\u06C0", "\u0647\u0654") if "\u06C0" in before and "\u06C0" in text else text
    if v.get("zero_width", True):
        text = re.sub(r"[\u200B\u200D\uFEFF\u2060]", "", text)
    # length difference is intentional (it is added to the change count below)
    changed = sum(1 for a, b in zip(before, text, strict=False) if a != b) + abs(len(before) - len(text))
    half_spaces = text.count("\u200C") - before.count("\u200C")
    result = _emit(ctx, v, text)
    if half_spaces > 0:
        result.message += ctx.M(f"\n{half_spaces} نیم‌فاصله اضافه شد.", f"\n{half_spaces} half-space(s) added.")
    if changed == 0:
        result.message += ctx.M("\nمتن از قبل درست بود.", "\nThe text was already clean.")
    return result


@tool(
    id="persian_numbers", category="convert", name="تبدیل ارقام فارسی/لاتین",
    name_en="Convert digits",
    desc="بین ارقام فارسی، عربی و لاتین جابه‌جا می‌شود و عدد را با جداکننده‌ی هزارگان می‌نویسد.",
    desc_en="Switch between Persian, Arabic and Latin digits, with optional thousands separators.",
    icon="hash", order=201,
    keywords=("ارقام فارسی", "عدد فارسی", "persian digits", "۱۲۳", "تبدیل عدد"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "متن", "Text", visible_when=("source", "text")),
        Field("file", K_FILE, "فایل", "File", filt="متن (*.txt *.csv);;همه فایل‌ها (*)",
              visible_when=("source", "file")),
        Field("target", K_CHOICE, "خروجی", "Output", default="fa",
              choices=[("fa", "فارسی ۱۲۳", "Persian ۱۲۳"), ("en", "لاتین 123", "Latin 123"),
                       ("ar", "عربی ١٢٣", "Arabic ١٢٣")]),
        Field("thousands", K_BOOL, "جداکننده‌ی هزارگان (۱٬۲۳۴)", "Thousands separator (1,234)", default=False),
        Field("write_file", K_BOOL, "ذخیره در فایل", "Save to file", default=False),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="متن (*.txt)", visible_when=("write_file", True)),
    ],
)
def persian_numbers(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = _read_text(ctx, v)
    if not text:
        raise ToolError(ctx.M("متنی وارد نشده.", "No text provided."))
    all_digits = LATIN_DIGITS + PERSIAN_DIGITS + ARABIC_DIGITS
    latin = text.translate(str.maketrans(all_digits, LATIN_DIGITS * 3))
    target = str(v.get("target") or "fa")
    if v.get("thousands"):
        def format_number(match: re.Match) -> str:
            body = match.group(0)
            return body if body.startswith("0") else f"{int(body):,}".replace(",", "٬")
        latin = re.sub(r"\d{4,}", format_number, latin)
    if target == "fa":
        text = latin.translate(str.maketrans(LATIN_DIGITS, PERSIAN_DIGITS))
    elif target == "ar":
        text = latin.translate(str.maketrans(LATIN_DIGITS, ARABIC_DIGITS))
    else:
        text = latin
    return _emit(ctx, v, text, "_digits")


# ------------------------------------------------------------------- calendar
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


def _gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd
    for index in range(gm - 1):
        days += g_days_in_month[index]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
GREGORIAN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                    "August", "September", "October", "November", "December"]


@tool(
    id="jalali_date", category="convert", name="تاریخ شمسی ⇄ میلادی", name_en="Jalali ↔ Gregorian",
    desc="تاریخ شمسی را به میلادی و برعکس تبدیل می‌کند و اختلاف روز تا امروز را می‌گوید.",
    desc_en="Convert Jalali to Gregorian dates and back, with the day gap to today.",
    icon="calendar", order=202,
    keywords=("تاریخ شمسی", "تقویم", "jalali", "persian date", "تبدیل تاریخ", "۱۴۰۳"),
    fields=[
        Field("direction", K_CHOICE, "جهت", "Direction", default="to_gregorian",
              choices=[("to_gregorian", "شمسی → میلادی", "Jalali → Gregorian"),
                       ("to_jalali", "میلادی → شمسی", "Gregorian → Jalali"),
                       ("today", "امروز", "Today")]),
        Field("year", K_INT, "سال", "Year", default=1403, minimum=1200, maximum=2200),
        Field("month", K_INT, "ماه", "Month", default=1, minimum=1, maximum=12),
        Field("day", K_INT, "روز", "Day", default=1, minimum=1, maximum=31),
    ],
)
def jalali_date(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    today = date.today()
    jy, jm, jd = _gregorian_to_jalali(today.year, today.month, today.day)
    today_line = ctx.M(
        f"امروز: {today.year}-{today.month:02d}-{today.day:02d} میلادی = {jy}/{jm:02d}/{jd:02d} شمسی ({JALALI_MONTHS[jm - 1]})",
        f"Today: {today.year}-{today.month:02d}-{today.day:02d} = {jy}/{jm:02d}/{jd:02d} Jalali")
    direction = str(v.get("direction") or "to_gregorian")
    if direction == "today":
        return RunResult(message=today_line)
    year, month, day = int(v.get("year") or 1403), int(v.get("month") or 1), int(v.get("day") or 1)
    if direction == "to_gregorian":
        try:
            gy, gm, gd = _jalali_to_gregorian(year, month, day)
            target = date(gy, gm, gd)
        except Exception as exc:
            raise ToolError(ctx.M(f"تاریخ شمسی نامعتبر است: {exc}", f"Invalid Jalali date: {exc}"))
        delta = (target - today).days
        message = ctx.M(
            f"{year}/{month:02d}/{day:02d} شمسی = {gy}-{gm:02d}-{gd:02d} میلادی ({GREGORIAN_MONTHS[gm - 1]})",
            f"{year}/{month:02d}/{day:02d} Jalali = {gy}-{gm:02d}-{gd:02d} Gregorian")
    else:
        try:
            target = date(year, month, day)
        except Exception as exc:
            raise ToolError(ctx.M(f"تاریخ میلادی نامعتبر است: {exc}", f"Invalid Gregorian date: {exc}"))
        jy2, jm2, jd2 = _gregorian_to_jalali(year, month, day)
        delta = (target - today).days
        weekday_fa = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"][target.weekday()]
        message = ctx.M(
            f"{year}-{month:02d}-{day:02d} میلادی = {jy2}/{jm2:02d}/{jd2:02d} شمسی "
            f"({JALALI_MONTHS[jm2 - 1]}) — {weekday_fa}",
            f"{year}-{month:02d}-{day:02d} = {jy2}/{jm2:02d}/{jd2:02d} Jalali ({weekday_fa})")
    if delta == 0:
        gap = ctx.M("همین امروز است.", "It is today.")
    elif delta > 0:
        gap = ctx.M(f"{delta} روز بعد از امروز.", f"{delta} day(s) from today.")
    else:
        gap = ctx.M(f"{abs(delta)} روز قبل از امروز.", f"{abs(delta)} day(s) before today.")
    return RunResult(message=message, details=f"{today_line}\n{gap}")


@tool(
    id="persian_sort", category="convert", name="مرتب‌سازی الفبای فارسی",
    name_en="Sort Persian lines",
    desc="خطوط متن را بر اساس الفبای فارسی مرتب می‌کند (و ارقام را عددی، نه متنی).",
    desc_en="Sort lines by the Persian alphabet (numbers sorted numerically, not as text).",
    icon="sort", order=203,
    keywords=("مرتب سازی", "الفبا", "persian sort", "فارسی مرتب", "جدول نام"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "خطوط", "Lines", visible_when=("source", "text")),
        Field("file", K_FILE, "فایل", "File", filt="متن (*.txt *.csv);;همه فایل‌ها (*)",
              visible_when=("source", "file")),
        Field("descending", K_BOOL, "نزولی", "Descending", default=False),
        Field("write_file", K_BOOL, "ذخیره در فایل", "Save to file", default=False),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="متن (*.txt)", visible_when=("write_file", True)),
    ],
)
def persian_sort(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = _read_text(ctx, v)
    if not text.strip():
        raise ToolError(ctx.M("متنی وارد نشده.", "No text provided."))
    lines = [line for line in text.splitlines() if line.strip()]
    # Persian collation: آ/ا together, ي/ك folded, diacritics and ZWNJ ignored, numbers numeric
    lines.sort(key=persian_pdf.persian_sort_key, reverse=bool(v.get("descending")))
    return _emit(ctx, v, "\n".join(lines), "_sorted")


@tool(
    id="keyboard_fix", category="convert", name="اصلاح چیدمان کیبورد",
    name_en="Fix keyboard layout",
    desc="متنی که با چیدمان اشتباه تایپ شده را بین فارسی و انگلیسی تبدیل می‌کند.",
    desc_en="Convert text typed with the wrong keyboard layout between Persian and English.",
    icon="terminal", order=204,
    keywords=("کیبورد", "چیدمان", "keyboard layout", "فینگلیش", "تایپ اشتباه"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text", required=True),
        Field("direction", K_CHOICE, "جهت", "Direction", default="en_to_fa",
              choices=[("en_to_fa", "انگلیسی → فارسی (sghl → سلام)", "English → Persian"),
                       ("fa_to_en", "فارسی → انگلیسی", "Persian → English")]),
    ],
)
def keyboard_fix(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    if not text:
        raise ToolError(ctx.M("متنی وارد نشده.", "No text provided."))
    if str(v.get("direction") or "en_to_fa") == "en_to_fa":
        converted = "".join(KEYBOARD_MAP_EN_TO_FA.get(char, char) for char in text)
    else:
        converted = "".join(KEYBOARD_MAP_FA_TO_EN.get(char, char) for char in text)
    return RunResult(message=ctx.M("چیدمان متن اصلاح شد.", "Layout converted."), details=converted)
