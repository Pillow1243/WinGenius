"""Local text intelligence: summaries, keywords, readability, extraction, numbers."""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_FILE, K_INT, K_MULTILINE, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir
from ._helpers import read_text_file

STOP_FA = set("""
و در به از که این را با برای آن است بود شد می هم یا تا بر ای اند شده کند کنند کرد باید
خود هر بین دو یک های ها ترین تر اما اگر چون پس همه هیچ نه بله دیگر بیشتر بسیار چه کجا
چون همین ان را_ نیز شود شوند داشت دارد داریم دارم داشتند خواهد توان توانست مثل وقتی
""".split())
STOP_EN = set("""
a an the and or but if then than that this these those of in on at to for from with by as is
are was were be been being it its it's you your we our they their he she his her i me my not no
do does did done have has had will would can could should may might must about into over under
""".split())
STOPWORDS = STOP_FA | STOP_EN

WORD_RE = re.compile(r"[0-9A-Za-zآ-یءؤئÀ-ÿ]+")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…\n])\s+")


def _text_from(values: Dict[str, Any], ctx: RunContext, key: str = "text", file_key: str = "file") -> str:
    text = str(values.get(key) or "")
    if text.strip():
        return text
    path = str(values.get(file_key) or "").strip()
    if path and Path(path).exists():
        if Path(path).is_dir():
            raise ToolError(ctx.M(f"این یک پوشه است، نه فایل: {path}",
                                  f"That is a folder, not a file: {path}"))
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read the file: {exc}"))
        for encoding in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")
    raise ToolError(ctx.M("متن یا فایل را وارد کنید.", "Enter text or pick a file."))


def _sentences(text: str) -> List[str]:
    return [part.strip() for part in SENTENCE_SPLIT.split(text) if len(part.strip()) > 2]


def _tokens(text: str) -> List[str]:
    return [word.lower() for word in WORD_RE.findall(text)]


def _save(text: str, requested: str) -> List[str]:
    requested = (requested or "").strip()
    if not requested:
        return []
    target = ensure_dir(Path(requested).parent) / Path(requested).name
    target.write_text(text, encoding="utf-8")
    return [str(target)]


