"""Advanced PDF tools: page surgery, crop, stamps, search, metadata."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

from ..core import persian_pdf
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size
from ._helpers import first_file, page_ranges, save_target
from .pdf_tools import PDF_FILTER, _fitz, _open_doc, _open_reader, _pypdf


def _writer(ctx: RunContext, src: Path, values: Dict[str, Any], suffix: str) -> Path:
    requested = str(values.get("out") or "").strip()
    return save_target(ctx, requested, f"{src.stem}{suffix}.pdf")


# ------------------------------------------------------------------ page edits
@tool(
    id="pdf_delete_pages", category="pdf", name="حذف صفحه‌های PDF", name_en="Delete PDF pages",
    danger=True,
    desc="صفحه‌های انتخابی را از PDF حذف می‌کند (مثال: 2,5-7).",
    desc_en="Remove selected pages from a PDF (e.g. 2,5-7).",
    icon="scissors", order=115,
    keywords=("delete pdf pages", "remove page", "حذف صفحه", "پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("pages", K_TEXT, "صفحه‌هایی که حذف شوند", "Pages to delete",
              default="1", placeholder="2,5-7", placeholder_en="2,5-7", help="شماره‌ی صفحه‌ها از ۱ شروع می‌شود."
              , help_en="Page numbers start at 1."),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_delete_pages(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total = len(reader.pages)
    drop = set(page_ranges(ctx, str(v.get("pages") or ""), total))
    if not drop:
        raise ToolError(ctx.M("شماره‌ی صفحه‌ی معتبری وارد نشد.", "No valid page numbers given."))
    if len(drop) >= total:
        raise ToolError(ctx.M("همه‌ی صفحه‌ها را نمی‌توان حذف کرد.",
                             "You cannot delete every page of the document."))
    pypdf = _pypdf(ctx)
    writer = pypdf.PdfWriter()
    kept = 0
    for index, page in enumerate(reader.pages):
        if index in drop:
            continue
        writer.add_page(page)
        kept += 1
        ctx.progress(index, total, ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
    dest = _writer(ctx, src, v, "_edited")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"{len(drop)} صفحه حذف شد؛ {kept} صفحه ماند.\n{dest}",
                      f"Removed {len(drop)} page(s); {kept} kept.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
        details=f"{src.name} → {dest.name} ({human_size(dest.stat().st_size)})",
    )


@tool(
    id="pdf_reorder_pages", category="pdf", name="چیدمان دوباره‌ی صفحه‌ها",
    name_en="Reorder PDF pages",
    desc="ترتیب صفحه‌ها را با یک الگو عوض می‌کند (مثال: 3,1,2,5-7).",
    desc_en="Rebuild the page order from a pattern (e.g. 3,1,2,5-7).",
    icon="sort", order=116,
    keywords=("reorder pdf", "page order", "ترتیب صفحه", "مرتب کردن پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("order", K_TEXT, "ترتیب جدید", "New order", default="1", placeholder="3,1,2",
              placeholder_en="3,1,2"),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_reorder_pages(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total = len(reader.pages)
    order = page_ranges(ctx, str(v.get("order") or ""), total)
    if not order:
        raise ToolError(ctx.M("الگوی ترتیب خالی است.", "The order pattern is empty."))
    pypdf = _pypdf(ctx)
    writer = pypdf.PdfWriter()
    for position, index in enumerate(order, start=1):
        ctx.progress(position, len(order), ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
        writer.add_page(reader.pages[index])
    dest = _writer(ctx, src, v, "_reordered")
    with open(dest, "wb") as fh:
        writer.write(fh)
    note = ""
    if len(order) < total:
        note = ctx.M(f"\nتوجه: {total - len(order)} صفحه در الگو نبود و حذف شد.",
                     f"\nNote: {total - len(order)} page(s) were not in the pattern and were dropped.")
    return RunResult(
        message=ctx.M(f"ترتیب {len(order)} صفحه اعمال شد.\n{dest}{note}",
                      f"Reordered {len(order)} page(s).\n{dest}{note}")
        if False else ctx.M(f"ترتیب {len(order)} صفحه اعمال شد.\n{dest}", f"Reordered {len(order)} page(s).\n{dest}") + note,
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_insert_blank", category="pdf", name="افزودن صفحه‌ی خالی",
    name_en="Insert blank pages",
    desc="بعد از هر صفحه (یا فقط یک‌بار) یک صفحه‌ی خالی هم‌اندازه اضافه می‌کند.",
    desc_en="Add a blank page — after every page, or once at a position.",
    icon="file", order=117,
    keywords=("blank page", "insert page", "صفحه خالی", "افزودن صفحه"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("mode", K_CHOICE, "حالت", "Mode", default="every",
              choices=[("every", "بعد از هر صفحه", "After every page"),
                       ("position", "در یک موقعیت مشخص", "At one position")]),
        Field("position", K_INT, "بعد از صفحه‌ی شماره", "After page number", default=1,
              minimum=1, maximum=10000, visible_when=("mode", "position")),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_insert_blank(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    pypdf = _pypdf(ctx)
    writer = pypdf.PdfWriter()
    total = len(reader.pages)
    mode = str(v.get("mode") or "every")
    position = int(v.get("position") or 1)
    added = 0
    for index in range(total):
        page = reader.pages[index]
        writer.add_page(page)
        box = page.mediabox
        if mode == "every" or (index + 1) == position:
            writer.add_blank_page(width=float(box.width), height=float(box.height))
            added += 1
        ctx.progress(index + 1, total, ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
    dest = _writer(ctx, src, v, "_blank")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"{added} صفحه‌ی خالی اضافه شد.\n{dest}", f"Added {added} blank page(s).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_crop", category="pdf", name="برش حاشیه‌ی PDF", name_en="Crop PDF margins",
    desc="حاشیه‌ها را به درصد برش می‌دهد تا محتوا بزرگ‌تر دیده شود.",
    desc_en="Crop margins by percentage so the content fills more of the page.",
    icon="crop", order=118,
    keywords=("crop pdf", "margin", "برش پی دی اف", "حاشیه"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("top", K_FLOAT, "بالا (%)", "Top (%)", default=5.0, minimum=0, maximum=40, decimals=1),
        Field("bottom", K_FLOAT, "پایین (%)", "Bottom (%)", default=5.0, minimum=0, maximum=40, decimals=1),
        Field("left", K_FLOAT, "چپ (%)", "Left (%)", default=5.0, minimum=0, maximum=40, decimals=1),
        Field("right", K_FLOAT, "راست (%)", "Right (%)", default=5.0, minimum=0, maximum=40, decimals=1),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_crop(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    pypdf = _pypdf(ctx)
    writer = pypdf.PdfWriter()
    total = len(reader.pages)
    for index in range(total):
        page = reader.pages[index]
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        left = float(v.get("left") or 0) / 100 * width
        right = float(v.get("right") or 0) / 100 * width
        top = float(v.get("top") or 0) / 100 * height
        bottom = float(v.get("bottom") or 0) / 100 * height
        if width - left - right < 30 or height - top - bottom < 30:
            raise ToolError(ctx.M("مقدار برش بیش از حد بزرگ است.", "The crop is too aggressive."))
        from pypdf.generic import RectangleObject

        page.cropbox = RectangleObject([
            float(box.left) + left, float(box.bottom) + bottom,
            float(box.right) - right, float(box.top) - top,
        ])
        writer.add_page(page)
        ctx.progress(index + 1, total, ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
    dest = _writer(ctx, src, v, "_cropped")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"حاشیه‌ی {total} صفحه برش خورد.\n{dest}", f"Cropped margins on {total} page(s).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_to_html", category="pdf", name="PDF به HTML", name_en="PDF → HTML",
    desc="متن PDF را در یک صفحه‌ی HTML تمیز و قابل جست‌وجو ذخیره می‌کند.",
    desc_en="Export the PDF text as a clean, searchable HTML page.",
    icon="code", order=119,
    keywords=("pdf to html", "html", "پی دی اف به اچ تی ام ال"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("page_break", K_BOOL, "خط جداکننده بین صفحه‌ها", "Separator between pages", default=True),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="HTML (*.html)"),
    ],
)
def pdf_to_html(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    import html as html_mod

    src = first_file(v, "src", ctx)
    doc = _open_doc(ctx, src)
    esc = html_mod.escape
    try:
        parts: List[str] = []
        total = doc.page_count
        rtl_pages = 0
        for index in range(total):
            ctx.progress(index, total, ctx.M(f"صفحه {index + 1} از {total}", f"Page {index + 1} of {total}"))
            content = persian_pdf.extract_page(doc, index, tables=True, images=False)
            if content.rtl:
                rtl_pages += 1
            body: List[str] = []
            for item in content.items:
                if isinstance(item, persian_pdf.Paragraph):
                    if not item.text.strip():
                        continue
                    direction = "rtl" if item.rtl else "ltr"
                    align = {"center": "center", "end": "left" if item.rtl else "right"}.get(
                        item.align, "right" if item.rtl else "left")
                    runs = []
                    for span in item.spans:
                        chunk = esc(span.text)
                        if span.bold:
                            chunk = f"<b>{chunk}</b>"
                        if span.italic:
                            chunk = f"<i>{chunk}</i>"
                        runs.append(chunk)
                    inner = "".join(runs)
                    if item.heading:
                        tag = f"h{min(4, item.heading + 1)}"
                        body.append(f'<{tag} dir="{direction}" style="text-align:{align}">{inner}</{tag}>')
                    elif item.bullet:
                        body.append(f'<ul dir="{direction}"><li style="text-align:{align}">{inner}</li></ul>')
                    else:
                        body.append(f'<p dir="{direction}" style="text-align:{align}">{inner}</p>')
                elif isinstance(item, persian_pdf.Table):
                    rows = []
                    for r_index, row in enumerate(item.rows):
                        tag = "th" if r_index == 0 else "td"
                        rows.append("<tr>" + "".join(
                            f'<{tag} dir="{"rtl" if persian_pdf.is_rtl(cell) else "ltr"}">{esc(cell)}</{tag}>'
                            for cell in row) + "</tr>")
                    body.append(f'<table dir="{"rtl" if item.rtl else "ltr"}">{"".join(rows)}</table>')
            if not body:
                body.append(f'<p class="empty">{esc(ctx.M("(بدون متن قابل استخراج)", "(no extractable text)"))}</p>')
            separator = ' class="page"' if v.get("page_break", True) else ' class="page flat"'
            parts.append(f'<section{separator}><h2 class="pagenum">'
                         f'{esc(ctx.M("صفحه", "Page"))} {index + 1}</h2>{"".join(body)}</section>')
        title = esc(src.stem)
        lang = "fa" if rtl_pages * 2 >= total and total else "en"
        document = f"""<!DOCTYPE html>
