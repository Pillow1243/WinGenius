"""Extra developer utilities: minifying, SQL, YAML/TOML, .env, semver, slugs, CSV→SQL."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_DIR, K_FILE, K_MULTILINE, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir
from ._helpers import read_text_file, save_target

# --------------------------------------------------------------------- minify
def _minify_css(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{}:;,>])\s*", r"\1", text)
    return text.replace(";}", "}").strip()


def _minify_js(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith("//")]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _minify_html(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r">\s+<", "><", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _minify_json(text: str) -> str:
    return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))


MINIFIERS = {"css": _minify_css, "js": _minify_js, "html": _minify_html, "json": _minify_json}


@tool(
    id="minify_code", category="dev", name="فشرده‌سازی کد (Minify)", name_en="Minify code",
    desc="CSS، JavaScript، HTML یا JSON را بدون تغییر رفتار فشرده می‌کند و درصد کاهش را می‌گوید.",
    desc_en="Minifies CSS, JavaScript, HTML or JSON and reports how much smaller it got.",
    icon="code", order=40,
    keywords=("minify", "compress code", "فشرده", "کد", "css", "javascript", "html"),
    fields=[
        Field("kind", K_CHOICE, "نوع", "Kind", default="css",
              choices=[("css", "CSS", "CSS"), ("js", "JavaScript", "JavaScript"),
                       ("html", "HTML", "HTML"), ("json", "JSON", "JSON")]),
        Field("file", K_FILE, "فایل", "File", filt="Code (*.css *.js *.html *.json *.txt)"),
        Field("text", K_MULTILINE, "یا متن", "or text"),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def minify_code(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    kind = str(v.get("kind") or "css")
    raw = str(v.get("text") or "")
    if not raw.strip():
        path = str(v.get("file") or "").strip()
        if not path or not Path(path).exists():
            raise ToolError(ctx.M("فایل یا متن را بدهید.", "Provide a file or some text."))
        raw = read_text_file(path, ctx)
    try:
        result = MINIFIERS[kind](raw)
    except json.JSONDecodeError as exc:
        raise ToolError(ctx.M(f"JSON نامعتبر است: {exc}", f"Invalid JSON: {exc}"))
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, f"minified.{kind}")
        target.write_text(result, encoding="utf-8")
        saved.append(str(target))
    saved_percent = max(0.0, 100 - len(result) * 100 / max(1, len(raw)))
    return RunResult(
        message=ctx.M(f"{saved_percent:.1f}% کوچک‌تر شد ({len(raw):,} → {len(result):,}).",
                      f"{saved_percent:.1f}% smaller ({len(raw):,} → {len(result):,})."),
        details=result[:4000], outputs=saved)


# ------------------------------------------------------------------------ sql
SQL_KEYWORDS = ("select", "from", "where", "join", "left", "right", "inner", "outer", "on",
                "group by", "order by", "having", "limit", "insert into", "values", "update",
                "set", "delete from", "union", "offset", "and", "or")


@tool(
    id="sql_format", category="dev", name="مرتب‌سازی SQL", name_en="Format SQL",
    desc="دستور SQL را با شکست خط و تودرتویی خوانا می‌کند.",
    desc_en="Pretty-prints an SQL statement with line breaks and indentation.",
    icon="database", order=41, keywords=("sql", "format", "query", "کوئری", "مرتب", "پایگاه داده"),
    fields=[Field("sql", K_MULTILINE, "دستور SQL", "SQL", required=True),
            Field("out", K_SAVE, "ذخیره در", "Save to", filt="SQL (*.sql *.txt)")],
)
def sql_format(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    sql = str(v.get("sql") or "").strip()
    if not sql:
        raise ToolError(ctx.M("دستور SQL را وارد کنید.", "Enter an SQL statement."))
    text = re.sub(r"\s+", " ", sql)
    for keyword in sorted(SQL_KEYWORDS, key=len, reverse=True):
        pattern = re.compile(rf"\b{keyword}\b", re.I)
        replacement = keyword.upper() if keyword in ("and", "or", "on", "set") else "\n" + keyword.upper()
        text = pattern.sub(replacement, text)
    text = re.sub(r"\n\s*\n", "\n", text)
    text = re.sub(r"\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|UNION|OFFSET)\b",
                  lambda m: m.group(1), text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    formatted = "\n  ".join(lines)
    formatted = re.sub(r"\n  (SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|UNION|OFFSET|JOIN|LEFT|RIGHT|INNER|OUTER)\b",
                       r"\n\1", formatted)
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, "formatted.sql")
        target.write_text(formatted, encoding="utf-8")
        saved.append(str(target))
    return RunResult(message=ctx.M("SQL مرتب شد.", "SQL formatted."), details=formatted, outputs=saved)


# ----------------------------------------------------------------- json / yaml
@tool(
    id="json_yaml", category="dev", name="JSON ⇄ YAML ⇄ TOML", name_en="JSON ⇄ YAML ⇄ TOML",
    desc="بین JSON، YAML و TOML تبدیل می‌کند (اگر PyYAML نصب نباشد، تبدیل ساده‌ی داخلی انجام می‌شود).",
    desc_en="Converts between JSON, YAML and TOML (falls back to a built-in writer without PyYAML).",
    icon="convert", order=42, keywords=("yaml", "yml", "toml", "json", "config", "تبدیل"),
    fields=[
        Field("source", K_CHOICE, "از", "From", default="json",
              choices=[("json", "JSON", "JSON"), ("yaml", "YAML", "YAML"), ("toml", "TOML", "TOML")]),
        Field("target", K_CHOICE, "به", "To", default="yaml",
              choices=[("json", "JSON", "JSON"), ("yaml", "YAML", "YAML"), ("toml", "TOML", "TOML")]),
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Config (*.json *.yaml *.yml *.toml)"),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def json_yaml(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("text") or "")
    if not raw.strip():
        path = str(v.get("file") or "").strip()
        if path and Path(path).exists():
            raw = read_text_file(path, ctx)
    if not raw.strip():
        raise ToolError(ctx.M("متن یا فایل را بدهید.", "Provide a text or a file."))
    source = str(v.get("source") or "json")
    target = str(v.get("target") or "yaml")
    try:
        if source == "json":
            data = json.loads(raw)
        elif source == "yaml":
            import yaml

            data = yaml.safe_load(raw)
        else:
            import tomllib

            data = tomllib.loads(raw)
    except ImportError as exc:
        raise ToolError(ctx.M(f"کتابخانه‌ی لازم نصب نیست: {exc}",
                              f"Required library missing: {exc}"))
    except Exception as exc:
        raise ToolError(ctx.M(f"ساختار ورودی معتبر نیست: {exc}", f"Invalid input: {exc}"))
    if target == "json":
        output = json.dumps(data, ensure_ascii=False, indent=2)
    elif target == "yaml":
        try:
            import yaml

            output = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        except ImportError:
            output = _simple_yaml(data)
    else:
        output = _simple_toml(data)
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target_path = save_target(ctx, requested, f"converted.{target}")
        target_path.write_text(output, encoding="utf-8")
        saved.append(str(target_path))
    return RunResult(message=ctx.M(f"{source.upper()} → {target.upper()} آماده است.",
                                   f"{source.upper()} → {target.upper()} ready."),
                     details=output[:4000], outputs=saved)


def _simple_yaml(data: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(_simple_yaml(value, indent + 2))
            else:
                lines.append(f"{pad}{key}: {json.dumps(value, ensure_ascii=False)}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(_simple_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {json.dumps(item, ensure_ascii=False)}")
        return "\n".join(lines)
    return f"{pad}{json.dumps(data, ensure_ascii=False)}"


def _simple_toml(data: Any) -> str:
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)
    lines: List[str] = []
    tables: List[tuple] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list):
            lines.append(f"{key} = [")
            for item in value:
                lines.append(f"  {json.dumps(item, ensure_ascii=False)},")
            lines.append("]")
        else:
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    for key, value in tables:
        lines.append(f"\n[{key}]")
        for sub_key, sub_value in value.items():
            if isinstance(sub_value, (dict, list)):
                lines.append(f"{sub_key} = {json.dumps(sub_value, ensure_ascii=False)}")
            else:
                lines.append(f"{sub_key} = {json.dumps(sub_value, ensure_ascii=False)}")
    return "\n".join(lines)


# -------------------------------------------------------------------- env file
SECRET_HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "private", "access_key")


@tool(
    id="env_check", category="dev", name="بررسی فایل .env", name_en="Check a .env file",
    desc="کلیدهای تکراری، مقدارهای خالی و مقادیر حساسِ بدون نقل‌قول را در فایل .env پیدا می‌کند.",
    desc_en="Finds duplicate keys, empty values and unsafe secret formatting in a .env file.",
    icon="shield", order=43, keywords=("env", "dotenv", "config", "متغیر", "تنظیمات", "بررسی"),
    fields=[Field("file", K_FILE, "فایل .env", "File", required=True, filt="Env (*.env *.txt *)")],
)
def env_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    path = Path(str(v.get("file") or ""))
    if not path.exists():
        raise ToolError(ctx.M("فایل پیدا نشد.", "File not found."))
    seen: Dict[str, int] = {}
    problems: List[str] = []
    keys = 0
    for number, line in enumerate(read_text_file(path, ctx).splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            problems.append(f"خط {number}: بدون «=» → {stripped[:60]}")
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        keys += 1
        if key in seen:
            problems.append(f"خط {number}: کلید تکراری «{key}» (اولین بار خط {seen[key]})")
        seen[key] = number
        if not value:
            problems.append(f"خط {number}: «{key}» مقدار خالی دارد")
        if any(hint in key.lower() for hint in SECRET_HINTS) and value and not value.startswith(("'", '"')):
            problems.append(f"خط {number}: «{key}» حساس است؛ مقدار را در نقل‌قول بگذارید")
        if value.startswith((" ", "'")) or value.endswith((" ", "'")):
            problems.append(f"خط {number}: فاصله/نقل‌قول اضافه در «{key}»")
    details = ("\n".join(problems) if problems
               else ctx.M("همه‌چیز مرتب است.", "Everything looks fine."))
    message = (ctx.M(f"{keys} کلید بررسی شد · {len(problems)} هشدار",
                     f"{keys} keys checked · {len(problems)} warning(s)"))
    return RunResult(message=message, details=f"{keys} keys\n\n{details}")


# ---------------------------------------------------------------------- semver
def _parse_version(text: str) -> tuple:
    numbers = re.findall(r"\d+", str(text))
    parts = [int(number) for number in numbers[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


@tool(
    id="semver_compare", category="dev", name="مقایسه‌ی نسخه‌ها", name_en="Compare versions",
    desc="دو نسخه (۱.۲.۳، v2.0، 1.2.3-beta) را مقایسه می‌کند و می‌گوید کدام جدیدتر است.",
    desc_en="Compares two versions (1.2.3, v2.0, 1.2.3-beta) and says which one is newer.",
    icon="tag", order=44, keywords=("version", "semver", "compare", "نسخه", "مقایسه"),
    fields=[
        Field("a", K_TEXT, "نسخه‌ی اول", "First version", required=True, default="1.0.0"),
        Field("b", K_TEXT, "نسخه‌ی دوم", "Second version", required=True, default="1.0.1"),
    ],
)
def semver_compare(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    a, b = str(v.get("a") or ""), str(v.get("b") or "")
    left, right = _parse_version(a), _parse_version(b)
    if left == right:
        message = ctx.M(f"«{a}» و «{b}» برابرند.", f"“{a}” and “{b}” are equal.")
        symbol = "="
    elif left > right:
        message = ctx.M(f"«{a}» جدیدتر است.", f"“{a}” is newer.")
        symbol = ">"
    else:
        message = ctx.M(f"«{b}» جدیدتر است.", f"“{b}” is newer.")
        symbol = "<"
    details = f"{a} {symbol} {b}\n\n{left} vs {right}"
    return RunResult(message=message, details=details)


# ----------------------------------------------------------------- html escape
@tool(
    id="html_escape", category="dev", name="HTML: فرار و برچسب‌زدایی", name_en="HTML escape / strip",
    desc="کاراکترهای خطرناک HTML را امن می‌کند یا برچسب‌ها را حذف می‌کند و متن خالص می‌دهد.",
    desc_en="Escapes HTML special characters or strips tags down to plain text.",
    icon="code", order=45, keywords=(" html", "escape", "strip", "برچسب", "امن", "متن خالص"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text", required=True),
        Field("mode", K_CHOICE, "کار", "Mode", default="escape",
              choices=[("escape", "امن کردن", "Escape"), ("unescape", "برگرداندن", "Unescape"),
                       ("strip", "حذف برچسب‌ها", "Strip tags")]),
    ],
)
def html_escape(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import html

    text = str(v.get("text") or "")
    mode = str(v.get("mode") or "escape")
    if mode == "escape":
        output = html.escape(text, quote=True)
    elif mode == "unescape":
        output = html.unescape(text)
    else:
        output = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
        output = re.sub(r"<style.*?</style>", " ", output, flags=re.S | re.I)
        output = re.sub(r"<[^>]+>", " ", output)
        output = re.sub(r"\s+", " ", html.unescape(output)).strip()
    return RunResult(message=ctx.M("انجام شد.", "Done."), details=output[:4000])


# ------------------------------------------------------------------- csv → sql
@tool(
    id="csv_to_sql", category="dev", name="CSV → دستورهای SQL", name_en="CSV → SQL inserts",
    desc="از یک فایل CSV، دستورهای INSERT (و CREATE TABLE حدسی) می‌سازد.",
    desc_en="Turns a CSV file into INSERT statements (plus a guessed CREATE TABLE).",
    icon="database", order=46, keywords=("csv", "sql", "insert", "database", "تبدیل", "پایگاه داده"),
    fields=[
        Field("file", K_FILE, "فایل CSV", "CSV file", required=True, filt="CSV (*.csv *.tsv)"),
        Field("table", K_TEXT, "نام جدول", "Table name", default="data"),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="SQL (*.sql *.txt)"),
    ],
)
def csv_to_sql(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    path = Path(str(v.get("file") or ""))
    if not path.exists():
        raise ToolError(ctx.M("فایل CSV پیدا نشد.", "CSV file not found."))
    if path.is_dir():
        raise ToolError(ctx.M("این یک پوشه است، نه فایل CSV.", "That is a folder, not a CSV file."))
    table = re.sub(r"\W", "_", str(v.get("table") or "data")) or "data"
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=delimiter))
    except OSError as exc:
        raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read the file: {exc}"))
    if not rows:
        raise ToolError(ctx.M("فایل خالی است.", "The file is empty."))
    header = [re.sub(r"\W", "_", name.strip()) or f"col{index}" for index, name in enumerate(rows[0])]
    buffer = io.StringIO()
    buffer.write(f"CREATE TABLE {table} (\n  " + ",\n  ".join(f"{name} TEXT" for name in header)
                 + "\n);\n\n")
    for row in rows[1:]:
        cells = []
        for index, name in enumerate(header):
            value = (row[index] if index < len(row) else "").strip()
            if re.fullmatch(r"-?\d+", value):
                cells.append(value)
            else:
                cells.append("'" + value.replace("'", "''") + "'")
        buffer.write(f"INSERT INTO {table} ({', '.join(header)}) VALUES ({', '.join(cells)});\n")
    sql = buffer.getvalue()
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, f"{table}.sql")
        target.write_text(sql, encoding="utf-8")
        saved.append(str(target))
    return RunResult(message=ctx.M(f"{len(rows) - 1} ردیف به SQL تبدیل شد.",
                                   f"{len(rows) - 1} rows converted to SQL."),
                     details=sql[:4000], outputs=saved)


# ---------------------------------------------------------------------- slugify
@tool(
    id="slugify", category="dev", name="ساخت Slug و نام فایل امن", name_en="Slug / safe file name",
    desc="از عنوان فارسی یا انگلیسی، نامک (slug) و نام فایل امن برای ویندوز می‌سازد.",
    desc_en="Turns any title into a URL slug and a Windows-safe file name.",
    icon="link", order=47, keywords=("slug", "url", "filename", "نامک", "لینک", "نام فایل"),
    fields=[Field("text", K_TEXT, "عنوان", "Title", required=True)],
)
def slugify(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "").strip()
    if not text:
        raise ToolError(ctx.M("عنوان را وارد کنید.", "Enter a title."))
    lowered = text.lower().replace("ي", "ی").replace("ك", "ک")
    slug = re.sub(r"[^\wآ-ی]+", "-", lowered, flags=re.U).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    safe = safe[:120] or "file"
    details = f"slug: {slug}\nfile: {safe}\nlen: {len(text)}"
    return RunResult(message=slug[:60], details=details)
