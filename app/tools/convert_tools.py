"""Converters & text utilities: Base64, JSON, tables, units, colour, QR, UUID."""
from __future__ import annotations

import base64
import binascii
import colorsys
import csv
import datetime
import html as _html
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FLOAT, K_INT, K_MULTILINE, K_PASSWORD, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, safe_filename, unique_path
from ._helpers import (ensure_dir, first_file, out_dir, read_text_file, require, save_target,
                       text_or_file)


def _text_result(ctx: RunContext, title: str, body: str, save_field: Any = "") -> RunResult:
    outputs: List[str] = []
    if str(save_field or "").strip():
        dest = save_target(ctx, str(save_field), "output.txt")
        dest.write_text(body, encoding="utf-8")
        outputs.append(str(dest))
    return RunResult(message=title, details=body, outputs=outputs)


# -------------------------------------------------------------------- base64
@tool(
    id="base64_tool", category="convert", name="Base64", name_en="Base64",
    desc="متن یا فایل را به Base64 تبدیل می‌کند و برعکس.",
    desc_en="Encode/decode text or files to/from Base64.",
    icon="convert", order=10, keywords=("base64", "encode", "decode", "کدگذاری"),
    fields=[
        Field("mode", K_CHOICE, "عملیات", "Operation", default="encode",
              choices=[("encode", "تبدیل به Base64", "Encode"), ("decode", "بازگردانی", "Decode")]),
        Field("source", K_CHOICE, "منبع", "Source", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "متن", "Text", visible_when=("source", "text")),
        Field("file", K_FILE, "فایل", "File", visible_when=("source", "file")),
        Field("save", K_SAVE, "ذخیره در", "Save as"),
    ],
)
def base64_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    mode = str(v.get("mode") or "encode")
    if str(v.get("source") or "text") == "text":
        raw = str(v.get("text") or "")
        if not raw:
            raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
        if mode == "encode":
            body = base64.b64encode(raw.encode("utf-8")).decode()
        else:
            try:
                body = base64.b64decode(raw.strip(), validate=True).decode("utf-8", errors="replace")
            except (binascii.Error, ValueError):
                raise ToolError(ctx.M("این رشته Base64 معتبر نیست.", "Not valid Base64."))
        return _text_result(ctx, ctx.M("نتیجه Base64", "Base64 result"), body, v.get("save"))
    path = first_file(v, "file", ctx)
    if mode == "encode":
        body = base64.b64encode(path.read_bytes()).decode()
        return _text_result(ctx, ctx.M(f"{path.name} → Base64 ({human_size(path.stat().st_size)})",
                                       f"{path.name} → Base64 ({human_size(path.stat().st_size)})"),
                            body, v.get("save"))
    raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        raise ToolError(ctx.M("محتوای فایل Base64 معتبر نیست.", "File content is not valid Base64."))
    dest = save_target(ctx, str(v.get("save") or ""), f"{path.stem}.decoded")
    dest.write_bytes(data)
    return RunResult(message=ctx.M(f"{human_size(len(data))} بازگردانی شد.", f"{human_size(len(data))} decoded."),
                     outputs=[str(dest)], open_target=str(dest))


