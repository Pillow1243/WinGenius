"""Advanced PDF work: attachments, forms, booklets, posters, comparison, markdown."""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_INT, K_MULTILINE, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, parse_ranges
from ._helpers import first_file, fmt_out_message, out_dir, save_target

PDF_FILTER = "PDF (*.pdf)"


def _fitz(ctx: RunContext):
    try:
        import fitz

        return fitz
    except Exception:
        raise ToolError(ctx.M(
            "برای این ابزار PyMuPDF لازم است: pip install PyMuPDF",
            "This tool needs PyMuPDF: pip install PyMuPDF"))


def _pypdf(ctx: RunContext):
    try:
        from pypdf import PdfReader, PdfWriter

        return PdfReader, PdfWriter
    except Exception:
        raise ToolError(ctx.M(
            "برای این ابزار pypdf لازم است: pip install pypdf",
            "This tool needs pypdf: pip install pypdf"))


# ----------------------------------------------------------------- attachments
@tool(
    id="pdf_attachments", category="pdf", name="پیوست PDF", name_en="PDF attachments",
    desc="فایل‌های همراهِ داخل یک PDF را فهرست، استخراج یا فایل تازه‌ای به آن پیوست می‌کند.",
    desc_en="Lists, extracts or adds embedded file attachments inside a PDF.",
    icon="archive", order=50, keywords=("attachment", "embedded", "پیوست", "ضمیمه", "فایل همراه"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("mode", K_CHOICE, "کار", "Action", default="list",
              choices=[("list", "فهرست", "List"), ("extract", "استخراج", "Extract"),
                       ("add", "افزودن", "Add")]),
        Field("file", K_FILE, "فایل برای افزودن", "File to attach", visible_when=("mode", "add")),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def pdf_attachments(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    mode = str(v.get("mode") or "list")
    reader = PdfReader(str(source))
    if mode == "add":
        extra = str(v.get("file") or "").strip()
        if not extra or not Path(extra).exists():
            raise ToolError(ctx.M("فایل پیوست را انتخاب کنید.", "Choose a file to attach."))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        for name, content in (reader.attachments or {}).items():
            writer.add_attachment(name, content)
        writer.add_attachment(Path(extra).name, Path(extra).read_bytes())
        target = save_target(ctx, "", f"{source.stem}_attached.pdf")
        with target.open("wb") as handle:
            writer.write(handle)
        return RunResult(message=ctx.M(f"«{Path(extra).name}» پیوست شد.", f"“{Path(extra).name}” attached."),
                         outputs=[str(target)], open_target=str(target))
    attachments = reader.attachments or {}
    if not attachments:
        return RunResult(message=ctx.M("این PDF هیچ پیوستی ندارد.", "This PDF has no attachments."),
                         details="—")
    if mode == "list":
        lines = [f"• {name} ({len(contents[0]) if contents else 0:,} bytes)"
                 for name, contents in attachments.items()]
        return RunResult(message=ctx.M(f"{len(attachments)} پیوست", f"{len(attachments)} attachment(s)"),
                         details="\n".join(lines))
    target = out_dir(ctx, f"{source.stem}_attachments")
    made: List[str] = []
    for name, contents in attachments.items():
        for index, content in enumerate(contents):
            suffix = "" if index == 0 else f"_{index + 1}"
            dest = target / f"{Path(name).stem}{suffix}{Path(name).suffix}"
            dest.write_bytes(content)
            made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# --------------------------------------------------------------------- forms
@tool(
    id="pdf_forms_list", category="pdf", name="فیلدهای فرم PDF", name_en="PDF form fields",
    desc="فیلدهای فرم (AcroForm) یک PDF را با نوع و مقدار فعلی فهرست می‌کند.",
    desc_en="Lists the AcroForm fields of a PDF with their type and current value.",
    icon="list", order=51, keywords=("form", "acroform", "fields", "فرم", "فیلد", "پرسشنامه"),
    fields=[Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER)],
)
def pdf_forms_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, _ = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    fields = PdfReader(str(source)).get_fields() or {}
    if not fields:
        return RunResult(message=ctx.M("این PDF فرم پرکردنی ندارد.", "This PDF has no fillable form."),
                         details="—")
    lines = []
    for name, meta in fields.items():
        kind = str(meta.get("/FT") or "?")
        value = str(meta.get("/V") or "")
        lines.append(f"• {name} [{kind}] {value}")
    return RunResult(message=ctx.M(f"{len(fields)} فیلد", f"{len(fields)} field(s)"),
                     details="\n".join(lines))


@tool(
    id="pdf_forms_fill", category="pdf", name="پر کردن فرم PDF", name_en="Fill a PDF form",
    desc="فیلدهای فرم را با خطوط key=value پر می‌کند و نسخه‌ی تازه می‌سازد.",
    desc_en="Fills form fields from key=value lines and writes a new PDF.",
    icon="pencil", order=52, keywords=("fill form", "form", "پر کردن", "فرم", "اکروفرم"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("fields", K_MULTILINE, "مقادیر (هر خط name=value)", "Values (name=value per line)",
              required=True, placeholder="FirstName=علی\nLastName=رضایی"),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_forms_fill(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    pairs: Dict[str, str] = {}
    for line in str(v.get("fields") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip():
                pairs[key.strip()] = value.strip()
    if not pairs:
        raise ToolError(ctx.M("خط «name=value» وارد کنید.", "Enter at least one name=value line."))
    reader = PdfReader(str(source))
    known = set((reader.get_fields() or {}).keys())
    unknown = [key for key in pairs if key not in known]
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.update_page_form_field_values(writer.pages[0], pairs, auto_regenerate=False)
    target = save_target(ctx, str(v.get("out") or ""), f"{source.stem}_filled.pdf")
    with target.open("wb") as handle:
        writer.write(handle)
    details = f"{len(pairs)} مقدار نوشته شد."
    if unknown:
        details += "\n" + ctx.M("فیلد ناشناخته: ", "Unknown fields: ") + ", ".join(unknown[:10])
    return RunResult(message=ctx.M(f"{len(pairs)} فیلد پر شد.", f"{len(pairs)} field(s) filled."),
                     details=details, outputs=[str(target)], open_target=str(target))


# ------------------------------------------------------------------- bookmark
@tool(
    id="pdf_split_bookmarks", category="pdf", name="تفکیک بر اساس فهرست", name_en="Split by bookmarks",
    desc="PDF را بر اساس فصل‌های فهرست (bookmark/outline) به فایل‌های جدا می‌شکند.",
    desc_en="Splits a PDF into one file per bookmark (chapter) found in its outline.",
    icon="pdf", order=53, keywords=("split", "bookmarks", "outline", "chapters", "تفکیک", "فصل"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def pdf_split_bookmarks(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    reader = PdfReader(str(source))
    try:
        outline = reader.outline or []
    except Exception:
        outline = []
    chapters: List[Tuple[str, int]] = []

    def walk(items) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            title = str(getattr(item, "title", "") or "section")
            try:
                page_number = reader.get_destination_page_number(item)
            except Exception:
                continue
            chapters.append((title, int(page_number)))

    walk(outline)
    if not chapters:
        raise ToolError(ctx.M("این PDF فهرست/بوکمارک ندارد.", "This PDF has no bookmarks."))
    chapters.sort(key=lambda item: item[1])
    target = out_dir(ctx, f"{source.stem}_chapters")
    made: List[str] = []
    for index, (title, start) in enumerate(chapters):
        end = chapters[index + 1][1] if index + 1 < len(chapters) else len(reader.pages)
        if end <= start:
            continue
        writer = PdfWriter()
        for page_number in range(start, end):
            writer.add_page(reader.pages[page_number])
        safe = "".join(char for char in title if char not in '\\/:*?"<>|')[:60] or f"part{index + 1}"
        dest = target / f"{index + 1:02d} - {safe}.pdf"
        with dest.open("wb") as handle:
            writer.write(handle)
        made.append(str(dest))
        ctx.progress(index + 1, len(chapters), safe)
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ---------------------------------------------------------------------- layout
@tool(
    id="pdf_booklet", category="pdf", name="چیدمان دفترچه‌ای (Booklet)", name_en="Booklet imposition",
    desc="صفحه‌ها را دوبه‌دو کنار هم می‌چیند تا با چاپ دورو، دفترچه‌ی صحافی‌شده بسازید.",
    desc_en="Puts two pages side by side so double-sided printing folds into a real booklet.",
    icon="layers", order=54, keywords=("booklet", "imposition", "دفترچه", "چاپ", "صحافی"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("per_sheet", K_INT, "صفحه در هر برگ", "Pages per sheet", default=2, minimum=2, maximum=4),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_booklet(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = first_file(v, "src", ctx)
    doc = fitz.open(str(source))
    count = doc.page_count
    if count < 2:
        raise ToolError(ctx.M("برای دفترچه حداقل دو صفحه لازم است.", "A booklet needs at least two pages."))
    per_sheet = 2 if int(v.get("per_sheet") or 2) < 3 else 4
    total = count + (-count) % per_sheet
    # saddle-stitch order: outer pages first, always paired from both ends
    queue = list(range(total))
    spreads: List[List[int]] = []
    while queue:
        spreads.append([queue.pop(-1), queue.pop(0)])
        if queue:
            spreads.append([queue.pop(0), queue.pop(-1)])
    width, height = doc[0].rect.width, doc[0].rect.height
    output = fitz.open()
    step = 2 if per_sheet == 4 else 1
    sheets = 0
    for index in range(0, len(spreads), step):
        group = spreads[index:index + step]
        page = output.new_page(width=width, height=height)
        sheets += 1
        for spread_index, spread in enumerate(group):
            for slot, page_number in enumerate(spread):
                if page_number >= count:
                    continue
                if per_sheet == 2:
                    target = fitz.Rect(slot * width / 2, 0, (slot + 1) * width / 2, height)
                else:
                    column = slot % 2
                    row = spread_index
                    target = fitz.Rect(column * width / 2, row * height / 2,
                                       (column + 1) * width / 2, (row + 1) * height / 2)
                page.show_pdf_page(target, doc, page_number, keep_proportion=True)
        ctx.progress(sheets, (len(spreads) + step - 1) // step, f"{sheets}")
    dest = save_target(ctx, str(v.get("out") or ""), f"{source.stem}_booklet.pdf")
    output.save(str(dest))
    output.close()
    doc.close()
    return RunResult(message=ctx.M(f"دفترچه با {sheets} برگ ساخته شد.", f"Booklet with {sheets} sheets built."),
                     outputs=[str(dest)], open_target=str(dest))


@tool(
    id="pdf_poster", category="pdf", name="چاپ پوستری (Tile)", name_en="Poster tiling",
    desc="یک صفحه‌ی بزرگ را به کاشی‌های A4 می‌شکند تا با چاپ و چسباندن، پوستر بسازید.",
    desc_en="Splits big pages into A4 tiles you can print and glue into a poster.",
    icon="grid", order=55, keywords=("poster", "tile", "a4", "پوستر", "کاشی", "چاپ بزرگ"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("cols", K_INT, "ستون‌ها", "Columns", default=2, minimum=1, maximum=6),
        Field("rows", K_INT, "ردیف‌ها", "Rows", default=2, minimum=1, maximum=6),
        Field("overlap", K_INT, "همپوشانی (نقطه)", "Overlap (pt)", default=12, minimum=0, maximum=60),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_poster(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = first_file(v, "src", ctx)
    doc = fitz.open(str(source))
    cols = max(1, int(v.get("cols") or 2))
    rows = max(1, int(v.get("rows") or 2))
    overlap = float(v.get("overlap") or 0)
    output = fitz.open()
    total_tiles = 0
    for page_index in range(doc.page_count):
        ctx.progress(page_index + 1, doc.page_count, str(page_index + 1))
        rect = doc[page_index].rect
        tile_w = rect.width / cols
        tile_h = rect.height / rows
        for row in range(rows):
            for col in range(cols):
                clip = fitz.Rect(col * tile_w - overlap, row * tile_h - overlap,
                                 (col + 1) * tile_w + overlap, (row + 1) * tile_h + overlap)
                clip = clip & rect
                if clip.is_empty:
                    continue
                new_page = output.new_page(width=clip.width, height=clip.height)
                new_page.show_pdf_page(new_page.rect, doc, page_index, clip=clip)
                total_tiles += 1
    dest = save_target(ctx, str(v.get("out") or ""), f"{source.stem}_poster.pdf")
    output.save(str(dest))
    output.close()
    doc.close()
    return RunResult(message=ctx.M(f"{total_tiles} کاشی آماده چاپ شد.", f"{total_tiles} printable tiles created."),
                     outputs=[str(dest)], open_target=str(dest))


@tool(
    id="pdf_page_size", category="pdf", name="یکسان‌سازی اندازه‌ی صفحه", name_en="Normalise page size",
    desc="همه‌ی صفحه‌های PDF را به A4، Letter یا مربع تبدیل می‌کند.",
    desc_en="Rescales every page to A4, Letter or square.",
    icon="pdf", order=56, keywords=("page size", "a4", "letter", "اندازه", "a4", "یکسان"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("size", K_CHOICE, "اندازه", "Size", default="a4",
              choices=[("a4", "A4", "A4"), ("letter", "Letter", "Letter"),
                       ("square", "مربع", "Square"), ("a5", "A5", "A5")]),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_page_size(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = first_file(v, "src", ctx)
    sizes = {"a4": (595, 842), "letter": (612, 792), "a5": (420, 595), "square": (600, 600)}
    width, height = sizes.get(str(v.get("size") or "a4"), sizes["a4"])
    doc = fitz.open(str(source))
    pages = doc.page_count
    output = fitz.open()
    for index in range(pages):
        ctx.progress(index + 1, pages, str(index + 1))
        page = output.new_page(width=width, height=height)
        page.show_pdf_page(page.rect, doc, index, keep_proportion=True)
    dest = save_target(ctx, str(v.get("out") or ""), f"{source.stem}_{v_const(v)}.pdf")
    output.save(str(dest))
    output.close()
    doc.close()
    return RunResult(message=ctx.M(f"{pages} صفحه یکسان شد.", f"{pages} page(s) normalised."),
                     outputs=[str(dest)], open_target=str(dest))


def v_const(v: Dict[str, Any]) -> str:
    return str(v.get("size") or "a4")


# ---------------------------------------------------------------- extract images
@tool(
    id="pdf_extract_images", category="pdf", name="استخراج تصاویر PDF", name_en="Extract PDF images",
    desc="همه‌ی تصاویر جاسازی‌شده در PDF را با کیفیت اصلی بیرون می‌کشد.",
    desc_en="Extracts every embedded image from a PDF at its original quality.",
    icon="image", order=57, keywords=("extract images", "photos", "استخراج", "تصویر", "عکس"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def pdf_extract_images(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = first_file(v, "src", ctx)
    doc = fitz.open(str(source))
    target = out_dir(ctx, f"{source.stem}_images")
    made: List[str] = []
    seen = set()
    for page_index in range(doc.page_count):
        ctx.progress(page_index + 1, doc.page_count, str(page_index + 1))
        for image_info in doc[page_index].get_images(full=True):
            xref = image_info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                data = doc.extract_image(xref)
            except Exception:
                continue
            extension = data.get("ext", "png")
            dest = target / f"image_{len(made) + 1:03d}.{extension}"
            dest.write_bytes(data["image"])
            made.append(str(dest))
    doc.close()
    if not made:
        raise ToolError(ctx.M("تصویری داخل این PDF نبود.", "This PDF contains no images."))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# -------------------------------------------------------------------- markdown
@tool(
    id="pdf_to_markdown", category="pdf", name="PDF به Markdown", name_en="PDF → Markdown",
    desc="متن PDF را با تشخیص سرتیتر (بر اساس اندازه‌ی قلم) به Markdown ساخت‌یافته تبدیل می‌کند.",
    desc_en="Converts PDF text to structured Markdown, guessing headings from font sizes.",
    icon="text", order=58, keywords=("markdown", "md", "convert", "مارک‌داون", "تبدیل", "سرتیتر"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="Markdown (*.md)"),
    ],
)
def pdf_to_markdown(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = first_file(v, "src", ctx)
    doc = fitz.open(str(source))
    lines: List[str] = [f"# {source.stem}", ""]
    for page_index in range(doc.page_count):
        ctx.progress(page_index + 1, doc.page_count, str(page_index + 1))
        blocks = doc[page_index].get_text("dict").get("blocks", [])
        sizes: List[float] = []
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(float(span.get("size", 10)))
        body = sorted(sizes)[len(sizes) // 2] if sizes else 10.0
        for block in blocks:
            for line in block.get("lines", []):
                parts: List[str] = []
                biggest = 0.0
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text.strip():
                        biggest = max(biggest, float(span.get("size", 10)))
                        parts.append(text)
                text = "".join(parts).strip()
                if not text:
                    continue
                if biggest >= body * 1.6:
                    lines.append(f"## {text}")
                elif biggest >= body * 1.25:
                    lines.append(f"### {text}")
                elif text.startswith(("•", "-", "*")):
                    lines.append(f"- {text.lstrip('•-* ').strip()}")
                else:
                    lines.append(text)
        lines.append("")
    doc.close()
    markdown = "\n".join(lines)
    dest = save_target(ctx, str(v.get("out") or ""), f"{source.stem}.md")
    dest.write_text(markdown, encoding="utf-8")
    return RunResult(message=ctx.M(f"Markdown ساخته شد ({len(markdown):,} کاراکتر).",
                                   f"Markdown written ({len(markdown):,} chars)."),
                     details=markdown[:1500], outputs=[str(dest)], open_target=str(dest))


# -------------------------------------------------------------------- compare
@tool(
    id="pdf_compare", category="pdf", name="مقایسه‌ی دو PDF", name_en="Compare two PDFs",
    desc="متن دو PDF را صفحه‌به‌صفحه مقایسه می‌کند و تفاوت‌ها را گزارش می‌دهد.",
    desc_en="Compares the text of two PDFs page by page and reports what changed.",
    icon="diff", order=59, keywords=("compare", "diff", "versions", "مقایسه", "تفاوت", "نسخه"),
    fields=[
        Field("first", K_FILE, "PDF اول", "First PDF", required=True, filt=PDF_FILTER),
        Field("second", K_FILE, "PDF دوم", "Second PDF", required=True, filt=PDF_FILTER),
        Field("out", K_SAVE, "ذخیره‌ی گزارش", "Save report", filt="Text (*.txt)"),
    ],
)
def pdf_compare(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    left = Path(str(v.get("first") or ""))
    right = Path(str(v.get("second") or ""))
    for path in (left, right):
        if not path.exists():
            raise ToolError(ctx.M(f"فایل پیدا نشد: {path}", f"File not found: {path}"))
        if path.is_dir():
            raise ToolError(ctx.M(f"این یک پوشه است، نه PDF: {path}",
                                  f"That is a folder, not a PDF: {path}"))
    try:
        first_doc, second_doc = fitz.open(str(left)), fitz.open(str(right))
    except Exception as exc:                      # broken/encrypted/not a PDF
        raise ToolError(ctx.M(f"PDF باز نشد: {exc}", f"Could not open the PDF: {exc}"))
    pages = max(first_doc.page_count, second_doc.page_count)
    report: List[str] = [f"{left.name} ({first_doc.page_count} صفحه) ↔ "
                         f"{right.name} ({second_doc.page_count} صفحه)", "=" * 50]
    changed = 0
    for index in range(pages):
        ctx.progress(index + 1, pages, str(index + 1))
        first_text = first_doc[index].get_text() if index < first_doc.page_count else ""
        second_text = second_doc[index].get_text() if index < second_doc.page_count else ""
        if first_text.strip() == second_text.strip():
            continue
        changed += 1
        diff = list(difflib.unified_diff(first_text.splitlines(), second_text.splitlines(),
                                         lineterm="", n=0))
        body = "\n".join(line for line in diff[2:22] if line.startswith(("+", "-")))
        report.append(f"\n--- صفحه {index + 1} ---\n{body or '(متن فقط فاصله/چیدمان تفاوت دارد)'}")
    first_doc.close()
    second_doc.close()
    if not changed:
        report.append("\nمتن دو فایل یکسان است.")
    text = "\n".join(report)
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        dest = save_target(ctx, requested, "pdf_compare.txt")
        dest.write_text(text, encoding="utf-8")
        saved.append(str(dest))
    message = (ctx.M(f"{changed} صفحه تفاوت دارد.", f"{changed} page(s) differ.")
               if changed else ctx.M("متن دو PDF یکسان است.", "The two PDFs have identical text."))
    return RunResult(message=message, details=text[:4000], outputs=saved)


# ---------------------------------------------------------------- redaction
@tool(
    id="pdf_redact", category="pdf", name="حذف واقعی محتوا از PDF (Redaction)",
    name_en="Redact PDF content",
    desc=("متن یا ناحیه‌ی انتخابی را نه فقط سیاه، بلکه برای همیشه از فایل پاک می‌کند؛ "
          "بعد از ذخیره، متن حذف‌شده با کپی‌کردن یا استخراج قابل بازیابی نیست."),
    desc_en=("Permanently remove text or an area from a PDF — after saving, the hidden words "
             "cannot be copied or extracted back out."),
    icon="shield", order=123,
    keywords=("redact", "black out", "hide text pdf", "سیاه کردن", "حذف متن pdf",
              "محرمانه", "پاک کردن اطلاعات", "redaction"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("mode", K_CHOICE, "روش انتخاب", "Selection method", default="search",
              choices=[("search", "کلمه‌ها/عبارت‌ها", "Words or phrases"),
                       ("area", "ناحیه‌ی مستطیلی", "Rectangular area")],
              help="«کلمه‌ها» امن‌ترین است: خودِ متن پیدا و پاک می‌شود.",
              help_en="“Words” is the safest: the text itself is located and removed."),
        Field("terms", K_MULTILINE, "عبارت‌هایی که پاک شوند (هر خط یکی)", "Phrases to remove (one per line)",
              default="", placeholder="شماره کارت\nکلمه عبور", placeholder_en="card number\npassword",
              visible_when=("mode", "search"),
              help="حساس به حروف نیست و همه‌ی تکرارها در همه‌ی صفحه‌ها پاک می‌شوند.",
              help_en="Case-insensitive; every occurrence on every page is removed."),
        Field("pages", K_TEXT, "صفحه‌ها", "Pages", default="all", placeholder="all یا 1-3,7",
              help="خالی = همه‌ی صفحه‌ها.", help_en="Empty = every page."),
        Field("area", K_TEXT, "ناحیه (٪): x,y,عرض,ارتفاع", "Area (%): x,y,w,h", default="5,5,40,12",
              visible_when=("mode", "area"),
              help="درصد نسبت به اندازه‌ی صفحه، از گوشه‌ی بالا-چپ.",
              help_en="Percent of the page, measured from the top-left corner."),
        Field("images", K_CHOICE, "تصویرهای داخل ناحیه", "Images inside the area", default="remove",
              choices=[("remove", "کامل حذف شوند", "Remove them"),
                       ("keep", "دست‌نخورده بمانند", "Leave them")],
              help="اگر عکس هم بخشی از اطلاعات محرمانه است، «حذف» را انتخاب کنید.",
              help_en="Pick “remove” when the picture itself is sensitive."),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_redact(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    src = first_file(v, "src", ctx)
    mode = str(v.get("mode") or "search")
    keep_images = str(v.get("images") or "remove") == "keep"

    terms = [line.strip() for line in str(v.get("terms") or "").splitlines() if line.strip()]
    if mode == "search" and not terms:
        raise ToolError(ctx.M("عبارتی برای پاک‌کردن وارد نشده است.",
                              "No phrase to redact was given."))
    if mode == "area":
        try:
            parts = [float(part) for part in str(v.get("area") or "").replace("،", ",").split(",")]
        except ValueError:
            parts = []
        if len(parts) != 4:
            raise ToolError(ctx.M("ناحیه باید چهار عدد باشد: x,y,عرض,ارتفاع (درصد).",
                                  "The area needs four numbers: x,y,w,h (percent)."))
        x, y, w, h = parts
        if w <= 0 or h <= 0 or w > 100 or h > 100:
            raise ToolError(ctx.M("عرض و ارتفاع ناحیه باید بین ۰ و ۱۰۰ درصد باشد.",
                                  "Area width and height must be between 0 and 100 percent."))

    try:
        doc = fitz.open(str(src))
    except Exception as exc:
        raise ToolError(ctx.M(f"فایل PDF باز نشد: {exc}", f"Could not open the PDF: {exc}"))

    if doc.needs_pass:
        doc.close()
        raise ToolError(ctx.M("این PDF رمز دارد؛ اول با ابزار «برداشتن رمز PDF» بازش کنید.",
                              "This PDF is encrypted; decrypt it first."))

    total = doc.page_count
    pages_text = str(v.get("pages") or "").strip().lower()
    if pages_text in ("", "all", "*", "همه"):
        targets = list(range(total))
    else:
        try:
            targets = parse_ranges(pages_text, total)
        except Exception:
            doc.close()
            raise ToolError(ctx.M("شماره‌ی صفحه‌ها را مثل «1-3,7» یا «all» بنویسید.",
                                 "Write the pages like “1-3,7” or “all”."))
    if not targets:
        doc.close()
        raise ToolError(ctx.M("هیچ صفحه‌ای انتخاب نشد.", "No page was selected."))
    image_rule = fitz.PDF_REDACT_IMAGE_NONE if keep_images else fitz.PDF_REDACT_IMAGE_REMOVE
    removed = 0
    words = 0
    empty_pages: List[int] = []
    for page_index in targets:
        ctx.progress(page_index + 1, total, ctx.M(f"صفحه {page_index + 1}", f"Page {page_index + 1}"))
        page = doc[page_index]
        hits = 0
        if mode == "search":
            for term in terms:
                for rect in page.search_for(term):
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    hits += 1
        else:
            box = page.rect
            rect = fitz.Rect(box.x0 + box.width * x / 100.0, box.y0 + box.height * y / 100.0,
                             box.x0 + box.width * (x + w) / 100.0,
                             box.y0 + box.height * (y + h) / 100.0)
            if rect.is_empty:
                continue
            page.add_redact_annot(rect, fill=(0, 0, 0))
            hits += 1
        if not hits:
            empty_pages.append(page_index + 1)
            continue
        # apply_redactions() physically rewrites the content stream: the glyphs and
        # the text operators are gone, so nothing can be copied or extracted back.
        page.apply_redactions(images=image_rule)
        removed += hits
        words += hits if mode == "search" else 0

    if not removed:
        doc.close()
        hint = ctx.M("عبارت‌ها در صفحه‌های انتخاب‌شده پیدا نشدند.",
                     "None of the phrases occur on the selected pages.")
        if mode == "search":
            hint += ctx.M(" اگر متن داخل عکس است، اول ابزار «OCR روی PDF» را اجرا کنید.",
                          " If the text lives inside an image, run “OCR on PDF” first.")
        raise ToolError(hint)

    dest = save_target(ctx, str(v.get("out") or "").strip(), f"{src.stem}_redacted.pdf")
    doc.save(str(dest), garbage=4, deflate=True)
    doc.close()

    # Verification pass: reopen the saved file and make sure the words really are gone.
    left = 0
    verify = fitz.open(str(dest))
    try:
        if mode == "search":
            for page_index in range(verify.page_count):
                text = verify[page_index].get_text().lower()
                left += sum(text.count(term.lower()) for term in terms)
    finally:
        verify.close()

    size = human_size(dest.stat().st_size)
    if mode == "search":
        message = ctx.M(
            f"{words} مورد در {len(targets)} صفحه پاک شد · حجم {size}",
            f"{words} match(es) removed across {len(targets)} page(s) · {size}",
        )
        if left:
            message += ctx.M(f" · هشدار: {left} مورد هنوز پیدا می‌شود",
                             f" · warning: {left} occurrence(s) still readable")
        else:
            message += ctx.M(" · تأیید شد: متن حذف‌شده دیگر استخراج نمی‌شود",
                             " · verified: the redacted text can no longer be extracted")
    else:
        message = ctx.M(f"{removed} ناحیه سیاه شد · حجم {size}",
                        f"{removed} area(s) redacted · {size}")
    details = ctx.M("صفحه‌هایی که چیزی برای پاک‌کردن نداشتند: ",
                    "Pages with nothing to redact: ")
    details += ", ".join(str(n) for n in empty_pages[:40]) or ctx.M("هیچ", "none")
    return RunResult(message=message, details=details, outputs=[str(dest)],
                     open_target=str(dest.parent))