# ------------------------------------------------------------------ summarize
@tool(
    id="text_summarize", category="convert", name="خلاصه‌سازی متن", name_en="Summarise text",
    desc="متن بلند (یا فایل PDF/Word/متن) را می‌خواند و مهم‌ترین جمله‌ها را به‌صورت خلاصه برمی‌گرداند؛ کاملاً محلی و بدون هوش مصنوعی ابری.",
    desc_en="Reads a long text (or a PDF/Word/text file) and returns its most important sentences — fully offline.",
    icon="text", order=30,
    keywords=("summary", "summarize", "خلاصه", "چکیده", "جمع‌بندی", "tl;dr"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text/PDF/Word (*.txt *.md *.pdf *.docx)"),
        Field("size", K_INT, "تعداد جمله‌ها", "Sentences", default=5, minimum=1, maximum=40),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="Text (*.txt)"),
    ],
)
def text_summarize(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = _text_from(v, ctx)
    if str(v.get("file") or "").lower().endswith(".pdf"):
        try:
            from ..core import indexer

            raw = indexer.extract_text(Path(str(v["file"]))) or raw
        except Exception:
            pass
    sentences = _sentences(raw)
    if len(sentences) <= 3:
        raise ToolError(ctx.M("متن برای خلاصه‌سازی کوتاه است.", "The text is too short to summarise."))
    counts = Counter(token for token in _tokens(raw) if token not in STOPWORDS and len(token) > 2)
    if not counts:
        raise ToolError(ctx.M("کلمه‌ی معناداری پیدا نشد.", "No meaningful words found."))
    top = max(counts.values())
    weight = {word: count / top for word, count in counts.items()}
    scored: List[Tuple[float, int, str]] = []
    for index, sentence in enumerate(sentences):
        words = [token for token in _tokens(sentence) if token not in STOPWORDS]
        if not words:
            continue
        score = sum(weight.get(word, 0) for word in words) / math.sqrt(len(words))
        if index == 0:
            score *= 1.25            # first sentence is usually a good summary line
        if index == len(sentences) - 1:
            score *= 1.05
        scored.append((score, index, sentence))
    wanted = max(1, int(v.get("size") or 5))
    picked = sorted(sorted(scored, reverse=True)[:wanted], key=lambda item: item[1])
    summary = "\n".join(f"• {sentence}" for _score, _index, sentence in picked)
    details = (f"{ctx.M('جمله‌های متن', 'Sentences')}: {len(sentences)} · "
               f"{ctx.M('کلمه‌ها', 'Words')}: {len(_tokens(raw))}\n\n{summary}")
    outputs = _save(summary, str(v.get("out") or ""))
    return RunResult(
        message=ctx.M(f"خلاصه در {len(picked)} جمله آماده شد.", f"Summary ready ({len(picked)} sentences)."),
        details=details, outputs=outputs)


# ------------------------------------------------------------------- keywords
@tool(
    id="text_keywords", category="convert", name="کلیدواژه‌های متن", name_en="Keywords",
    desc="پرتکرارترین و مهم‌ترین کلمه‌های متن را با شمارش و درصد نشان می‌دهد.",
    desc_en="Extracts the most important keywords with counts and percentages.",
    icon="tag", order=31, keywords=("keywords", "tags", "کلیدواژه", "کلمات مهم", "برچسب"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text (*.txt *.md *.csv)"),
        Field("count", K_INT, "تعداد", "How many", default=15, minimum=3, maximum=100),
    ],
)
def text_keywords(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = _text_from(v, ctx)
    words = [token for token in _tokens(raw) if token not in STOPWORDS and len(token) > 2]
    if not words:
        raise ToolError(ctx.M("کلمه‌ی قابل توجهی پیدا نشد.", "No usable words found."))
    counts = Counter(words)
    total = len(words)
    lines = [f"{index:>2}. {word:<24} {count:>4}  ({count * 100 / total:.1f}%)"
             for index, (word, count) in enumerate(counts.most_common(int(v.get("count") or 15)), 1)]
    details = "\n".join(lines)
    return RunResult(message=ctx.M(f"{len(counts)} کلیدواژه‌ی یکتا",
                                   f"{len(counts)} unique keywords"), details=details)


# --------------------------------------------------------------- readability
@tool(
    id="text_readability", category="convert", name="خوانایی و زمان مطالعه", name_en="Readability",
    desc="طول متن، تعداد جمله، میانگین کلمه در جمله، زمان تقریبی مطالعه و سطح دشواری را می‌سنجد.",
    desc_en="Measures length, sentences, words per sentence, reading time and difficulty.",
    icon="chart", order=32, keywords=("readability", "reading time", "خوانایی", "زمان مطالعه"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text (*.txt *.md)"),
    ],
)
def text_readability(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = _text_from(v, ctx)
    words = _tokens(raw)
    sentences = _sentences(raw)
    if not words:
        raise ToolError(ctx.M("متن خالی است.", "The text is empty."))
    per_sentence = len(words) / max(1, len(sentences))
    syllables = sum(max(1, len(re.findall(r"[aeiouyآایو]+", word.lower()))) for word in words)
    flesch = 206.835 - 1.015 * per_sentence - 84.6 * (syllables / len(words))
    level = ("ساده" if flesch > 70 else "متوسط" if flesch > 45 else "دشوار")
    minutes = max(1, round(len(words) / 200))
    details = (f"کلمه‌ها: {len(words)}\nجمله‌ها: {len(sentences)}\n"
               f"میانگین کلمه در جمله: {per_sentence:.1f}\n"
               f"زمان مطالعه: ~{minutes} دقیقه\n"
               f"امتیاز Flesch: {flesch:.1f} ({level})")
    return RunResult(message=f"{len(words)} کلمه · ~{minutes} دقیقه مطالعه", details=details)


# ---------------------------------------------------------------- language id
@tool(
    id="lang_detect", category="convert", name="تشخیص زبان متن", name_en="Detect language",
    desc="حدس می‌زند متن فارسی، انگلیسی یا ترکیبی است و نسبت حروف هر زبان را نشان می‌دهد.",
    desc_en="Guesses whether a text is Persian, English or mixed and shows the letter ratio.",
    icon="globe", order=33, keywords=("language", "detect", "زبان", "تشخیص", "فارسی"),
    fields=[Field("text", K_MULTILINE, "متن", "Text", required=True)],
)
def lang_detect(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    persian = sum(1 for char in text if "\u0600" <= char <= "\u06ff")
    latin = sum(1 for char in text if char.isascii() and char.isalpha())
    digits = sum(1 for char in text if char.isdigit())
    total = max(1, persian + latin)
    ratio = persian / total
    if ratio > 0.75:
        label = "فارسی"
    elif ratio > 0.25:
        label = "ترکیبی (فارسی + انگلیسی)"
    elif latin:
        label = "انگلیسی"
    else:
        label = "نامشخص"
    details = (f"حروف فارسی: {persian}\nحروف لاتین: {latin}\nارقام: {digits}\n"
               f"نسبت فارسی: {ratio * 100:.1f}%")
    return RunResult(message=f"زبان: {label}", details=details)


# ------------------------------------------------------------ number to words
_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
_SCALES = ["", " هزار", " میلیون", " میلیارد", " تریلیون"]


def number_to_persian(number: int) -> str:
    if number == 0:
        return "صفر"
    if number < 0:
        return "منفی " + number_to_persian(-number)
    groups: List[str] = []
    scale = 0
    while number > 0:
        chunk = number % 1000
        if chunk:
            words: List[str] = []
            hundreds, rest = divmod(chunk, 100)
            if hundreds:
                words.append(_HUNDREDS[hundreds])
            if 10 <= rest < 20:
                words.append(_TEENS[rest - 10])
            else:
                tens, ones = divmod(rest, 10)
                if tens:
                    words.append(_TENS[tens])
                if ones:
                    words.append(_ONES[ones])
            phrase = " و ".join(words)
            groups.insert(0, phrase + _SCALES[scale])
        number //= 1000
        scale += 1
    return " و ".join(groups)


@tool(
    id="number_to_words", category="convert", name="عدد به حروف فارسی", name_en="Number to Persian words",
    desc="عدد را به فارسی روان می‌نویسد (مناسب چک، فاکتور و قرارداد).",
    desc_en="Writes a number in Persian words — handy for cheques, invoices and contracts.",
    icon="text", order=34, keywords=("number", "words", "عدد", "حروف", "چک", "فاکتور"),
    fields=[
        Field("number", K_TEXT, "عدد", "Number", required=True, placeholder="1,250,000"),
        Field("currency", K_CHOICE, "واحد", "Unit", default="",
              choices=[("", "بدون واحد", "None"), ("ریال", "ریال", "Rial"),
                       ("تومان", "تومان", "Toman"), ("دلار", "دلار", "Dollar")]),
    ],
)
def number_to_words(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("number") or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    cleaned = re.sub(r"[^\d-]", "", raw)
    if not cleaned or cleaned in ("-",):
        raise ToolError(ctx.M("یک عدد معتبر وارد کنید.", "Enter a valid number."))
    value = int(cleaned)
    words = number_to_persian(value)
    unit = str(v.get("currency") or "").strip()
    message = f"{words}{(' ' + unit) if unit else ''}"
    details = f"{value:,}\n{message}"
    return RunResult(message=message, details=details)


# --------------------------------------------------------------------- outline
@tool(
    id="text_outline", category="convert", name="ساخت فهرست از متن", name_en="Build an outline",
    desc="سرتیترها و ساختار متن مارک‌داون/فهرست‌دار را استخراج می‌کند و فهرستی مرتب برمی‌گرداند.",
    desc_en="Extracts headings from Markdown or numbered text and returns a clean outline.",
    icon="list", order=35, keywords=("outline", "toc", "headings", "فهرست", "سرتیتر", "سرفصل"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text (*.md *.txt)"),
    ],
)
def text_outline(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = _text_from(v, ctx)
    lines: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            lines.append("  " * max(0, level - 1) + "• " + stripped.lstrip("#").strip())
            continue
        match = re.match(r"^(\d+([.)\-]\d+)*)[.)\-]?\s+(.{3,90})$", stripped)
        if match and len(stripped) < 95:
            depth = match.group(1).count(".") + match.group(1).count("-")
            lines.append("  " * min(depth, 4) + "• " + match.group(3).strip())
    if not lines:
        raise ToolError(ctx.M("سرتیتری پیدا نشد؛ متن باید # یا شماره‌گذاری داشته باشد.",
                              "No headings found — the text needs # marks or numbering."))
    details = "\n".join(lines)
    return RunResult(message=f"{len(lines)} سرتیتر", details=details)


# ------------------------------------------------------------------ extraction
@tool(
    id="text_extract", category="convert", name="استخراج ایمیل/لینک/شماره", name_en="Extract emails, links, numbers",
    desc="از متن یا فایل، ایمیل‌ها، لینک‌ها، شماره تلفن‌ها، آی‌پی‌ها و تاریخ‌ها را جدا می‌کند.",
    desc_en="Pulls emails, URLs, phone numbers, IPs and dates out of a text or file.",
    icon="filter", order=36, keywords=("extract", "emails", "links", "استخراج", "ایمیل", "لینک"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file", filt="Text (*.txt *.csv *.md)"),
        Field("kind", K_CHOICE, "چه چیزی", "What", default="all",
              choices=[("all", "همه", "Everything"), ("email", "ایمیل", "Emails"),
                       ("url", "لینک", "URLs"), ("phone", "شماره تلفن", "Phones"),
                       ("ip", "آی‌پی", "IPs"), ("date", "تاریخ", "Dates")]),
    ],
)
def text_extract(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = _text_from(v, ctx)
    patterns = {
        "email": r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
        "url": r"https?://[^\s<>\"')]+",
        "phone": r"(?:\+?\d{1,3}[\s\-]?)?(?:0\d{2,3}[\s\-]?)?\d{7,8}\b|09\d{9}",
        "ip": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "date": r"\b1[34]\d{2}[/\-]\d{1,2}[/\-]\d{1,2}\b|\b20\d{2}[/\-]\d{1,2}[/\-]\d{1,2}\b",
    }
    kind = str(v.get("kind") or "all")
    wanted = list(patterns) if kind == "all" else [kind]
    blocks: List[str] = []
    total = 0
    for key in wanted:
        found = sorted(set(re.findall(patterns[key], raw)))
        found = [item if isinstance(item, str) else item[0] for item in found]
        total += len(found)
        if found:
            blocks.append(f"— {key} ({len(found)})\n" + "\n".join(found[:200]))
    if not total:
        raise ToolError(ctx.M("چیزی از این نوع پیدا نشد.", "Nothing of that kind was found."))
    return RunResult(message=ctx.M(f"{total} مورد استخراج شد.", f"{total} item(s) extracted."),
                     details="\n\n".join(blocks))


# ------------------------------------------------------------------- csv profiler
@tool(
    id="csv_profile", category="convert", name="پروفایل جدول CSV", name_en="Profile a CSV",
    desc="برای هر ستون نوع داده، تعداد خالی‌ها، مقادیر یکتا و نمونه‌ها را گزارش می‌کند.",
    desc_en="Profiles every column of a CSV: data type, empty cells, unique values, samples.",
    icon="table", order=37, keywords=("csv", "profile", "columns", "پروفایل", "جدول", "ستون"),
    fields=[
        Field("file", K_FILE, "فایل CSV", "CSV file", required=True, filt="CSV (*.csv *.tsv)"),
        Field("rows", K_INT, "حداکثر ردیف بررسی", "Max rows", default=20000, minimum=100, maximum=500000),
    ],
)
def csv_profile(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import csv

    path = Path(str(v.get("file") or ""))
    if not path.exists():
        raise ToolError(ctx.M("فایل پیدا نشد.", "File not found."))
    if path.is_dir():
        raise ToolError(ctx.M("این یک پوشه است، نه فایل CSV.", "That is a folder, not a CSV file."))
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                raise ToolError(ctx.M("فایل خالی است.", "The file is empty."))
            counters = {name: Counter() for name in header}
            empties = {name: 0 for name in header}
            rows = 0
            limit = int(v.get("rows") or 20000)
            for row in reader:
                rows += 1
                if rows > limit:
                    break
                for index, name in enumerate(header):
                    cell = (row[index] if index < len(row) else "").strip()
                    if not cell:
                        empties[name] += 1
                        continue
                    counters[name][cell] += 1
    except PermissionError:
        raise ToolError(ctx.M(f"اجازه‌ی خواندن این فایل را ندارید: {path}",
                              f"No permission to read: {path}"))
    except OSError as exc:
        raise ToolError(ctx.M(f"فایل خوانده نشد: {exc}", f"Could not read the file: {exc}"))
    lines = [f"{len(header)} ستون · {rows} ردیف", "-" * 40]
    for name in header:
        values = counters[name]
        numbers = sum(count for value, count in values.items()
                      if re.fullmatch(r"-?\d+(\.\d+)?", value))
        kind = "عدد" if numbers > sum(values.values()) * 0.8 else "متن"
        top = ", ".join(f"{value} ({count})" for value, count in values.most_common(3)) or "—"
        lines.append(f"• {name}: {kind} · یکتا {len(values)} · خالی {empties[name]}\n    نمونه: {top}")
    return RunResult(message=f"{len(header)} ستون · {rows} ردیف", details="\n".join(lines))


# ---------------------------------------------------------------------- cleanup
@tool(
    id="text_cleanup", category="convert", name="پاکسازی متن", name_en="Clean up text",
    desc="فاصله‌های اضافی، خط‌های تکراری، نیم‌فاصله و نقل‌قول‌های نادرست را درست می‌کند.",
    desc_en="Fixes extra spaces, duplicate lines, half-spaces and wrong quotation marks.",
    icon="broom", order=38, keywords=("clean", "tidy", "پاکسازی", "مرتب", "فاصله"),
    fields=[
        Field("text", K_MULTILINE, "متن", "Text", required=True),
        Field("dedupe", K_INT, "حذف خط‌های تکراری (۰ = نه)", "Drop duplicate lines (0 = no)",
              default=1, minimum=0, maximum=1),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="Text (*.txt)"),
    ],
)
def text_cleanup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    before = len(text)
    text = text.replace("\u064a", "ی").replace("\u0643", "ک")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("“", "«").replace("”", "»").replace('"', "«", 1)
    if int(v.get("dedupe") or 0):
        seen: set = set()
        unique: List[str] = []
        for line in text.splitlines():
            key = line.strip()
            if key and key in seen:
                continue
            seen.add(key)
            unique.append(line)
        text = "\n".join(unique)
    text = text.strip()
    outputs = _save(text, str(v.get("out") or ""))
    return RunResult(message=ctx.M(f"پاک‌سازی شد ({before} → {len(text)} کاراکتر).",
                                  f"Cleaned ({before} → {len(text)} chars)."),
                     details=text[:4000], outputs=outputs)