# ----------------------------------------------------------------------- json
@tool(
    id="json_tool", category="convert", name="JSON ساز", name_en="JSON tools",
    desc="JSON را مرتب، کوچک یا اعتبارسنجی می‌کند و خطاها را نشان می‌دهد.",
    desc_en="Pretty-print, minify or validate JSON with error positions.",
    icon="text", order=20, keywords=("json", "pretty", "validate", "مرتب کردن"),
    fields=[
        Field("mode", K_CHOICE, "عملیات", "Operation", default="pretty",
              choices=[("pretty", "مرتب‌سازی (2 فاصله)", "Pretty (2 spaces)"),
                       ("minify", "کوچک‌سازی", "Minify"), ("validate", "فقط اعتبارسنجی", "Validate only")]),
        Field("text", K_MULTILINE, "JSON", "JSON"),
        Field("file", K_FILE, "یا فایل JSON", "Or JSON file", filt="JSON (*.json);;همه فایل‌ها (*)"),
        Field("save", K_SAVE, "ذخیره در", "Save as", filt="JSON (*.json)"),
    ],
)
def json_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "").strip()
    file_value = str(v.get("file") or "").strip()
    if file_value:
        text = read_text_file(file_value, ctx)
    if not text:
        raise ToolError(ctx.M("JSON را وارد کنید یا فایل بدهید.", "Paste JSON or choose a file."))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return RunResult(
            message=ctx.M(f"❌ JSON نامعتبر است — خط {exc.lineno}، ستون {exc.colno}: {exc.msg}",
                          f"❌ Invalid JSON — line {exc.lineno}, column {exc.colno}: {exc.msg}"),
            details="\n".join(text.splitlines()[max(0, exc.lineno - 4): exc.lineno + 2]),
        )
    mode = str(v.get("mode") or "pretty")
    if mode == "validate":
        kind = type(data).__name__
        size = len(data) if isinstance(data, (list, dict)) else 1
        return RunResult(message=ctx.M(f"✅ JSON معتبر است ({kind}، {size} آیتم)",
                                       f"✅ Valid JSON ({kind}, {size} items)"))
    body = json.dumps(data, ensure_ascii=False, indent=2) if mode == "pretty" else json.dumps(
        data, ensure_ascii=False, separators=(",", ":"))
    return _text_result(ctx, ctx.M(f"JSON {'مرتب' if mode == 'pretty' else 'کوچک'} شد ({len(body):,} کاراکتر)",
                                   f"JSON {'formatted' if mode == 'pretty' else 'minified'} ({len(body):,} chars)"),
                        body, v.get("save"))