<html lang="{lang}" dir="{"rtl" if lang == "fa" else "ltr"}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: Vazirmatn, "Segoe UI", Tahoma, sans-serif; max-width: 900px; margin: 32px auto;
         padding: 0 20px; background: #0f1420; color: #e6ecf5; line-height: 1.9; }}
  h1 {{ color: #18c8f5; }} h2.pagenum {{ color: #7b61ff; font-size: 13px; margin-top: 34px; letter-spacing: .04em; }}
  h2, h3, h4 {{ line-height: 1.5; }}
  .page {{ border-bottom: 1px solid #26314a; padding-bottom: 18px; }}
  .page.flat {{ border: 0; }} .page.flat .pagenum {{ display: none; }}
  p {{ margin: 0 0 .7em 0; }} .empty {{ color: #8a94a8; font-style: italic; }}
  table {{ border-collapse: collapse; margin: 12px 0; width: 100%; }}
  td, th {{ border: 1px solid #2d3a57; padding: 6px 10px; text-align: start; }}
  th {{ background: #18213a; }}
</style></head><body>
<h1>{title}</h1>
{''.join(parts)}
</body></html>"""
        dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}.html")
        dest.write_text(document, encoding="utf-8")
    finally:
        doc.close()
    return RunResult(
        message=ctx.M(f"HTML ساخته شد ({human_size(dest.stat().st_size)}).\n{dest}",
                      f"HTML created ({human_size(dest.stat().st_size)}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_find_text", category="pdf", name="جست‌وجو در PDF", name_en="Search inside PDF",
    desc="یک عبارت را در همه‌ی صفحه‌ها پیدا می‌کند و شماره‌ی صفحه‌ها را می‌دهد.",
    desc_en="Find a phrase across all pages and list where it appears.",
    icon="search", order=120,
    keywords=("find in pdf", "search pdf", "جستجو در پی دی اف", "پیدا کردن"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("query", K_TEXT, "عبارت", "Phrase", required=True, placeholder="متن مورد جست‌وجو", placeholder_en="text to find"),
        Field("ignore_case", K_BOOL, "بدون توجه به بزرگی/کوچکی حروف", "Ignore letter case", default=True),
    ],
)
def pdf_find_text(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    query = str(v.get("query") or "").strip()
    if not query:
        raise ToolError(ctx.M("عبارت جست‌وجو خالی است.", "The search phrase is empty."))
    ignore_case = bool(v.get("ignore_case", True))

    def _fold(text: str) -> str:
        # Persian-aware folding: ي/ك → ی/ک, digits unified, diacritics ignored and
        # «کتاب ها» / «کتاب‌ها» / «کتابها» all treated as the same word
        text = persian_pdf.normalize_persian(text, digits="en", zwnj=True, fix_reversed=False)
        text = persian_pdf.strip_diacritics(text).replace("\u200C", "")
        return text.lower() if ignore_case else text

    needle = _fold(query)
    doc = _open_doc(ctx, src)
    hits: List[str] = []
    count = 0
    try:
        total = doc.page_count
        for index in range(total):
            ctx.progress(index + 1, total, ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
            try:
                pages_text = persian_pdf.extract_page(doc, index, tables=True, images=False).text
            except Exception:
                pages_text = doc[index].get_text("text")
            lines = [line for line in pages_text.splitlines() if needle in _fold(line)]
            found = sum(_fold(line).count(needle) for line in lines)
            if not found:
                continue
            count += found
            sample = " … ".join(line.strip() for line in lines[:2])[:160]
            hits.append(f"• {ctx.M('صفحه', 'Page')} {index + 1} ({found}×): {sample}")
    finally:
        doc.close()
    if not hits:
        return RunResult(message=ctx.M(f"«{query}» پیدا نشد.", f"“{query}” was not found."), details=src.name)
    return RunResult(
        message=ctx.M(f"«{query}» {count} بار در {len(hits)} صفحه پیدا شد.",
                      f"“{query}” found {count} time(s) on {len(hits)} page(s)."),
        details="\n".join(hits),
    )


@tool(
    id="pdf_set_metadata", category="pdf", name="ویرایش اطلاعات PDF",
    name_en="Edit PDF metadata",
    desc="عنوان، نویسنده، موضوع و کلمات کلیدی سند را تنظیم می‌کند.",
    desc_en="Set the title, author, subject and keywords of a document.",
    icon="tag", order=121,
    keywords=("pdf metadata", "title author", "اطلاعات پی دی اف", "متادیتا"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("title", K_TEXT, "عنوان", "Title"),
        Field("author", K_TEXT, "نویسنده", "Author"),
        Field("subject", K_TEXT, "موضوع", "Subject"),
        Field("keywords", K_TEXT, "کلمات کلیدی", "Keywords", placeholder="با کاما جدا کنید", placeholder_en="comma separated"),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_set_metadata(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    pypdf = _pypdf(ctx)
    try:
        writer = pypdf.PdfWriter(clone_from=str(src))
    except Exception as exc:
        raise ToolError(ctx.M(f"این PDF باز نشد: {exc}", f"Could not open the PDF: {exc}"))
    meta = {key: value for key, value in writer.metadata.items() if isinstance(value, str)} if writer.metadata else {}
    mapping = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
    changed = []
    for key, pdf_key in mapping.items():
        value = str(v.get(key) or "").strip()
        if value:
            meta[pdf_key] = value
            changed.append(key)
    if not changed:
        raise ToolError(ctx.M("حداقل یکی از فیلدها را پر کنید.", "Fill at least one of the fields."))
    meta["/Producer"] = "WinGenius"
    writer.add_metadata(meta)
    dest = _writer(ctx, src, v, "_meta")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"اطلاعات PDF به‌روزرسانی شد ({', '.join(changed)}).\n{dest}",
                      f"PDF metadata updated ({', '.join(changed)}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_stamp_image", category="pdf", name="مهر تصویری روی PDF",
    name_en="Stamp image on PDF",
    desc="یک لوگو/امضا را با شفافیت و موقعیت دلخواه روی همه‌ی صفحه‌ها می‌گذارد.",
    desc_en="Place a logo or signature on every page with position and opacity.",
    icon="stamp", order=122,
    keywords=("pdf stamp", "logo on pdf", "امضا", "لوگو روی پی دی اف", "مهر"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("image", K_FILE, "تصویر مهر", "Stamp image", required=True,
              filt="تصاویر (*.png *.jpg *.jpeg);;همه فایل‌ها (*)"),
        Field("position", K_CHOICE, "موقعیت", "Position", default="br",
              choices=[("br", "پایین راست", "Bottom right"), ("bl", "پایین چپ", "Bottom left"),
                       ("tr", "بالا راست", "Top right"), ("tl", "بالا چپ", "Top left"),
                       ("center", "وسط", "Center")]),
        Field("scale", K_INT, "عرض مهر (% صفحه)", "Stamp width (% of page)", default=25,
              minimum=5, maximum=90),
        Field("opacity", K_INT, "شفافیت (%)", "Opacity (%)", default=100, minimum=10, maximum=100),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="PDF (*.pdf)"),
    ],
)
def pdf_stamp_image(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from reportlab.lib.utils import ImageReader

    src = first_file(v, "src", ctx)
    image_path = first_file(v, "image", ctx)
    pypdf = _pypdf(ctx)
    reader = _open_reader(ctx, src)
    writer = pypdf.PdfWriter()
    total = len(reader.pages)
    position = str(v.get("position") or "br")
    scale = max(5, min(90, int(v.get("scale") or 25))) / 100.0
    opacity = max(10, min(100, int(v.get("opacity") or 100))) / 100.0
    for index in range(total):
        ctx.progress(index + 1, total, ctx.M(f"صفحه {index + 1}", f"Page {index + 1}"))
        page = reader.pages[index]
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        layer = canvas.Canvas(buffer, pagesize=(width, height))
        image_reader = ImageReader(str(image_path))
        img_w, img_h = image_reader.getSize()
        draw_w = width * scale
        draw_h = draw_w * img_h / max(1.0, img_w)
        margin = min(width, height) * 0.04
        x = margin if position in ("bl", "tl") else width - draw_w - margin
        y = margin if position in ("bl", "br") else height - draw_h - margin
        if position == "center":
            x = (width - draw_w) / 2
            y = (height - draw_h) / 2
        layer.saveState()
        try:
            layer.setFillAlpha(opacity)
        except Exception:
            pass
        layer.drawImage(image_reader, x, y, draw_w, draw_h, mask="auto")
        layer.restoreState()
        layer.save()
        buffer.seek(0)
        overlay = pypdf.PdfReader(buffer).pages[0]
        page.merge_page(overlay)
        writer.add_page(page)
    dest = _writer(ctx, src, v, "_stamped")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"مهر روی {total} صفحه گذاشته شد.\n{dest}", f"Stamped {total} page(s).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )
