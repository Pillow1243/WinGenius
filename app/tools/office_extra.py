"""Office & text extras: Word→text, Excel→CSV, table splitting, text statistics."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILE, K_FILES, K_INT, K_MULTILINE, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import first_file, out_dir, require, save_target

DOCX_FILTER = "Word (*.docx);;همه فایل‌ها (*)"
SHEET_FILTER = "اکسل (*.xlsx *.xlsm);;همه فایل‌ها (*)"
CSV_FILTER = "CSV (*.csv);;همه فایل‌ها (*)"


@tool(
    id="word_to_text", category="convert", name="Word به متن", name_en="Word → text",
    desc="متن فایل Word را با حفظ ترتیب پاراگراف‌ها و جدول‌ها در TXT ذخیره می‌کند.",
    desc_en="Extract the text of a .docx file (paragraphs and tables) into TXT.",
    icon="file", order=210,
    keywords=("docx to txt", "word text", "متن ورد", "ورد به متن"),
    fields=[
        Field("src", K_FILE, "فایل Word", "Word file", required=True, filt=DOCX_FILTER),
        Field("tables", K_BOOL, "تبدیل جدول‌ها به متن", "Include tables", default=True),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="متن (*.txt)"),
    ],
)
def word_to_text(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    document_module = require("docx", "python-docx", ctx)
    src = first_file(v, "src", ctx)
    try:
        document = document_module.Document(str(src))
    except Exception as exc:
        raise ToolError(ctx.M(f"این فایل Word باز نشد: {exc}", f"Could not open the Word file: {exc}"))
    lines: List[str] = []
    total = max(1, len(document.paragraphs))
    for index, paragraph in enumerate(document.paragraphs, start=1):
        if index % 40 == 0:
            ctx.progress(index, total, ctx.M("خواندن پاراگراف‌ها", "Reading paragraphs"))
        text = paragraph.text.strip()
        if text:
            lines.append(text)
    if v.get("tables", True):
        for table in document.tables:
            lines.append("")
            for row in table.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    if not lines:
        raise ToolError(ctx.M("این سند متنی ندارد.", "This document has no text."))
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}.txt")
    dest.write_text("\n".join(lines), encoding="utf-8")
    words = len(" ".join(lines).split())
    return RunResult(
        message=ctx.M(f"{words:,} کلمه استخراج شد ({human_size(dest.stat().st_size)}).\n{dest}",
                      f"Extracted {words:,} word(s) ({human_size(dest.stat().st_size)}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
        details="\n".join(lines)[:6000])


@tool(
    id="sheet_to_csv", category="convert", name="اکسل به CSV", name_en="Excel → CSV",
    desc="هر برگه‌ی اکسل را به یک فایل CSV جداگانه تبدیل می‌کند (یا فقط یک برگه).",
    desc_en="Convert each Excel sheet to its own CSV file (or just one sheet).",
    icon="table", order=211,
    keywords=("xlsx to csv", "excel csv", "اکسل به سی اس وی", "برگه"),
    fields=[
        Field("src", K_FILE, "فایل اکسل", "Excel file", required=True, filt=SHEET_FILTER),
        Field("sheet", K_TEXT, "نام برگه (خالی = همه)", "Sheet name (blank = all)"),
        Field("delimiter", K_CHOICE, "جداکننده", "Delimiter", default=",",
              choices=[(",", "کاما (,)", "Comma (,)"), (";", "نقطه‌ویرگول (;)", "Semicolon (;)"),
                       ("\\t", "تب", "Tab")]),
        Field("out_dir", K_TEXT, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def sheet_to_csv(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    openpyxl = require("openpyxl", "openpyxl", ctx)
    src = first_file(v, "src", ctx)
    try:
        workbook = openpyxl.load_workbook(str(src), data_only=True, read_only=True)
    except Exception as exc:
        raise ToolError(ctx.M(f"این فایل اکسل باز نشد: {exc}", f"Could not open the workbook: {exc}"))
    wanted = str(v.get("sheet") or "").strip()
    sheets = [wanted] if wanted else list(workbook.sheetnames)
    missing = [name for name in sheets if name not in workbook.sheetnames]
    if missing:
        raise ToolError(ctx.M(f"برگه‌ی «{missing[0]}» وجود ندارد. برگه‌ها: {', '.join(workbook.sheetnames)}",
                              f"Sheet '{missing[0]}' not found. Sheets: {', '.join(workbook.sheetnames)}"))
    delimiter = str(v.get("delimiter") or ",").replace("\\t", "\t")
    folder = str(v.get("out_dir") or "").strip()
    target_dir = Path(folder) if folder else out_dir(ctx)
    target_dir.mkdir(parents=True, exist_ok=True)
    outputs: List[str] = []
    for index, name in enumerate(sheets, start=1):
        ctx.progress(index, len(sheets), ctx.M(f"برگه {name}", f"Sheet {name}"))
        sheet = workbook[name]
        safe_name = re.sub(r"[^\w\-]+", "_", str(name))
        suffix = "" if len(sheets) == 1 else "_" + safe_name
        dest = unique_path(target_dir / f"{src.stem}{suffix}.csv")
        rows = 0
        with open(dest, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=delimiter)
            for row in sheet.iter_rows(values_only=True):
                if row is None or all(cell is None for cell in row):
                    continue
                writer.writerow(["" if cell is None else cell for cell in row])
                rows += 1
        if rows == 0:
            dest.unlink(missing_ok=True)
            continue
        outputs.append(str(dest))
        ctx.log(f"{name}: {rows} rows")
    workbook.close()
    if not outputs:
        raise ToolError(ctx.M("هیچ برگه‌ای داده نداشت.", "No sheet contained data."))
    return RunResult(
        message=ctx.M(f"{len(outputs)} فایل CSV ساخته شد.", f"{len(outputs)} CSV file(s) created.")
        + f"\n{target_dir}",
        outputs=outputs, open_target=str(target_dir))


@tool(
    id="table_split", category="convert", name="تقسیم جدول بزرگ", name_en="Split a big table",
    desc="یک CSV/اکسل بزرگ را به چند فایل کوچک با تعداد سطر مشخص تقسیم می‌کند (با سرستون).",
    desc_en="Split a big CSV/Excel table into files of N rows, repeating the header row.",
    icon="scissors", order=212,
    keywords=("split csv", "chunk rows", "تقسیم اکسل", "تقسیم سی اس وی"),
    fields=[
        Field("src", K_FILE, "فایل جدول", "Table file", required=True,
              filt="جدول (*.csv *.xlsx *.xlsm);;همه فایل‌ها (*)"),
        Field("rows", K_INT, "سطر در هر فایل", "Rows per file", default=1000, minimum=10, maximum=200000),
        Field("keep_header", K_BOOL, "تکرار سرستون", "Repeat header row", default=True),
        Field("out_dir", K_TEXT, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def table_split(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    rows_per_file = max(10, int(v.get("rows") or 1000))
    rows: List[List[Any]] = []
    if src.suffix.lower() == ".csv":
        with open(src, "r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except Exception:
                dialect = csv.excel
            rows = [list(row) for row in csv.reader(handle, dialect)]
    else:
        openpyxl = require("openpyxl", "openpyxl", ctx)
        workbook = openpyxl.load_workbook(str(src), data_only=True, read_only=True)
        sheet = workbook[workbook.sheetnames[0]]
        rows = [["" if cell is None else cell for cell in row] for row in sheet.iter_rows(values_only=True)]
        workbook.close()
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if len(rows) <= 1:
        raise ToolError(ctx.M("جدول خالی است.", "The table is empty."))
    header = rows[0] if v.get("keep_header", True) else []
    body = rows[1:]
    folder = str(v.get("out_dir") or "").strip()
    target_dir = Path(folder) if folder else out_dir(ctx) / f"{src.stem}_parts"
    target_dir.mkdir(parents=True, exist_ok=True)
    outputs: List[str] = []
    chunks = [body[index:index + rows_per_file] for index in range(0, len(body), rows_per_file)]
    for index, chunk in enumerate(chunks, start=1):
        ctx.progress(index, len(chunks), ctx.M(f"تکه {index}", f"Part {index}"))
        dest = unique_path(target_dir / f"{src.stem}_part{index:02d}.csv")
        with open(dest, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            if header:
                writer.writerow(header)
            writer.writerows(chunk)
        outputs.append(str(dest))
    return RunResult(
        message=ctx.M(f"{len(outputs)} فایل ساخته شد ({len(body)} سطر داده).",
                      f"{len(outputs)} file(s) created ({len(body)} data rows).") + f"\n{target_dir}",
        outputs=outputs, open_target=str(target_dir))


@tool(
    id="text_stats", category="convert", name="آمار متن", name_en="Text statistics",
    desc="تعداد کلمه، نویسه، خط، جمله و زمان تقریبی خواندن/گفتن متن را می‌سنجد.",
    desc_en="Count words, characters, lines and sentences, with reading/speaking time.",
    icon="chart", order=213,
    keywords=("word count", "character count", "شمارش کلمات", "تعداد کلمه", "آمار متن"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل", "File")]),
        Field("text", K_MULTILINE, "متن", "Text", visible_when=("source", "text")),
        Field("file", K_FILES, "فایل‌ها", "Files", filt="متن (*.txt *.md *.csv *.srt);;همه فایل‌ها (*)",
              multiple=True, visible_when=("source", "file")),
    ],
)
def text_stats(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if str(v.get("source") or "text") == "file":
        items = [Path(p) for p in (v.get("file") or [])]
        if not items:
            raise ToolError(ctx.M("فایلی انتخاب نشده.", "No file selected."))
        parts = []
        for index, path in enumerate(items, start=1):
            ctx.progress(index, len(items), path.name)
            try:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except Exception as exc:
                raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read the file: {exc}"))
        text = "\n".join(parts)
        label_text = ", ".join(path.name for path in items[:3])
    else:
        text = str(v.get("text") or "")
        label_text = ctx.M("متن ورودی", "Input text")
    if not text.strip():
        raise ToolError(ctx.M("متنی وارد نشده.", "No text provided."))
    words = [item for item in re.split(r"\s+", text.strip()) if item]
    characters = len(text)
    characters_no_space = len(re.sub(r"\s", "", text))
    lines = len([line for line in text.splitlines() if line.strip()])
    latin_sentences = len([s for s in re.split(r"[.!?…]+", text) if s.strip()])
    persian_sentences = len([s for s in re.split(r"[.!?…؟]+", text) if s.strip()])
    sentences = max(latin_sentences, persian_sentences)
    persian_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    reading = max(1, round(len(words) / 200))
    speaking = max(1, round(len(words) / 130))
    unique_words = len({word.strip(".,،؛:!؟") for word in words})
    lines_out = [
        f"{ctx.M('نام', 'Source')}: {label_text}",
        f"{ctx.M('کلمه', 'Words')}: {len(words):,}",
        f"{ctx.M('نویسه', 'Characters')}: {characters:,} ({characters_no_space:,} {ctx.M('بدون فاصله', 'without spaces')})",
        f"{ctx.M('خط', 'Lines')}: {lines:,}",
        f"{ctx.M('جمله', 'Sentences')}: {sentences:,}",
        f"{ctx.M('کلمه‌های یکتا', 'Unique words')}: {unique_words:,}",
        f"{ctx.M('حروف فارسی', 'Persian letters')}: {persian_chars:,}",
        "",
        ctx.M(f"زمان تقریبی خواندن: {reading} دقیقه", f"Reading time: ~{reading} min"),
        ctx.M(f"زمان تقریبی گفتن: {speaking} دقیقه", f"Speaking time: ~{speaking} min"),
    ]
    return RunResult(
        message=ctx.M(f"{len(words):,} کلمه · {characters:,} نویسه · {lines:,} خط · خواندن ~{reading} دقیقه",
                      f"{len(words):,} words · {characters:,} chars · {lines:,} lines · ~{reading} min read"),
        details="\n".join(lines_out))