# ---------------------------------------------------------------------- tables
@tool(
    id="table_convert", category="convert", name="تبدیل جدول", name_en="Table converter",
    desc="CSV، Excel و JSON را به هم تبدیل می‌کند.",
    desc_en="Convert between CSV, Excel and JSON.",
    icon="list", order=30, keywords=("csv to excel", "xlsx", "json to csv", "اکسل", "جدول"),
    fields=[
        Field("src", K_FILE, "فایل ورودی", "Input file", required=True,
              filt="جدول (*.csv *.xlsx *.xls *.json);;همه فایل‌ها (*)"),
        Field("target", K_CHOICE, "خروجی", "Output", default="xlsx",
              choices=[("xlsx", "Excel (xlsx)", "Excel (xlsx)"), ("csv", "CSV", "CSV"),
                       ("json", "JSON", "JSON")]),
        Field("sheet", K_TEXT, "نام شیت (برای اکسل)", "Sheet name (Excel)", default="Sheet1"),
        Field("out", K_SAVE, "ذخیره در", "Save as"),
    ],
)
def table_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    target = str(v.get("target") or "xlsx")
    suffix = src.suffix.lower()
    rows: List[List[Any]] = []
    headers: List[str] = []
    if suffix == ".csv":
        with open(src, newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.reader(fh)
            data = list(reader)
        if data:
            headers, rows = data[0], data[1:]
    elif suffix in (".xlsx", ".xls"):
        openpyxl = require("openpyxl", "openpyxl", ctx)
        workbook = openpyxl.load_workbook(src, data_only=True)
        sheet = workbook.active
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index == 0:
                headers = [str(c) if c is not None else f"col{i + 1}" for i, c in enumerate(row)]
            else:
                rows.append(list(row))
        workbook.close()
    elif suffix == ".json":
        data = json.loads(src.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not data:
            raise ToolError(ctx.M("ساختار JSON باید لیستی از آبجکت‌ها باشد.", "JSON must be a list of objects."))
        headers = list({key for item in data if isinstance(item, dict) for key in item.keys()})
        rows = [[item.get(key, "") for key in headers] for item in data if isinstance(item, dict)]
    else:
        raise ToolError(ctx.M("فرمت پشتیبانی نمی‌شود.", "Unsupported format."))
    if not headers:
        raise ToolError(ctx.M("فایل خالی است.", "File is empty."))
    default_name = f"{src.stem}.{ 'xlsx' if target == 'xlsx' else target}"
    dest = save_target(ctx, str(v.get("out") or ""), default_name)
    if target == "xlsx":
        openpyxl = require("openpyxl", "openpyxl", ctx)
        from openpyxl.styles import Font, PatternFill

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = (str(v.get("sheet") or "Sheet1"))[:31] or "Sheet1"
        sheet.append(headers)
        for row in rows:
            sheet.append([_cell(value) for value in row])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="4C6EF5")
        sheet.freeze_panes = "A2"
        for index, header in enumerate(headers, start=1):
            width = max(10, min(48, len(str(header)) + 6))
            sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width
        workbook.save(dest)
    elif target == "csv":
        with open(dest, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            writer.writerows(rows)
    else:
        payload = [dict(zip(headers, [_cell(value) for value in row])) for row in rows]
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return RunResult(message=ctx.M(f"{len(rows)} سطر تبدیل شد.", f"{len(rows)} row(s) converted."),
                     outputs=[str(dest)], open_target=str(dest))


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# ----------------------------------------------------------------------- units
UNITS: Dict[str, Dict[str, float]] = {
    "length": {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "inch": 0.0254,
               "ft": 0.3048, "yard": 0.9144, "mile": 1609.344},
    "weight": {"mg": 1e-6, "g": 1e-3, "kg": 1.0, "ton": 1000.0, "oz": 0.0283495, "lb": 0.453592},
    "data": {"B": 1.0, "KB": 1024.0, "MB": 1024.0 ** 2, "GB": 1024.0 ** 3, "TB": 1024.0 ** 4,
             "Kbit": 128.0, "Mbit": 131072.0, "Gbit": 134217728.0},
    "area": {"cm2": 1e-4, "m2": 1.0, "km2": 1e6, "hectare": 10000.0, "ft2": 0.092903, "acre": 4046.86},
    "speed": {"m/s": 1.0, "km/h": 0.277778, "mph": 0.44704, "knot": 0.514444},
    "time": {"ms": 0.001, "s": 1.0, "min": 60.0, "hour": 3600.0, "day": 86400.0, "week": 604800.0},
    "volume": {"ml": 1e-6, "l": 1e-3, "m3": 1.0, "gallon": 0.00378541, "cup": 0.000236588},
}
TEMP_SCALE = {"c": "celsius", "f": "fahrenheit", "k": "kelvin"}


@tool(
    id="unit_convert", category="convert", name="تبدیل واحد", name_en="Unit converter",
    desc="طول، وزن، داده، دما، سرعت، مساحت، حجم و زمان را تبدیل می‌کند.",
    desc_en="Convert length, weight, data, temperature, speed, area, volume and time.",
    icon="ruler", order=40, keywords=("unit converter", "تبدیل واحد", "متر", "کیلو", "سلسیوس", "دما"),
    fields=[
        Field("category", K_CHOICE, "دسته", "Category", default="length",
              choices=[("length", "طول", "Length"), ("weight", "وزن", "Weight"),
                       ("data", "داده (بایت)", "Data"), ("temp", "دما", "Temperature"),
                       ("speed", "سرعت", "Speed"), ("area", "مساحت", "Area"),
                       ("volume", "حجم", "Volume"), ("time", "زمان", "Time")]),
        Field("value", K_FLOAT, "مقدار", "Value", default=1.0, minimum=-1e12, maximum=1e12, decimals=4),
        Field("from", K_TEXT, "از واحد", "From unit", default="m",
              help="مثال: m, cm, km, inch / kg, lb / MB, GB / c, f, k",
              help_en="e.g. m, cm, km, inch / kg, lb / MB, GB / c, f, k"),
        Field("to", K_TEXT, "به واحد", "To unit", default="cm"),
    ],
)
def unit_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    category = str(v.get("category") or "length")
    value = float(v.get("value") or 0)
    src_unit = str(v.get("from") or "").strip().lower()
    dst_unit = str(v.get("to") or "").strip().lower()
    if category == "temp":
        if src_unit not in ("c", "f", "k") or dst_unit not in ("c", "f", "k"):
            raise ToolError(ctx.M("واحد دما: c (سلسیوس)، f (فارنهایت)، k (کلوین).",
                                  "Temperature units: c, f or k."))
        celsius = {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[src_unit]
        result = {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + 273.15}[dst_unit]
        return RunResult(message=f"{value:g} °{src_unit.upper()} = {result:,.4f} °{dst_unit.upper()}",
                         details=f"{result:.6f}")
    table = UNITS.get(category)
    if not table:
        raise ToolError(ctx.M("دسته نامعتبر است.", "Invalid category."))
    norm = {key.lower(): factor for key, factor in table.items()}
    if src_unit not in norm or dst_unit not in norm:
        raise ToolError(ctx.M(
            f"واحد نامعتبر. واحدهای مجاز: {', '.join(table.keys())}",
            f"Invalid unit. Allowed: {', '.join(table.keys())}",
        ))
    result = value * norm[src_unit] / norm[dst_unit]
    return RunResult(
        message=f"{value:g} {src_unit} = {result:,.6f} {dst_unit}",
        details=f"{result:.8f}",
    )


# ---------------------------------------------------------------------- colour
@tool(
    id="color_convert", category="convert", name="تبدیل رنگ", name_en="Colour converter",
    desc="HEX، RGB و HSL را به هم تبدیل می‌کند و کدهای آماده می‌دهد.",
    desc_en="Convert between HEX, RGB and HSL, with ready-to-paste snippets.",
    icon="color", order=50, keywords=("color converter", "رنگ", "hex", "rgb", "hsl"),
    fields=[Field("color", K_TEXT, "رنگ", "Colour", required=True, default="#5B8CFF",
                  placeholder="#5B8CFF یا rgb(91,140,255) یا hsl(220,100%,68%)",
                  placeholder_en="#5B8CFF or rgb(91,140,255) or hsl(...)")],
)
def color_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("color") or "").strip()
    rgb = _parse_color(raw)
    if rgb is None:
        raise ToolError(ctx.M("رنگ نامعتبر است. نمونه: #5B8CFF یا rgb(91,140,255)",
                              "Invalid colour. e.g. #5B8CFF or rgb(91,140,255)"))
    r, g, b = rgb
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue = round(h * 360)
    lightness = round(l * 100)
    saturation = round(s * 100)
    hex_value = f"#{r:02X}{g:02X}{b:02X}"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    rows = [
        f"HEX      : {hex_value}",
        f"HEX (a)  : {hex_value}FF",
        f"RGB      : rgb({r}, {g}, {b})",
        f"RGBA     : rgba({r}, {g}, {b}, 1)",
        f"HSL      : hsl({hue}, {saturation}%, {lightness}%)",
        "HSV      : " + ", ".join(str(round(x * 100)) for x in colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)),
        "CMYK     : " + ", ".join(str(round(x * 100)) for x in _cmyk(r, g, b)),
        f"CSS name : {_nearest_name(r, g, b)}",
        ctx.M(f"بهترین رنگ متن: {'تیره' if luminance > 0.55 else 'روشن'}",
              f"Best text colour: {'dark' if luminance > 0.55 else 'light'}"),
    ]
    return RunResult(message=f"{hex_value}  ·  rgb({r}, {g}, {b})", details="\n".join(rows))


def _cmyk(r: int, g: int, b: int):
    if (r, g, b) == (0, 0, 0):
        return (0, 0, 0, 1)
    k = 1 - max(r, g, b) / 255
    c = (1 - r / 255 - k) / (1 - k)
    m = (1 - g / 255 - k) / (1 - k)
    y = (1 - b / 255 - k) / (1 - k)
    return (c, m, y, k)


_NAMED = {"#000000": "black", "#FFFFFF": "white", "#FF0000": "red", "#00FF00": "lime",
          "#0000FF": "blue", "#FFFF00": "yellow", "#FFA500": "orange", "#800080": "purple",
          "#808080": "gray", "#00FFFF": "cyan", "#FF00FF": "magenta", "#5B8CFF": "cornflower-ish"}


def _nearest_name(r: int, g: int, b: int) -> str:
    best, best_dist = "custom", 1e9
    for key in _NAMED:
        cr, cg, cb = int(key[1:3], 16), int(key[3:5], 16), int(key[5:7], 16)
        dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if dist < best_dist:
            best, best_dist = _NAMED[key], dist
    return best


def _parse_color(raw: str):
    text = raw.strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", text):
        text = text.lstrip("#")
        return tuple(int(ch * 2, 16) for ch in text)
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
        text = text.lstrip("#")
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    match = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", text)
    if match:
        return tuple(max(0, min(255, int(x))) for x in match.groups())
    match = re.search(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%", text)
    if match:
        h, s, l = (float(match.group(1)) / 360, float(match.group(2)) / 100, float(match.group(3)) / 100)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return (round(r * 255), round(g * 255), round(b * 255))
    if text.lower() in _NAMED:
        key = text.upper()
        return (int(key[1:3], 16), int(key[3:5], 16), int(key[5:7], 16))
    return None


# -------------------------------------------------------------------------- QR
def _escape_wifi_qr(value: str) -> str:
    return (value.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace(":", "\\:")
            .replace('"', '\\"'))


@tool(
    id="qr_generate", category="convert", name="ساخت QR کد", name_en="QR code",
    desc="از متن، لینک یا وای‌فای یک QR کد PNG/SVG می‌سازد.",
    desc_en="Generate a PNG/SVG QR code from text, a link or Wi-Fi credentials.",
    icon="qr", order=60, keywords=("qr code", "کیو ار", "barcode", "وای فای"),
    fields=[
        Field("mode", K_CHOICE, "محتوا", "Content", default="text",
              choices=[("text", "متن / لینک", "Text / link"), ("wifi", "اتصال وای‌فای", "Wi-Fi")]),
        Field("text", K_TEXT, "متن یا لینک", "Text or link", default="https://",
              visible_when=("mode", "text")),
        Field("ssid", K_TEXT, "نام شبکه (SSID)", "SSID", visible_when=("mode", "wifi")),
        Field("password", K_PASSWORD, "رمز وای‌فای", "Wi-Fi password", visible_when=("mode", "wifi")),
        Field("size", K_INT, "اندازه (px)", "Size (px)", default=600, minimum=100, maximum=2000),
        Field("color", K_TEXT, "رنگ (HEX)", "Colour (HEX)", default="#101828"),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PNG (*.png);;SVG (*.svg)"),
    ],
)
def qr_generate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    qrcode = require("qrcode", "qrcode", ctx)
    if str(v.get("mode") or "text") == "wifi":
        ssid = str(v.get("ssid") or "").strip()
        if not ssid:
            raise ToolError(ctx.M("نام شبکه را وارد کنید.", "Enter the SSID."))
        pw = str(v.get("password") or "")
        payload = f"WIFI:T:WPA;S:{_escape_wifi_qr(ssid)};P:{_escape_wifi_qr(pw)};;"
        name = f"wifi_{safe_filename(ssid)}"
    else:
        payload = str(v.get("text") or "").strip()
        if not payload:
            raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
        name = "qr_code"
    dest = save_target(ctx, str(v.get("out") or ""), f"{name}.png")
    color = str(v.get("color") or "#101828").strip()
    if not re.fullmatch(r"#?[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", color):
        raise ToolError(ctx.M(f"رنگ نامعتبر است: {color} — یک کد HEX مثل #101828 بدهید.",
                              f"Invalid colour: {color} — use a HEX value like #101828."))
    if not color.startswith("#"):
        color = "#" + color
    size = int(v.get("size") or 600)
    if not 100 <= size <= 2000:
        size = max(100, min(2000, size))
    if dest.suffix.lower() == ".svg":
        import qrcode.image.svg as svg_factory

        image = qrcode.make(payload, image_factory=svg_factory.SvgPathImage, box_size=max(2, size // 40))
        image.save(str(dest))
    else:
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=10, border=3)
        qr.add_data(payload)
        qr.make(fit=True)
        image = qr.make_image(fill_color=color, back_color="white")
        image = image.resize((size, size)) if hasattr(image, "resize") else image
        image.save(str(dest))
    return RunResult(message=ctx.M("QR کد ساخته شد.", "QR code created."),
                     outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------------------------ UUID
@tool(
    id="uuid_generate", category="convert", name="ساخت UUID", name_en="UUID generator",
    desc="شناسه‌های یکتا (UUID v4) و GUID ویندوزی می‌سازد.",
    desc_en="Generate UUID v4 identifiers (and Windows GUID style).",
    icon="hash", order=70, keywords=("uuid", "guid", "شناسه یکتا"),
    fields=[
        Field("count", K_INT, "تعداد", "Count", default=5, minimum=1, maximum=200),
        Field("upper", K_BOOL, "حروف بزرگ", "Uppercase", default=False),
        Field("braces", K_BOOL, "داخل آکولاد { }", "Wrap in braces { }", default=False),
        Field("nodash", K_BOOL, "بدون خط تیره", "Without dashes", default=False),
    ],
)
def uuid_generate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    count = int(v.get("count") or 5)
    items: List[str] = []
    for _ in range(count):
        value = str(uuid.uuid4())
        if v.get("nodash"):
            value = value.replace("-", "")
        if v.get("upper"):
            value = value.upper()
        if v.get("braces"):
            value = "{" + value + "}"
        items.append(value)
    return RunResult(message=ctx.M(f"{count} شناسه یکتا", f"{count} identifier(s)"), details="\n".join(items))


# ------------------------------------------------------------------ text tools
PERSIAN_MAP = {"ي": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "أ": "ا", "إ": "ا", "ؤ": "و",
               "ئ": "ی", "\u064b": "", "\u064c": "", "\u064d": "", "\u064e": "", "\u064f": "",
               "\u0650": "", "\u0651": "", "\u0652": "", "٫": ".", "٬": ","}


@tool(
    id="text_transform", category="convert", name="پردازش متن", name_en="Text tools",
    desc="اصلاح متن فارسی، تبدیل اعداد، حذف خطوط خالی، مرتب‌سازی خطوط و تغییر حروف بزرگ/کوچک.",
    desc_en="Persian text clean-up, digit conversion, line sorting and case changes.",
    icon="text", order=80, keywords=("text tools", "متن", "اصلاح متن", "اعداد فارسی", "case"),
    fields=[
        Field("operation", K_CHOICE, "عملیات", "Operation", default="persian",
              choices=[
                  ("persian", "اصلاح حروف عربی → فارسی", "Fix Arabic → Persian letters"),
                  ("fa_digits", "اعداد انگلیسی → فارسی", "Digits → Persian"),
                  ("en_digits", "اعداد فارسی → انگلیسی", "Digits → English"),
                  ("lower", "همه کوچک (lowercase)", "lowercase"),
                  ("upper", "همه بزرگ (UPPERCASE)", "UPPERCASE"),
                  ("title", "حرف اول هر کلمه بزرگ", "Title Case"),
                  ("trim", "حذف فاصله‌های اضافی", "Collapse spaces"),
                  ("empty_lines", "حذف خطوط خالی", "Remove empty lines"),
                  ("sort", "مرتب‌سازی خطوط", "Sort lines"),
                  ("unique", "حذف خطوط تکراری", "Remove duplicate lines"),
                  ("reverse", "برعکس کردن خطوط", "Reverse lines"),
                  ("half_space", "اصلاح نیم‌فاصله‌های رایج", "Fix common half-spaces"),
              ]),
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل متنی", "Or text file", filt="Text (*.txt *.md *.csv *.log);;همه فایل‌ها (*)"),
        Field("save", K_SAVE, "ذخیره در", "Save as"),
    ],
)
def text_transform(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    file_value = str(v.get("file") or "").strip()
    if file_value:
        text = read_text_file(file_value, ctx)
    if not text:
        raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
    op = str(v.get("operation") or "persian")
    if op == "persian":
        text = "".join(PERSIAN_MAP.get(ch, ch) for ch in text)
    elif op == "fa_digits":
        text = text.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    elif op == "en_digits":
        text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    elif op == "lower":
        text = text.lower()
    elif op == "upper":
        text = text.upper()
    elif op == "title":
        text = "\n".join(line.title() for line in text.splitlines())
    elif op == "trim":
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    elif op == "empty_lines":
        text = "\n".join(line for line in text.splitlines() if line.strip())
    elif op == "sort":
        text = "\n".join(sorted(text.splitlines(), key=str.lower))
    elif op == "unique":
        seen, lines = set(), []
        for line in text.splitlines():
            if line not in seen:
                seen.add(line)
                lines.append(line)
        text = "\n".join(lines)
    elif op == "reverse":
        text = "\n".join(reversed(text.splitlines()))
    elif op == "half_space":
        for pair in ("\u0647 \u0627\u06cc", "\u0645\u06cc ", "\u0646\u0645\u06cc ", "\u0647\u0627 \u06cc"):
            text = text.replace(pair, pair.replace(" ", "\u200c"))
    stats = ctx.M(f"{len(text):,} کاراکتر · {len(text.splitlines()):,} خط",
                  f"{len(text):,} chars · {len(text.splitlines()):,} lines")
    return _text_result(ctx, stats, text, v.get("save"))


# ----------------------------------------------------------------- timestamps
@tool(
    id="timestamp_convert", category="convert", name="تبدیل زمان", name_en="Timestamp",
    desc="تاریخ میلادی ↔ تایم‌استمپ یونیکس را با زمان محلی و UTC تبدیل می‌کند.",
    desc_en="Convert between dates and Unix timestamps (local & UTC).",
    icon="clock", order=90, keywords=("timestamp", "unix time", "تاریخ", "زمان"),
    fields=[
        Field("mode", K_CHOICE, "ورودی", "Input", default="now",
              choices=[("now", "همین حالا", "Now"), ("ts", "تایم‌استمپ", "Timestamp"),
                       ("date", "تاریخ (YYYY-MM-DD HH:MM)", "Date (YYYY-MM-DD HH:MM)")]),
        Field("value", K_TEXT, "مقدار", "Value", default="", visible_when=("mode", ("ts", "date"))),
    ],
)
def timestamp_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    mode = str(v.get("mode") or "now")
    value = str(v.get("value") or "").strip()
    if mode == "now":
        stamp = datetime.datetime.now()
    elif mode == "ts":
        try:
            number = float(value)
        except ValueError:
            raise ToolError(ctx.M("تایم‌استمپ باید عدد باشد.", "Timestamp must be a number."))
        if number > 1e11:
            number /= 1000.0
        stamp = datetime.datetime.fromtimestamp(number)
    else:
        try:
            stamp = datetime.datetime.fromisoformat(value.replace("/", "-"))
        except ValueError:
            raise ToolError(ctx.M("قالب تاریخ درست نیست. نمونه: 2025-01-31 14:30",
                                  "Wrong date format. e.g. 2025-01-31 14:30"))
    rows = [
        f"{ctx.M('یونیکس (ثانیه)', 'Unix (s)')} : {int(stamp.timestamp())}",
        f"{ctx.M('یونیکس (میلی‌ثانیه)', 'Unix (ms)')} : {int(stamp.timestamp() * 1000)}",
        f"{ctx.M('محلی', 'Local')}              : {stamp:%Y-%m-%d %H:%M:%S}",
        f"UTC                : {datetime.datetime.fromtimestamp(stamp.timestamp(), tz=datetime.timezone.utc):%Y-%m-%d %H:%M:%S}",
        f"ISO 8601           : {stamp.isoformat()}",
        f"{ctx.M('روز هفته', 'Weekday')}          : {stamp:%A}",
    ]
    return RunResult(message=f"{stamp:%Y-%m-%d %H:%M:%S}", details="\n".join(rows))


# ---------------------------------------------------------------- markdown
@tool(
    id="markdown_to_html", category="convert", name="مارک‌داون به HTML", name_en="Markdown → HTML",
    desc="فایل یا متن Markdown را به HTML آماده‌ی مرورگر تبدیل می‌کند.",
    desc_en="Convert Markdown text/files into browser-ready HTML.",
    icon="doc", order=100, keywords=("markdown", "md to html", "html", "مارک داون"),
    fields=[
        Field("text", K_MULTILINE, "Markdown", "Markdown"),
        Field("file", K_FILE, "یا فایل .md", "Or .md file", filt="Markdown (*.md *.markdown *.txt)"),
        Field("styled", K_BOOL, "قالب‌بندی زیبا", "Include pretty styling", default=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="HTML (*.html)"),
    ],
)
def markdown_to_html(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    file_value = str(v.get("file") or "").strip()
    if file_value:
        text = read_text_file(file_value, ctx)
    if not text.strip():
        raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
    try:
        markdown = require("markdown", "markdown", ctx)
        body = markdown.markdown(text, extensions=["extra", "tables", "fenced_code", "nl2br", "sane_lists"])
    except ToolError:
        body = "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    style = "" if not v.get("styled") else """
<style>
 body{max-width:820px;margin:40px auto;padding:0 20px;font-family:Vazirmatn,Segoe UI,Tahoma,sans-serif;
      line-height:1.9;color:#1c2333;background:#fff}
 h1,h2,h3{color:#12224a;border-bottom:1px solid #e5e9f2;padding-bottom:.3em}
 code{background:#f2f4f9;padding:2px 6px;border-radius:6px;font-family:Consolas,monospace}
 pre{background:#0f1526;color:#e7ecff;padding:16px;border-radius:12px;overflow:auto}
 blockquote{border-left:4px solid #5B8CFF;margin:0;padding:.4em 1em;background:#f7f9ff;color:#3b4661}
 table{border-collapse:collapse;width:100%} th,td{border:1px solid #e1e6f0;padding:8px}
 img{max-width:100%}
</style>"""
    title_text = _html.escape(Path(file_value).stem) if file_value else "Document"
    html = f"""<!DOCTYPE html>
<html lang="fa" dir="auto"><head><meta charset="utf-8">
<title>{title_text}</title>{style}</head>
<body>{body}</body></html>"""
    default_stem = safe_filename(Path(file_value).stem) if file_value else "document"
    dest = save_target(ctx, str(v.get("out") or ""), f"{default_stem}.html")
    dest.write_text(html, encoding="utf-8")
    return RunResult(message=ctx.M("HTML ساخته شد.", "HTML created."), outputs=[str(dest)], open_target=str(dest))
