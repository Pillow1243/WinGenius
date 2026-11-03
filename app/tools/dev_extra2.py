"""Developer extras: log triage, git archaeology, CSV joins, JSONL and curl conversion."""
from __future__ import annotations

import csv
import io
import json
import re
import shlex
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_DIR, K_FILE, K_INT, K_MULTILINE, K_SAVE, K_TEXT, Field, RunContext, RunResult,
    ToolError,
)
from ..core.utils import ensure_dir, human_size, unique_path
from ._helpers import first_file, out_dir, read_text_file, save_target

LEVELS = ("FATAL", "CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG", "TRACE", "NOTICE")
TIMESTAMP_RE = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


# --------------------------------------------------------------------- log triage
def _signature(message: str) -> str:
    """Collapse numbers/ids so `timeout 1203ms` and `timeout 88ms` count as one."""
    text = re.sub(r"0x[0-9a-fA-F]+", "0x…", message)
    text = re.sub(r"\b[0-9a-fA-F]{8,}\b", "…", text)
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160]


@tool(
    id="log_analyzer", category="dev", name="تحلیل فایل لاگ",
    name_en="Analyse a log file",
    desc="لاگ را دسته‌بندی می‌کند: تعداد هر سطح، پرتکرارترین خطاها (با نادیده‌گرفتن اعداد) و بازه‌ی زمانی.",
    desc_en="Groups a log by level, ranks the most frequent errors (numbers normalised) and shows the time range.",
    icon="report", order=50,
    keywords=("log", "analyze", "error", "level", "traceback", "لاگ", "تحلیل", "خطا", "گزارش"),
    fields=[
        Field("src", K_FILE, "فایل لاگ", "Log file", required=True),
        Field("top", K_INT, "چند خطای مهم", "How many top errors", default=10,
              minimum=1, maximum=100),
        Field("out", K_SAVE, "ذخیره‌ی گزارش", "Save the report", filt="Text (*.txt *.md)"),
    ],
)
def log_analyzer(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    source = first_file(v, "src", ctx)
    text = read_text_file(source, ctx, encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        raise ToolError(ctx.M("این فایل خالی است.", "This file is empty."))
    levels: Counter = Counter()
    errors: Counter = Counter()
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    first_stamp = ""
    last_stamp = ""
    error_lines = 0
    for index, line in enumerate(lines, start=1):
        ctx.progress(index, len(lines), "خواندن") if index % 500 == 0 else None
        upper = line.upper()
        level = ""
        for candidate in LEVELS:
            if re.search(rf"\b{candidate}\b", upper):
                level = "WARN" if candidate == "WARNING" else candidate
                break
        if level:
            levels[level] += 1
        stamp_match = TIMESTAMP_RE.search(line)
        stamp = stamp_match.group("stamp") if stamp_match else ""
        if stamp:
            first_stamp = first_stamp or stamp
            last_stamp = stamp
        if level in ("ERROR", "FATAL", "CRITICAL") or "TRACEBACK" in upper or "EXCEPTION" in upper:
            error_lines += 1
            signature = _signature(line)
            errors[signature] += 1
            first_seen.setdefault(signature, stamp or f"line {index}")
            last_seen[signature] = stamp or f"line {index}"
    top = int(v.get("top") or 10)
    level_text = " · ".join(f"{name}: {count}" for name, count in levels.most_common()) or "—"
    report_lines = [
        f"file        {source}",
        f"size        {human_size(source.stat().st_size)}",
        f"lines       {len(lines):,}",
        f"levels      {level_text}",
        f"error lines {error_lines:,}",
    ]
    if first_stamp:
        report_lines.append(f"time range  {first_stamp}  →  {last_stamp}")
    report_lines.append("")
    report_lines.append(("پرتکرارترین خطاها:" if ctx.lang != "en" else "Top error signatures:"))
    for rank, (signature, count) in enumerate(errors.most_common(top), start=1):
        report_lines.append(f"{rank:2}. ×{count:<5} {signature}")
        report_lines.append(f"     {first_seen[signature]} → {last_seen[signature]}")
    if not errors:
        report_lines.append(("— هیچ خطایی پیدا نشد." if ctx.lang != "en" else "— no error found."))
    report = "\n".join(report_lines)
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, f"{source.stem}_report.txt")
        target.write_text(report, encoding="utf-8")
        saved.append(str(target))
    return RunResult(
        message=ctx.M(
            f"{len(lines):,} خط لاگ خوانده شد · {error_lines:,} خط خطا · "
            f"{len(errors)} الگوی متفاوت",
            f"{len(lines):,} log line(s) · {error_lines:,} error line(s) · "
            f"{len(errors)} distinct signature(s)"),
        details=report, outputs=saved)


# ---------------------------------------------------------------------- git stats
@tool(
    id="git_stats", category="dev", name="آمار مخزن گیت",
    name_en="Repository statistics",
    desc="یک مخزن گیت را خلاصه می‌کند: تعداد کامیت، پرکارترین افراد، پرشیفت‌ترین فایل‌ها و بازه‌ی زمانی.",
    desc_en="Summarises a git repo: commits, busiest authors, most-changed files and the active date range.",
    icon="history", order=51,
    keywords=("git", "commit", "author", "history", "repo", "گیت", "کامیت", "تاریخچه", "مخزن"),
    fields=[
        Field("repo", K_DIR, "پوشه‌ی مخزن", "Repository folder", required=True),
        Field("limit", K_INT, "حداکثر کامیت برای بررسی", "Commits to scan", default=500,
              minimum=10, maximum=20000),
        Field("top", K_INT, "تعداد ردیف در هر جدول", "Rows per table", default=8,
              minimum=1, maximum=50),
    ],
)
def git_stats(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = str(v.get("repo") or "").strip()
    if not folder or not Path(folder).is_dir():
        raise ToolError(ctx.M(f"پوشه پیدا نشد: {folder or '—'}", f"Folder not found: {folder or '—'}"))
    limit = int(v.get("limit") or 500)
    top_n = int(v.get("top") or 8)
    base = ["git", "-C", str(folder), "-c", "core.quotepath=false"]

    def git(*args: str, timeout: int = 60) -> str:
        try:
            proc = subprocess.run(base + list(args), capture_output=True, timeout=timeout,
                                  check=False)
        except FileNotFoundError:
            raise ToolError(ctx.M("git نصب نیست یا در PATH نیست.", "git is not installed or not on PATH."))
        except subprocess.TimeoutExpired:
            raise ToolError(ctx.M("git کند پاسخ داد؛ مخزن بزرگ‌تری است؟",
                                  "git timed out; is this a very large repository?"))
        if proc.returncode != 0:
            message = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
            raise ToolError(ctx.M(f"git خطا داد: {message}", f"git failed: {message}"))
        return (proc.stdout or b"").decode("utf-8", "replace")

    if not Path(folder, ".git").exists():
        try:
            git("rev-parse", "--git-dir")
        except ToolError:
            raise ToolError(ctx.M(f"این پوشه مخزن گیت نیست: {folder}",
                                  f"This folder is not a git repository: {folder}"))
    header = git("log", f"-n{limit}", "--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%s", "--date=short")
    rows = [line.split("\x1f") for line in header.splitlines() if "\x1f" in line]
    if not rows:
        raise ToolError(ctx.M("این مخزن هیچ کامیتی ندارد.", "This repository has no commit."))
    authors = Counter(row[1] for row in rows)
    emails = {row[1]: row[2] for row in rows}
    dates = sorted(row[3] for row in rows if len(row) > 3 and row[3])
    numstat = git("log", f"-n{min(limit, 300)}", "--numstat", "--pretty=format:%x00")
    churn: Counter = Counter()
    added = deleted = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, remove, name = parts
        churn[name] += 1
        if add.isdigit():
            added += int(add)
        if remove.isdigit():
            deleted += int(remove)
    branches = git("branch", "--all", "--format=%(refname:short)").split()
    try:
        status = git("status", "--porcelain").strip()
        dirty = len([line for line in status.splitlines() if line.strip()])
    except ToolError:                                  # pragma: no cover - bare repo
        dirty = 0
    lines = [
        f"repository   {folder}",
        f"commits      {len(rows)} (scanned, limit {limit})",
        f"branches     {len(branches)}",
        f"uncommitted  {dirty} file(s)",
        f"date range   {dates[0] if dates else '—'} → {dates[-1] if dates else '—'}",
        f"churn        +{added:,} / -{deleted:,} lines",
        "",
        ("پرکارترین افراد:" if ctx.lang != "en" else "Busiest authors:"),
    ]
    for author, count in authors.most_common(top_n):
        share = count * 100 / max(1, len(rows))
        lines.append(f"  {author:28} {count:5} ({share:.1f}%)  <{emails.get(author, '')}>")
    lines.append("")
    lines.append(("پرشیفت‌ترین فایل‌ها:" if ctx.lang != "en" else "Most-changed files:"))
    for name, count in churn.most_common(top_n):
        lines.append(f"  {count:5}× {name}")
    return RunResult(
        message=ctx.M(
            f"{len(rows)} کامیت · {len(authors)} نفر · {len(churn)} فایل تغییر کرده",
            f"{len(rows)} commit(s) · {len(authors)} author(s) · {len(churn)} file(s) changed"),
        details="\n".join(lines))


# --------------------------------------------------------------------- csv join
@tool(
    id="csv_join", category="dev", name="اتصال دو CSV (Join)",
    name_en="Join two CSV files",
    desc="دو CSV را روی یک ستون مشترک مثل JOIN در SQL به هم می‌چسباند و گزارش می‌دهد چه چیزی جفت نشد.",
    desc_en="Joins two CSVs on a shared column like an SQL JOIN and reports what did not match.",
    icon="table", order=52,
    keywords=("csv", "join", "merge", "vlookup", "combine", "csv", "اتصال", "ادغام", "جدول"),
    fields=[
        Field("left", K_FILE, "CSV چپ", "Left CSV", required=True, filt="CSV (*.csv *.tsv *.txt)"),
        Field("right", K_FILE, "CSV راست", "Right CSV", required=True, filt="CSV (*.csv *.tsv *.txt)"),
        Field("key", K_TEXT, "ستون مشترک", "Join column", placeholder="id",
              help="خالی بگذارید تا اولین ستون مشترک انتخاب شود.",
              help_en="Leave empty to use the first shared column."),
        Field("mode", K_CHOICE, "نوع اتصال", "Join type", default="left",
              choices=[("left", "چپ (همه‌ی ردیف‌های چپ)", "Left (all left rows)"),
                       ("inner", "داخلی (فقط جفت‌شده‌ها)", "Inner (matches only)"),
                       ("outer", "کامل (همه‌ی ردیف‌ها)", "Outer (everything)")]),
        Field("delimiter", K_TEXT, "جداکننده", "Delimiter", default=",",
              help="برای TSV یک تب بگذارید.", help_en="Use a tab for TSV."),
    ],
)
def csv_join(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    left = first_file(v, "left", ctx)
    right = first_file(v, "right", ctx)
    delimiter = str(v.get("delimiter") or ",")
    if delimiter.lower() in ("tab", "\\t"):
        delimiter = "\t"
    mode = str(v.get("mode") or "left")

    def load(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if not reader.fieldnames:
                raise ToolError(ctx.M(f"این فایل سرستون ندارد: {path.name}",
                                      f"This file has no header row: {path.name}"))
            return list(reader.fieldnames), list(reader)

    left_fields, left_rows = load(left)
    right_fields, right_rows = load(right)
    shared = [name for name in left_fields if name in right_fields]
    key = str(v.get("key") or "").strip()
    if not key:
        if not shared:
            raise ToolError(ctx.M("این دو فایل هیچ ستون مشترکی ندارند؛ ستون اتصال را خودتان بنویسید.",
                                  "The files share no column; type the join column yourself."))
        key = shared[0]
    if key not in left_fields or key not in right_fields:
        raise ToolError(ctx.M(
            f"ستون «{key}» در یکی از فایل‌ها نیست.\nستون‌های مشترک: {', '.join(shared) or '—'}",
            f"Column '{key}' is missing in one of the files.\nShared columns: {', '.join(shared) or '—'}"))
    index: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in right_rows:
        index[str(row.get(key, "")).strip()].append(row)
    right_extra = [name for name in right_fields if name not in left_fields]
    header = left_fields + right_extra
    joined: List[Dict[str, str]] = []
    matched = 0
    unmatched = 0
    used: set = set()
    for row in left_rows:
        candidates = index.get(str(row.get(key, "")).strip(), [])
        if not candidates:
            unmatched += 1
            if mode in ("left", "outer"):
                joined.append({**{name: row.get(name, "") for name in left_fields},
                               **{name: "" for name in right_extra}})
            continue
        matched += 1
        for candidate in candidates:
            used.add(id(candidate))
            joined.append({**{name: row.get(name, "") for name in left_fields},
                           **{name: candidate.get(name, "") for name in right_extra}})
    orphans = 0
    if mode == "outer":
        for row in right_rows:
            if id(row) in used:
                continue
            orphans += 1
            joined.append({**{name: "" for name in left_fields},
                           **{name: row.get(name, "") for name in right_extra}})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, delimiter=delimiter)
    writer.writeheader()
    for row in joined:
        writer.writerow(row)
    target = save_target(ctx, "", f"{left.stem}_joined_{right.stem}.csv")
    target.write_text(buffer.getvalue(), encoding="utf-8-sig")
    message = ctx.M(
        f"{len(joined)} ردیف ساخته شد · {matched} جفت شد · {unmatched} بی‌جفت",
        f"{len(joined)} row(s) written · {matched} matched · {unmatched} unmatched")
    if mode == "outer":
        message += ctx.M(f" · {orphans} ردیف فقط در فایل راست", f" · {orphans} right-only")
    return RunResult(
        message=message,
        details=ctx.M(
            f"کلید: {key}\nستون‌های خروجی: {len(header)}\nفایل: {target}",
            f"Key: {key}\nOutput columns: {len(header)}\nFile: {target}"),
        outputs=[str(target)], open_target=str(target))


# ---------------------------------------------------------------------- jsonl
@tool(
    id="jsonl_tool", category="dev", name="ابزار JSONL",
    name_en="JSONL toolkit",
    desc="JSONL را به آرایه‌ی JSON تبدیل می‌کند، برعکسش، یا خط‌های خرابش را با شماره‌ی خط پیدا می‌کند.",
    desc_en="Converts JSONL to a JSON array (and back) or reports the exact broken lines.",
    icon="code", order=53,
    keywords=("jsonl", "ndjson", "json lines", "convert", "validate", "jsonl", "خط خراب", "تبدیل"),
    fields=[
        Field("src", K_FILE, "فایل JSONL یا JSON", "JSONL or JSON file", required=True,
              filt="JSON (*.jsonl *.ndjson *.json *.txt)"),
        Field("mode", K_CHOICE, "کار", "Action", default="to_array",
              choices=[("to_array", "JSONL → آرایه", "JSONL → array"),
                       ("to_lines", "آرایه → JSONL", "Array → JSONL"),
                       ("check", "فقط بررسی", "Check only")]),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def jsonl_tool(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    source = first_file(v, "src", ctx)
    text = read_text_file(source, ctx, encoding="utf-8-sig")
    if not text.strip():
        raise ToolError(ctx.M("این فایل خالی است.", "This file is empty."))
    mode = str(v.get("mode") or "to_array")
    broken: List[str] = []
    records: List[Any] = []
    if mode == "to_lines":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolError(ctx.M(f"این فایل JSON معتبر نیست: {exc}",
                                  f"This is not valid JSON: {exc}"))
        if not isinstance(data, list):
            raise ToolError(ctx.M("برای این کار فایل باید یک آرایه‌ی JSON باشد.",
                                  "The file must contain a JSON array for this action."))
        records = data
        rendered = "\n".join(json.dumps(item, ensure_ascii=False) for item in data) + "\n"
    else:
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                broken.append(f"line {number}: {exc.msg} (col {exc.colno})")
        if mode == "check":
            if broken:
                raise ToolError(ctx.M(
                    f"{len(broken)} خط خراب از {len(records) + len(broken)} خط:\n" +
                    "\n".join(broken[:20]),
                    f"{len(broken)} broken line(s) out of {len(records) + len(broken)}:\n" +
                    "\n".join(broken[:20])))
            keys = Counter(key for record in records if isinstance(record, dict) for key in record)
            return RunResult(
                message=ctx.M(f"همه‌ی {len(records)} خط سالم است.",
                              f"All {len(records)} line(s) are valid."),
                details=ctx.M("پرتکرارترین کلیدها: ", "Most common keys: ") +
                ", ".join(f"{key}({count})" for key, count in keys.most_common(8)))
        rendered = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    target = save_target(ctx, str(v.get("out") or "").strip(),
                         f"{source.stem}.{'jsonl' if mode == 'to_lines' else 'json'}")
    target.write_text(rendered, encoding="utf-8")
    message = ctx.M(f"{len(records)} رکورد تبدیل شد.", f"{len(records)} record(s) converted.")
    if broken:
        message += ctx.M(f" ({len(broken)} خط خراب نادیده گرفته شد)", f" ({len(broken)} broken line(s) skipped)")
    return RunResult(message=message,
                     details="\n".join(broken[:20]) or str(target),
                     outputs=[str(target)], open_target=str(target))


# ------------------------------------------------------------------ curl → code
@tool(
    id="curl_to_code", category="dev", name="تبدیل curl به کد",
    name_en="Convert curl to code",
    desc="یک دستور curl را به کد آماده‌ی پایتون (requests)، JavaScript (fetch) یا PowerShell تبدیل می‌کند.",
    desc_en="Turns a curl command into ready-to-run Python (requests), JavaScript (fetch) or PowerShell.",
    icon="terminal", order=54,
    keywords=("curl", "http", "requests", "fetch", "convert", "curl", "تبدیل", "کد", "درخواست"),
    fields=[
        Field("command", K_MULTILINE, "دستور curl", "curl command", required=True,
              placeholder="curl -X POST https://api.example.com -H 'Content-Type: application/json' -d '{\"a\":1}'"),
        Field("target", K_CHOICE, "خروجی", "Output", default="python",
              choices=[("python", "پایتون (requests)", "Python (requests)"),
                       ("javascript", "JavaScript (fetch)", "JavaScript (fetch)"),
                       ("powershell", "PowerShell", "PowerShell")]),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def curl_to_code(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("command") or "").strip()
    if not raw:
        raise ToolError(ctx.M("دستور curl را بچسبانید.", "Paste the curl command."))
    command = raw.replace("\\\n", " ").replace("\n", " ")
    if command.strip().lower().startswith("curl "):
        command = command.strip()[5:]
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolError(ctx.M(f"دستور خوانده نشد: {exc}", f"Cannot parse the command: {exc}"))
    url = ""
    method = ""
    headers: List[Tuple[str, str]] = []
    data: List[str] = []
    form: List[Tuple[str, str]] = []
    auth: Optional[Tuple[str, str]] = None
    cookies: List[str] = []
    insecure = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-X", "--request") and index + 1 < len(tokens):
            method = tokens[index + 1].upper()
            index += 2
            continue
        if token in ("-H", "--header") and index + 1 < len(tokens):
            name, _, value = tokens[index + 1].partition(":")
            headers.append((name.strip(), value.strip()))
            index += 2
            continue
        if token in ("-d", "--data", "--data-raw", "--data-binary") and index + 1 < len(tokens):
            data.append(tokens[index + 1])
            index += 2
            continue
        if token in ("-F", "--form") and index + 1 < len(tokens):
            name, _, value = tokens[index + 1].partition("=")
            form.append((name.strip(), value.strip()))
            index += 2
            continue
        if token in ("-u", "--user") and index + 1 < len(tokens):
            user, _, password = tokens[index + 1].partition(":")
            auth = (user, password)
            index += 2
            continue
        if token in ("-b", "--cookie") and index + 1 < len(tokens):
            cookies.append(tokens[index + 1])
            index += 2
            continue
        if token in ("-k", "--insecure"):
            insecure = True
            index += 1
            continue
        if token in ("-I", "--head"):
            method = "HEAD"
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        if not url:
            url = token
        index += 1
    if not url:
        raise ToolError(ctx.M("در این دستور آدرس (URL) پیدا نشد.", "No URL was found in the command."))
    if not method:
        method = "POST" if (data or form) else "GET"
    body = "\n".join(data)
    target = str(v.get("target") or "python")
    if target == "python":
        lines = ["import requests", ""]
        lines.append(f'url = "{url}"')
        if headers:
            lines.append("headers = {")
            for name, value in headers:
                lines.append(f'    "{name}": "{value}",')
            lines.append("}")
        else:
            lines.append("headers = {}")
        if form:
            lines.append("files = {")
            for name, value in form:
                lines.append(f'    "{name}": "{value}",')
            lines.append("}")
            lines.append("")
            lines.append(f'response = requests.{method.lower()}(url, headers=headers, files=files,'
                         f'{" auth=" + str(auth) + "," if auth else ""}'
                         f'{" verify=False," if insecure else ""} timeout=30)')
        elif body:
            payload = body
            try:
                json.loads(body)
                lines.append("payload = " + body)
                lines.append("")
                lines.append(f'response = requests.{method.lower()}(url, headers=headers,'
                             f' json=payload,{"" if not auth else " auth=" + str(auth) + ","}'
                             f'{" cookies=" + str(cookies) + "," if cookies else ""}'
                             f'{" verify=False," if insecure else ""} timeout=30)')
            except (json.JSONDecodeError, TypeError):
                lines.append(f'payload = """{payload}"""')
                lines.append("")
                lines.append(f'response = requests.{method.lower()}(url, headers=headers,'
                             f' data=payload,{" auth=" + str(auth) + "," if auth else ""}'
                             f'{" verify=False," if insecure else ""} timeout=30)')
        else:
            lines.append("")
            lines.append(f'response = requests.{method.lower()}(url, headers=headers,'
                         f'{" auth=" + str(auth) + "," if auth else ""}'
                         f'{" cookies=" + str(cookies) + "," if cookies else ""}'
                         f'{" verify=False," if insecure else ""} timeout=30)')
        lines.append("response.raise_for_status()")
        lines.append("print(response.status_code, response.headers.get('content-type'))")
        lines.append("print(response.text[:2000])")
        code = "\n".join(lines)
        suffix = ".py"
    elif target == "javascript":
        options = [f'  method: "{method}",']
        if headers:
            options.append("  headers: {")
            for name, value in headers:
                options.append(f'    "{name}": "{value}",')
            options.append("  },")
        if body:
            options.append(f"  body: {json.dumps(body)},")
        if auth:
            options.append(f'  // basic auth: {auth[0]}:{auth[1]}')
        code = ("const response = await fetch(" + json.dumps(url) + ", {\n"
                + "\n".join(options) + "\n});\n"
                "if (!response.ok) throw new Error(`HTTP ${response.status}`);\n"
                "console.log(await response.text());")
        suffix = ".js"
    else:
        lines = ["$headers = @{"]
        for name, value in headers:
            lines.append(f'    "{name}" = "{value}"')
        lines.append("}")
        lines.append(f'$response = Invoke-WebRequest -Uri "{url}" -Method {method} '
                     f'-Headers $headers' + (f' -Body \'{body}\'' if body else "")
                     + (" -SkipCertificateCheck" if insecure else ""))
        lines.append("$response.StatusCode")
        lines.append("$response.Content")
        code = "\n".join(lines)
        suffix = ".ps1"
    requested = str(v.get("out") or "").strip()
    saved: List[str] = []
    if requested:
        target_path = save_target(ctx, requested, f"request{suffix}")
        target_path.write_text(code, encoding="utf-8")
        saved.append(str(target_path))
    return RunResult(
        message=ctx.M(f"دستور curl به کد {target} تبدیل شد ({method} {url}).",
                      f"curl converted to {target} code ({method} {url})."),
        details=code, outputs=saved)
