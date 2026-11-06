"""PDF toolkit: convert, merge, split, rotate, compress, protect, watermark."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

from ..core import persian_pdf
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_PASSWORD, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import (
    atomic_write_bytes, ensure_dir, files_or_raise, first_file, fmt_out_message,
    need_windows, out_dir, page_ranges, require, same_file, save_target,
)

PDF_FILTER = "PDF (*.pdf);;همه فایلها (*)"
IMG_FILTER = "تصاویر (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif);;همه فایلها (*)"


def _pypdf(ctx: RunContext):
    return require("pypdf", "pypdf", ctx)


def _fitz(ctx: RunContext):
    """PyMuPDF renamed its import to `pymupdf`; support both."""
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except Exception:
        return require("fitz", "PyMuPDF", ctx)


def _open_reader(ctx: RunContext, path: Path):
    pypdf = _pypdf(ctx)
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as exc:
        raise ToolError(ctx.M(
            f"این فایل PDF باز نشد (شاید خراب یا رمزدار است):\n{exc}",
            f"Could not open this PDF (damaged or encrypted?):\n{exc}",
        ))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ToolError(ctx.M(
                "این PDF با رمز محافظت شده است. ابتدا از ابزار «حذف رمز PDF» استفاده کنید.",
                "This PDF is password protected. Use 'Remove PDF password' first.",
            ))
    return reader


# ---------------------------------------------------------------- pdf → image
@tool(
    id="pdf_to_images", category="pdf", name="PDF به تصویر", name_en="PDF → Images",
    desc="هر صفحه‌ی PDF را به تصویر PNG/JPG/WEBP تبدیل می‌کند.",
    desc_en="Render every PDF page to PNG/JPG/WEBP.",
    icon="image", order=10,
    keywords=("pdf to image", "pdf to png", "pdf to jpg", "پی دی اف به عکس", "صفحه"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("fmt", K_CHOICE, "فرمت خروجی", "Output format", default="png",
              choices=[("png", "PNG (شفاف)", "PNG"), ("jpg", "JPG (کم‌حجم)", "JPG"),
                       ("webp", "WEBP", "WEBP"), ("tiff", "TIFF", "TIFF")]),
        Field("dpi", K_INT, "کیفیت (DPI)", "Quality (DPI)", default=150, minimum=50, maximum=600, step=25),
        Field("quality", K_INT, "کیفیت فشرده‌سازی (JPG/WEBP)", "Compression quality", default=90,
              minimum=30, maximum=100, visible_when=("fmt", ("jpg", "webp"))),
        Field("out", K_DIR, "پوشه‌ی خروجی", "Output folder", required=False),
    ],
)
def pdf_to_images(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    src = first_file(v, "src", ctx)
    target_dir = ensure_dir(v.get("out") or out_dir(ctx) / f"{src.stem}_images")
    doc = fitz.open(str(src))
    try:
        total = doc.page_count
        if total == 0:
            raise ToolError(ctx.M("این PDF هیچ صفحه‌ای ندارد.", "This PDF has no pages."))
        digits = max(2, len(str(total)))
        fmt = str(v.get("fmt") or "png").lower()
        outputs: List[str] = []
        for index in range(total):
            ctx.progress(index, total, ctx.M(f"صفحه {index + 1} از {total}", f"Page {index + 1} of {total}"))
            page = doc.load_page(index)
            pix = page.get_pixmap(dpi=int(v.get("dpi") or 150))
            name = f"{src.stem}_page_{index + 1:0{digits}d}.{'jpg' if fmt == 'jpg' else fmt}"
            dest = unique_path(target_dir / name)
            if fmt in ("png", "tiff"):
                pix.save(str(dest))
            else:
                Image = require("PIL.Image", "Pillow", ctx)
                mode = "RGBA" if pix.alpha else "RGB"
                image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                if fmt == "jpg":
                    image = image.convert("RGB")
                image.save(str(dest), quality=int(v.get("quality") or 90), optimize=True)
            outputs.append(str(dest))
        ctx.progress(total, total, "")
        return RunResult(
            message=fmt_out_message(ctx, len(outputs), str(target_dir)),
            outputs=outputs, open_target=str(target_dir),
        )
    finally:
        doc.close()


# ------------------------------------------------------------------ pdf → text
@tool(
    id="pdf_to_text", category="pdf", name="PDF به متن", name_en="PDF → Text",
    desc="متن قابل انتخاب PDF را با ترتیب درست فارسی (نیم‌فاصله، اعداد، جدول‌ها) در فایل TXT ذخیره می‌کند.",
    desc_en="Extract selectable text into TXT with correct Persian order, half-spaces and tables.",
    icon="text", order=20, keywords=("pdf to text", "استخراج متن", "متن پی دی اف", "فارسی"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("per_page", K_BOOL, "جدا کردن متن هر صفحه", "Separate text per page", default=True),
        Field("digits", K_CHOICE, "ارقام", "Digits", default="keep",
              choices=[("keep", "بدون تغییر", "Keep"), ("fa", "فارسی ۱۲۳", "Persian ۱۲۳"),
                       ("en", "لاتین 123", "Latin 123")]),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="Text (*.txt)"),
    ],
)
def pdf_to_text(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    doc = _open_doc(ctx, src)
    try:
        total = doc.page_count
        digits = str(v.get("digits") or "keep")
        chunks: List[str] = []
        empty = 0
        persian_pages = 0
        for i in range(total):
            ctx.progress(i, total, ctx.M(f"صفحه {i + 1} از {total}", f"Page {i + 1} of {total}"))
            try:
                content = persian_pdf.extract_page(doc, i, tables=True, images=False)
                text = content.text
                if content.rtl:
                    persian_pages += 1
            except Exception:
                text = persian_pdf.normalize_persian(doc[i].get_text("text"))
            if digits in ("fa", "en"):
                text = persian_pdf.normalize_persian(text, digits=digits, zwnj=False, fix_reversed=False)
            if not text.strip():
                empty += 1
            chunks.append(f"===== Page {i + 1} =====\n{text}" if v.get("per_page") else text)
    finally:
        doc.close()
    body = "\n\n".join(chunks)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.txt")
    dest.write_text(body, encoding="utf-8")
    note = ""
    if empty == total:
        note = ctx.M(
            "\n\nتوجه: این PDF متن قابل انتخاب ندارد (تصویری است)؛ برای استخراج متن به OCR نیاز است.",
            "\n\nNote: this PDF has no selectable text (scanned); OCR would be required.",
        )
    elif persian_pages:
        note = ctx.M(f"\n{persian_pages} صفحه‌ی فارسی/راست‌به‌چپ با ترتیب درست استخراج شد.",
                     f"\n{persian_pages} RTL page(s) extracted in logical order.")
    return RunResult(
        message=ctx.M(f"متن استخراج شد ({len(body):,} کاراکتر).{note}", f"Text extracted ({len(body):,} chars).{note}"),
        outputs=[str(dest)], open_target=str(dest), details=body[:6000],
    )


def _open_doc(ctx: RunContext, path: Path):
    """PyMuPDF document with repaired Persian font maps (raises a friendly error)."""
    _fitz(ctx)
    try:
        doc = persian_pdf.open_repaired(path)
    except Exception as exc:
        raise ToolError(ctx.M(
            f"این فایل PDF باز نشد (شاید خراب یا رمزدار است):\n{exc}",
            f"Could not open this PDF (damaged or encrypted?):\n{exc}",
        ))
    if doc.is_encrypted and doc.needs_pass:
        doc.close()
        raise ToolError(ctx.M(
            "این PDF با رمز محافظت شده است. ابتدا از ابزار «حذف رمز PDF» استفاده کنید.",
            "This PDF is password protected. Use 'Remove PDF password' first.",
        ))
    return doc


# ------------------------------------------------------------------ pdf → docx
@tool(
    id="pdf_to_docx", category="pdf", name="PDF به Word", name_en="PDF → Word",
    desc="PDF را به Word قابل ویرایش تبدیل می‌کند: پاراگراف‌های واقعی راست‌به‌چپ، تیترها، جدول‌ها، تصاویر و قلم فارسی.",
    desc_en="Turn a PDF into an editable Word file: real RTL paragraphs, headings, tables, images and Persian fonts.",
    icon="doc", order=30, keywords=("pdf to word", "pdf to docx", "پی دی اف به ورد", "فارسی", "راست به چپ"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="Word (*.docx)"),
        Field("page_break", K_BOOL, "شکستن صفحه در ابتدای هر صفحه", "Page break per PDF page", default=True),
        Field("images", K_BOOL, "انتقال تصاویر", "Carry images over", default=True),
        Field("tables", K_BOOL, "تشخیص جدول‌ها", "Detect tables", default=True),
        Field("font", K_CHOICE, "قلم فارسی", "Persian font", default="Vazirmatn",
              choices=[("Vazirmatn", "وزیرمتن (همراه برنامه)", "Vazirmatn (bundled)"),
                       ("B Nazanin", "B Nazanin", "B Nazanin"), ("IRANSans", "ایران‌سنس", "IRANSans"),
                       ("Tahoma", "Tahoma", "Tahoma")]),
    ],
)
def pdf_to_docx(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    docx = require("docx", "python-docx", ctx)
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
    from docx.shared import Inches, Pt  # type: ignore

    src = first_file(v, "src", ctx)
    doc = _open_doc(ctx, src)
    font = str(v.get("font") or persian_pdf.FONT_FAMILY)
    document = docx.Document()
    persian_pdf.docx_prepare(document, font=font, size_pt=11)
    section = document.sections[0]
    try:
        first = doc[0].rect
        if first.width > first.height:
            from docx.enum.section import WD_ORIENT  # type: ignore

            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = section.page_height, section.page_width
    except Exception:
        pass
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Inches(0.9))
    usable_width = section.page_width - section.left_margin - section.right_margin

    total = doc.page_count
    paragraphs = tables = images = 0
    rtl_pages = 0
    try:
        for i in range(total):
            ctx.progress(i, total, ctx.M(f"صفحه {i + 1} از {total}", f"Page {i + 1} of {total}"))
            if i and v.get("page_break", True):
                document.add_page_break()
            content = persian_pdf.extract_page(doc, i, tables=bool(v.get("tables", True)),
                                               images=bool(v.get("images", True)))
            if content.rtl:
                rtl_pages += 1
            scale = float(usable_width) / max(1.0, content.width - 2 * 36)
            for item in content.items:
                if isinstance(item, persian_pdf.Paragraph):
                    if not item.text.strip():
                        continue
                    if item.heading:
                        paragraph = document.add_heading("", level=min(3, item.heading))
                    elif item.bullet:
                        paragraph = document.add_paragraph(style="List Bullet")
                    else:
                        paragraph = document.add_paragraph()
                    for span in item.spans:
                        run = paragraph.add_run(span.text)
                        size = span.size if not item.heading else None
                        persian_pdf.docx_style_run(run, rtl=item.rtl, font=font,
                                                   size_pt=min(36.0, max(7.0, size)) if size else None,
                                                   bold=True if span.bold else None,
                                                   italic=True if span.italic else None,
                                                   color=span.color)
                    persian_pdf.docx_set_rtl(paragraph, item.rtl, font=font)
                    if item.align == "center":
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif item.align == "end":
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT if item.rtl else WD_ALIGN_PARAGRAPH.RIGHT
                    else:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if item.rtl else WD_ALIGN_PARAGRAPH.LEFT
                    paragraph.paragraph_format.space_after = Pt(6)
                    paragraphs += 1
                elif isinstance(item, persian_pdf.Table):
                    if not item.rows:
                        continue
                    cols = max(len(row) for row in item.rows)
                    table = document.add_table(rows=len(item.rows), cols=cols)
                    table.style = "Table Grid"
                    if item.rtl:
                        persian_pdf.docx_table_rtl(table)
                    for r_index, row in enumerate(item.rows):
                        for c_index in range(cols):
                            text = row[c_index] if c_index < len(row) else ""
                            cell = table.cell(r_index, c_index)
                            cell.text = ""
                            paragraph = cell.paragraphs[0]
                            run = paragraph.add_run(text)
                            cell_rtl = persian_pdf.is_rtl(text) or (item.rtl and not text.strip())
                            persian_pdf.docx_style_run(run, rtl=cell_rtl, font=font, size_pt=10,
                                                       bold=True if r_index == 0 else None)
                            persian_pdf.docx_set_rtl(paragraph, cell_rtl, font=font)
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if cell_rtl else WD_ALIGN_PARAGRAPH.LEFT
                    document.add_paragraph()
                    tables += 1
                elif isinstance(item, persian_pdf.ImageRef):
                    try:
                        width_pt = (item.bbox[2] - item.bbox[0]) * scale
                        width_emu = int(min(float(usable_width), max(72.0, width_pt) * 12700))
                        document.add_picture(io.BytesIO(item.data), width=width_emu)
                        document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        images += 1
                    except Exception as exc:
                        ctx.log(f"image skipped: {exc}")
    finally:
        doc.close()
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.docx")
    if paragraphs == 0 and tables == 0:
        paragraph = document.add_paragraph(ctx.M(
            "(این PDF متن قابل استخراج نداشت — احتمالاً اسکن است؛ از ابزار OCR استفاده کنید.)",
            "(This PDF had no extractable text — probably a scan; run the OCR tool.)"))
        persian_pdf.docx_set_rtl(paragraph, ctx.lang == "fa", font=font)
    document.save(str(dest))
    extras = []
    if tables:
        extras.append(ctx.M(f"{tables} جدول", f"{tables} table(s)"))
    if images:
        extras.append(ctx.M(f"{images} تصویر", f"{images} image(s)"))
    if rtl_pages:
        extras.append(ctx.M(f"{rtl_pages} صفحه‌ی راست‌به‌چپ", f"{rtl_pages} RTL page(s)"))
    suffix = ("، " if ctx.lang == "fa" else ", ").join(extras)
    return RunResult(
        message=ctx.M(
            f"فایل Word ساخته شد: {paragraphs} پاراگراف" + (f"، {suffix}" if suffix else "") + ".",
            f"Word file created: {paragraphs} paragraphs" + (f", {suffix}" if suffix else "") + ".",
        ),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------ images→pdf
@tool(
    id="images_to_pdf", category="pdf", name="تصویر به PDF", name_en="Images → PDF",
    desc="چند تصویر را در یک فایل PDF چندصفحه‌ای جمع می‌کند.",
    desc_en="Combine several images into one multi-page PDF.",
    icon="pdf", order=40, keywords=("image to pdf", "jpg to pdf", "عکس به پی دی اف", "png به pdf"),
    fields=[
        Field("src", K_FILES, "تصاویر (به ترتیب نام)", "Images (sorted by name)", required=True,
              filt=IMG_FILTER, multiple=True),
        Field("page", K_CHOICE, "اندازه‌ی صفحه", "Page size", default="auto",
              choices=[("auto", "به اندازه‌ی تصویر", "Image size"),
                       ("a4", "A4 (با حاشیه سفید)", "A4 (fit with margin)"),
                       ("letter", "Letter", "Letter")]),
        Field("fit", K_CHOICE, "تناسب تصویر با صفحه", "Image fit", default="contain",
              choices=[("contain", "کامل داخل صفحه", "Contain"), ("cover", "پر کردن صفحه", "Cover")]),
        Field("quality", K_INT, "کیفیت", "Quality", default=88, minimum=30, maximum=100),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def images_to_pdf(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = require("PIL.Image", "Pillow", ctx)
    files = files_or_raise(v, "src", ctx)
    total = len(files)
    size = str(v.get("page") or "auto")
    pages = []
    try:
        for index, path in enumerate(files):
            ctx.progress(index, total, ctx.M(f"تصویر {index + 1} از {total}", f"Image {index + 1} of {total}"))
            with Image.open(path) as im:
                im = im.convert("RGB")
                if size == "auto":
                    pages.append(im.copy())
                else:
                    base = (2480, 3508) if size == "a4" else (2550, 3300)  # ~300 DPI
                    page = Image.new("RGB", base, "white")
                    if v.get("fit") == "cover":
                        from PIL import ImageOps

                        page.paste(ImageOps.fit(im, base, method=Image.LANCZOS), (0, 0))
                    else:
                        copy = im.copy()
                        copy.thumbnail((int(base[0] * 0.94), int(base[1] * 0.94)), Image.LANCZOS)
                        page.paste(copy, ((base[0] - copy.width) // 2, (base[1] - copy.height) // 2))
                        copy.close()
                    pages.append(page)
        if not pages:
            raise ToolError(ctx.M("تصویری برای تبدیل نبود.", "No images to convert."))
        name = files[0].stem
        dest = save_target(ctx, v.get("out", ""), f"{name}_combined.pdf")
        pages[0].save(str(dest), "PDF", resolution=150.0, save_all=True, append_images=pages[1:],
                      quality=int(v.get("quality") or 88))
        ctx.progress(total, total, "")
        return RunResult(
            message=ctx.M(f"{len(pages)} صفحه در یک PDF ذخیره شد.", f"{len(pages)} pages saved into one PDF."),
            outputs=[str(dest)], open_target=str(dest),
        )
    finally:
        for page in pages:
            try:
                page.close()
            except Exception:
                pass


# --------------------------------------------------------------------- merge
@tool(
    id="pdf_merge", category="pdf", name="ادغام PDF", name_en="Merge PDFs",
    desc="چند فایل PDF را به ترتیب دلخواه در یک فایل ترکیب می‌کند.",
    desc_en="Combine multiple PDFs into a single file.",
    icon="zip", order=50, keywords=("merge pdf", "join pdf", "ادغام", "ترکیب پی دی اف"),
    fields=[
        Field("src", K_FILES, "فایل‌های PDF", "PDF files", required=True, filt=PDF_FILTER, multiple=True),
        Field("sort", K_CHOICE, "ترتیب", "Order", default="name",
              choices=[("name", "الفبایی", "Alphabetical"), ("select", "ترتیب انتخاب", "Selection order")]),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_merge(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    files = files_or_raise(v, "src", ctx)
    if v.get("sort", "name") == "name":
        files.sort(key=lambda p: p.name.lower())
    writer = pypdf.PdfWriter()
    total = len(files)
    for index, path in enumerate(files):
        ctx.progress(index, total, ctx.M(f"افزودن {path.name}", f"Adding {path.name}"))
        reader = _open_reader(ctx, path)
        for page in reader.pages:
            writer.add_page(page)
    dest = save_target(ctx, v.get("out", ""), "merged.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"{len(files)} فایل و {len(writer.pages)} صفحه ادغام شد.", f"{len(files)} files, {len(writer.pages)} pages merged."),
        outputs=[str(dest)], open_target=str(dest),
    )


# --------------------------------------------------------------------- split
@tool(
    id="pdf_split", category="pdf", name="تفکیک PDF", name_en="Split PDF",
    desc="PDF را به صفحه‌های جدا، بازه‌های دلخواه یا بخش‌های N صفحه‌ای تقسیم می‌کند.",
    desc_en="Split a PDF into single pages, ranges or N-page chunks.",
    icon="crop", order=60, keywords=("split pdf", "تفکیک", "جدا کردن صفحات"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("mode", K_CHOICE, "حالت", "Mode", default="each",
              choices=[("each", "هر صفحه جدا", "One file per page"),
                       ("ranges", "بازه‌های دلخواه", "Custom ranges"),
                       ("chunks", "بخش‌های N صفحه‌ای", "N-page chunks")]),
        Field("ranges", K_TEXT, "بازه‌ها", "Ranges", default="1-3, 4-8",
              help="مثال: 1-3, 5, 9-  (هر بازه یک فایل)", help_en="e.g. 1-3, 5, 9-",
              visible_when=("mode", "ranges")),
        Field("chunk", K_INT, "تعداد صفحه در هر بخش", "Pages per chunk", default=10, minimum=1, maximum=500,
              visible_when=("mode", "chunks")),
        Field("out", K_DIR, "پوشه‌ی خروجی", "Output folder", required=False),
    ],
)
def pdf_split(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total_pages = len(reader.pages)
    if total_pages == 0:
        raise ToolError(ctx.M("این PDF صفحه‌ای ندارد.", "This PDF has no pages."))
    target_dir = ensure_dir(v.get("out") or out_dir(ctx) / f"{src.stem}_split")
    groups: List[List[int]] = []
    mode = v.get("mode") or "each"
    if mode == "each":
        groups = [[i] for i in range(total_pages)]
    elif mode == "ranges":
        text = str(v.get("ranges") or "").strip()
        if not text:
            raise ToolError(ctx.M("بازه‌ها را وارد کنید، مثلاً 1-3, 4-8", "Enter ranges, e.g. 1-3, 4-8"))
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            groups.append(page_ranges(ctx, part, total_pages))
    else:
        step = max(1, int(v.get("chunk") or 10))
        groups = [list(range(i, min(i + step, total_pages))) for i in range(0, total_pages, step)]
    outputs: List[str] = []
    digits = max(2, len(str(total_pages)))
    count = len(groups)
    for index, pages in enumerate(groups):
        if not pages:
            continue
        ctx.progress(index, count, ctx.M(f"فایل {index + 1} از {count}", f"File {index + 1} of {count}"))
        writer = pypdf.PdfWriter()
        for p in pages:
            writer.add_page(reader.pages[p])
        label = f"{pages[0] + 1:0{digits}d}" if len(pages) == 1 else f"{pages[0] + 1:0{digits}d}-{pages[-1] + 1:0{digits}d}"
        dest = unique_path(target_dir / f"{src.stem}_p{label}.pdf")
        with open(dest, "wb") as fh:
            writer.write(fh)
        outputs.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(outputs), str(target_dir)),
                     outputs=outputs, open_target=str(target_dir))


# -------------------------------------------------------------------- rotate
@tool(
    id="pdf_rotate", category="pdf", name="چرخش PDF", name_en="Rotate PDF",
    desc="صفحه‌های PDF را ۹۰/۱۸۰/۲۷۰ درجه می‌چرخاند.",
    desc_en="Rotate PDF pages by 90/180/270 degrees.",
    icon="convert", order=70, keywords=("rotate pdf", "چرخش", "برگرداندن"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("angle", K_CHOICE, "زاویه", "Angle", default=90,
              choices=[(90, "۹۰ درجه", "90°"), (180, "۱۸۰ درجه", "180°"), (270, "۲۷۰ درجه", "270°")]),
        Field("pages", K_TEXT, "صفحه‌ها", "Pages", default="", placeholder="خالی = همه صفحه‌ها",
              placeholder_en="empty = all pages"),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_rotate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total = len(reader.pages)
    targets = set(page_ranges(ctx, str(v.get("pages") or ""), total))
    angle = int(v.get("angle") or 90)
    for index, page in enumerate(reader.pages):
        if index in targets:
            page.rotate(angle)
        ctx.progress(index, total, "")
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_rotated.pdf")
    if same_file(dest, src):
        raise ToolError(ctx.M(
            "مسیر خروجی با فایل PDF ورودی یکی است؛ فایل اصلی بازنویسی نمی‌شود.",
            "The output path is the same file as the source PDF; the original is never overwritten.",
        ))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    out_buffer = io.BytesIO()
    writer.write(out_buffer)
    atomic_write_bytes(dest, out_buffer.getvalue())
    return RunResult(
        message=ctx.M(f"{len(targets)} صفحه چرخانده شد.", f"{len(targets)} page(s) rotated."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------ compress
@tool(
    id="pdf_compress", category="pdf", name="فشرده‌سازی PDF", name_en="Compress PDF",
    desc="حجم PDF را با فشرده‌سازی ساختار (و در حالت قوی با تبدیل به تصویر) کم می‌کند.",
    desc_en="Shrink a PDF by optimizing structure (or rasterizing in strong mode).",
    icon="zip", order=80, keywords=("compress pdf", "کم کردن حجم", "فشرده"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("mode", K_CHOICE, "حالت", "Mode", default="light",
              choices=[("light", "سبک (متن حفظ می‌شود)", "Light (keeps text)"),
                       ("strong", "قوی (تبدیل به تصویر)", "Strong (rasterize)")]),
        Field("dpi", K_INT, "کیفیت در حالت قوی (DPI)", "Strong-mode DPI", default=110,
              minimum=72, maximum=300, step=10, visible_when=("mode", "strong")),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_compress(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    before = src.stat().st_size
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_compressed.pdf")
    mode = v.get("mode") or "light"
    if mode == "light":
        fitz = _fitz(ctx)
        doc = fitz.open(str(src))
        try:
            ctx.progress(1, 3, ctx.M("بازنویسی فایل…", "Rewriting file…"))
            doc.save(str(dest), garbage=4, deflate=True, deflate_images=True,
                     deflate_fonts=True, clean=True)
        finally:
            doc.close()
    else:
        fitz = _fitz(ctx)
        Image = require("PIL.Image", "Pillow", ctx)
        doc = fitz.open(str(src))
        images = []
        try:
            total = doc.page_count
            for index in range(total):
                ctx.progress(index, total, ctx.M(f"صفحه {index + 1} از {total}", f"Page {index + 1} of {total}"))
                pix = doc.load_page(index).get_pixmap(dpi=int(v.get("dpi") or 110))
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                images.append(image)
            if not images:
                raise ToolError(ctx.M("صفحه‌ای برای فشرده‌سازی نبود.", "Nothing to compress."))
            images[0].save(str(dest), "PDF", save_all=True, append_images=images[1:],
                           quality=72, optimize=True, resolution=float(v.get("dpi") or 110))
        finally:
            doc.close()
            for image in images:
                try:
                    image.close()
                except Exception:
                    pass
    after = dest.stat().st_size
    saved = 100.0 * (1 - after / before) if before else 0
    note = "" if saved > 0 else ctx.M(
        "\n(فایل از قبل بهینه بود؛ حجم بیشتر نشد که مشکلی نیست.)",
        "\n(File was already optimal.)",
    )
    if saved >= 0.5:
        headline = ctx.M(
            f"حجم از {human_size(before)} به {human_size(after)} رسید ({saved:.1f}٪ کاهش).{note}",
            f"Size: {human_size(before)} → {human_size(after)} ({saved:.1f}% smaller).{note}")
    else:
        headline = ctx.M(
            f"فشرده‌سازی انجام شد ({human_size(before)} → {human_size(after)})؛ این فایل از قبل بهینه بوده است.",
            f"Compressed ({human_size(before)} → {human_size(after)}); the file was already optimal.")
    return RunResult(
        message=headline,
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------- encrypt/decrypt
@tool(
    id="pdf_encrypt", category="pdf", name="رمزگذاری PDF", name_en="Protect PDF",
    desc="برای PDF رمز عبور می‌گذارد (AES-256) تا فقط با رمز باز شود.",
    desc_en="Password-protect a PDF with AES-256 encryption.",
    icon="lock", order=90, keywords=("encrypt pdf", "قفل پی دی اف", "پسورد پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("password", K_PASSWORD, "رمز عبور", "Password", required=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_encrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    src = first_file(v, "src", ctx)
    password = str(v.get("password") or "")
    if len(password) < 3:
        raise ToolError(ctx.M("رمز باید حداقل ۳ کاراکتر باشد.", "Password must be at least 3 characters."))
    reader = _open_reader(ctx, src)
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    try:
        writer.encrypt(password, algorithm="AES-256")
    except TypeError:
        writer.encrypt(password)  # older pypdf
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_protected.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M("PDF با موفقیت رمزگذاری شد (AES-256).", "PDF encrypted successfully (AES-256)."),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="pdf_decrypt", category="pdf", name="حذف رمز PDF", name_en="Remove PDF password",
    desc="رمز PDF را حذف می‌کند (رمز فعلی را لازم دارد).",
    desc_en="Remove PDF protection (requires the current password).",
    icon="key", order=100, keywords=("decrypt pdf", "باز کردن قفل", "حذف پسورد"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("password", K_PASSWORD, "رمز فعلی", "Current password", required=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_decrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    src = first_file(v, "src", ctx)
    try:
        reader = pypdf.PdfReader(str(src))
    except Exception as exc:
        raise ToolError(str(exc))
    if reader.is_encrypted:
        try:
            ok = reader.decrypt(str(v.get("password") or ""))
        except Exception as exc:
            raise ToolError(ctx.M(f"رمز اشتباه است: {exc}", f"Wrong password: {exc}"))
        if not ok:
            raise ToolError(ctx.M("رمز عبور اشتباه است.", "Wrong password."))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_unlocked.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M("قفل PDF برداشته شد.", "Password protection removed."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------ watermark
@tool(
    id="pdf_watermark", category="pdf", name="واترمارک PDF", name_en="Watermark PDF",
    desc="یک متن کج و کم‌رنگ روی همه‌ی صفحه‌ها می‌گذارد.",
    desc_en="Stamp a diagonal text watermark on every page.",
    icon="note", order=110, keywords=("watermark pdf", "واترمارک", "علامت"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("text", K_TEXT, "متن واترمارک", "Watermark text", required=True, default="CONFIDENTIAL"),
        Field("size", K_INT, "اندازه‌ی فونت", "Font size", default=54, minimum=8, maximum=200),
        Field("opacity", K_FLOAT, "شفافیت", "Opacity", default=0.14, minimum=0.02, maximum=1.0,
              step=0.02, decimals=2),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_watermark(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    doc = _open_doc(ctx, src)
    text = str(v.get("text") or "CONFIDENTIAL")
    size = int(v.get("size") or 54)
    opacity = float(v.get("opacity") or 0.14)
    try:
        total = doc.page_count
        for index in range(total):
            ctx.progress(index, total, ctx.M(f"صفحه {index + 1} از {total}", f"Page {index + 1} of {total}"))
            page = doc[index]
            try:
                persian_pdf.stamp_text(page, text, size_pt=size, opacity=opacity, angle=45.0)
            except Exception as exc:
                raise ToolError(ctx.M(f"واترمارک روی صفحه {index + 1} نوشته نشد: {exc}",
                                      f"Could not stamp page {index + 1}: {exc}"))
        dest = save_target(ctx, v.get("out", ""), f"{src.stem}_watermarked.pdf")
        persian_pdf.save_pdf(doc, dest)
    finally:
        doc.close()
    return RunResult(
        message=ctx.M(f"واترمارک روی {total} صفحه اعمال شد.", f"Watermark applied to {total} pages."),
        outputs=[str(dest)], open_target=str(dest),
    )


# --------------------------------------------------------------- extract pages
@tool(
    id="pdf_extract_pages", category="pdf", name="استخراج صفحه‌ها", name_en="Extract pages",
    desc="صفحه‌های انتخابی را در یک PDF جدید جدا می‌کند.",
    desc_en="Pull selected pages into a new PDF.",
    icon="crop", order=120, keywords=("extract pages", "جدا کردن صفحه", "صفحه خاص"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("pages", K_TEXT, "صفحه‌ها", "Pages", required=True, default="1-3",
              help="مثال: 1-3, 7, 10-  (شماره‌گذاری از ۱)", help_en="e.g. 1-3, 7, 10- (1-based)"),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_extract_pages(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total = len(reader.pages)
    indices = page_ranges(ctx, str(v.get("pages") or ""), total)
    if not indices:
        raise ToolError(ctx.M("هیچ صفحه‌ای انتخاب نشد.", "No pages selected."))
    writer = pypdf.PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_pages.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
    return RunResult(
        message=ctx.M(f"{len(indices)} صفحه استخراج شد.", f"{len(indices)} page(s) extracted."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------- pdf info
@tool(
    id="pdf_info", category="pdf", name="اطلاعات PDF", name_en="PDF info",
    desc="تعداد صفحه، اندازه، متادیتا و رمزدار بودن فایل را نشان می‌دهد.",
    desc_en="Show page count, size, metadata and encryption state.",
    icon="list", order=130, keywords=("pdf info", "مشخصات پی دی اف", "تعداد صفحات"),
    fields=[Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER)],
)
def pdf_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    rows = [
        (ctx.M("نام فایل", "File"), src.name),
        (ctx.M("حجم", "Size"), human_size(src.stat().st_size)),
        (ctx.M("تعداد صفحه", "Pages"), str(len(reader.pages))),
        (ctx.M("رمزدار", "Encrypted"), ctx.M("بله", "Yes") if reader.is_encrypted else ctx.M("خیر", "No")),
    ]
    info = reader.metadata or {}
    for key, value in dict(info).items():
        if value:
            rows.append((str(key).lstrip("/"), str(value)))
    try:
        box = reader.pages[0].mediabox
        rows.append((
            ctx.M("اندازه‌ی صفحه‌ی اول", "First page size"),
            f"{float(box.width):.0f} × {float(box.height):.0f} pt",
        ))
    except Exception:
        pass
    details = "\n".join(f"{k}: {val}" for k, val in rows)
    return RunResult(
        message=ctx.M(f"{len(reader.pages)} صفحه، {human_size(src.stat().st_size)}",
                      f"{len(reader.pages)} pages, {human_size(src.stat().st_size)}"),
        details=details, outputs=[str(src)],
    )


# -------------------------------------------------------------- page numbers
@tool(
    id="pdf_page_numbers", category="pdf", name="شماره‌گذاری صفحه", name_en="Add page numbers",
    desc="پایین یا بالای هر صفحه شماره‌ی صفحه (لاتین یا فارسی) اضافه می‌کند.",
    desc_en="Stamp page numbers (Latin or Persian digits) at the bottom or top of each page.",
    icon="hash", order=140, keywords=("page numbers", "شماره صفحه", "صفحه بندی"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("start", K_INT, "شروع از شماره", "Start at", default=1, minimum=1, maximum=100000),
        Field("position", K_CHOICE, "موقعیت", "Position", default="bottom-center",
              choices=[("bottom-center", "پایین وسط", "Bottom centre"), ("bottom-right", "پایین راست", "Bottom right"),
                       ("bottom-left", "پایین چپ", "Bottom left"), ("top-center", "بالا وسط", "Top centre")]),
        Field("style", K_CHOICE, "قالب", "Format", default="n_of_total",
              choices=[("n_of_total", "۳ / ۱۲", "3 / 12"), ("n", "فقط شماره", "Number only"),
                       ("page_n", "صفحه ۳", "Page 3")]),
        Field("digits", K_CHOICE, "ارقام", "Digits", default="auto",
              choices=[("auto", "خودکار (فارسی برای سند فارسی)", "Auto (Persian for Persian docs)"),
                       ("en", "لاتین 123", "Latin 123"), ("fa", "فارسی ۱۲۳", "Persian ۱۲۳")]),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_page_numbers(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    doc = _open_doc(ctx, src)
    start = int(v.get("start") or 1)
    position = str(v.get("position") or "bottom-center")
    style = str(v.get("style") or "n_of_total")
    digits = str(v.get("digits") or "auto")
    try:
        total = doc.page_count
        if digits == "auto":
            sample = " ".join(doc[i].get_text("text")[:2000] for i in range(min(3, total)))
            digits = "fa" if persian_pdf.is_rtl(sample) else "en"
        for index in range(total):
            ctx.progress(index, total, "")
            number, last = start + index, start + total - 1
            if style == "n":
                label = f"{number}"
            elif style == "page_n":
                label = f"صفحه {number}" if digits == "fa" else f"Page {number}"
            else:
                label = f"{number} / {last}"
            if digits == "fa":
                label = persian_pdf.to_persian_digits(label)
            persian_pdf.stamp_line(doc[index], label, position=position, size_pt=10)
        dest = save_target(ctx, v.get("out", ""), f"{src.stem}_numbered.pdf")
        persian_pdf.save_pdf(doc, dest)
    finally:
        doc.close()
    return RunResult(
        message=ctx.M("شماره‌گذاری انجام شد.", "Page numbers added."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ----------------------------------------------------------- office → pdf (com)
@tool(
    id="docx_to_pdf", category="pdf", name="Word/Office به PDF", name_en="Word/Office → PDF",
    desc="فایل Word، Excel یا PowerPoint را با Microsoft Office یا LibreOffice به PDF تبدیل می‌کند.",
    desc_en="Convert Word/Excel/PowerPoint to PDF using MS Office or LibreOffice.",
    icon="doc", order=150, keywords=("word to pdf", "docx to pdf", "excel to pdf", "ورد به پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل آفیس", "Office file", required=True,
              filt="Office (*.docx *.doc *.xlsx *.xls *.pptx *.ppt *.odt);;همه فایل‌ها (*)"),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def docx_to_pdf(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.pdf")
    ctx.progress(1, 3, ctx.M("در حال تبدیل…", "Converting…"))

    if src.suffix.lower() in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"):
        try:
            import win32com.client  # type: ignore

            app_map = {
                ".docx": ("Word.Application", 17), ".doc": ("Word.Application", 17),
                ".xlsx": ("Excel.Application", 0), ".xls": ("Excel.Application", 0),
                ".pptx": ("PowerPoint.Application", 32), ".ppt": ("PowerPoint.Application", 32),
            }
            prog_id, fmt = app_map[src.suffix.lower()]
            app = win32com.client.Dispatch(prog_id)
            try:
                if prog_id == "Word.Application":
                    app.Visible = False
                    doc = app.Documents.Open(str(src.resolve()))
                    doc.SaveAs(str(dest.resolve()), FileFormat=fmt)
                    doc.Close(False)
                elif prog_id == "Excel.Application":
                    app.Visible = False
                    wb = app.Workbooks.Open(str(src.resolve()))
                    wb.ExportAsFixedFormat(0, str(dest.resolve()))
                    wb.Close(False)
                else:
                    pres = app.Presentations.Open(str(src.resolve()), WithWindow=False)
                    pres.SaveAs(str(dest.resolve()), 32)
                    pres.Close()
            finally:
                try:
                    app.Quit()
                except Exception:
                    pass
            if dest.exists():
                return RunResult(message=ctx.M("تبدیل با Microsoft Office انجام شد.", "Converted with Microsoft Office."),
                                 outputs=[str(dest)], open_target=str(dest))
        except Exception as exc:
            ctx.log(f"MS Office conversion failed: {exc}")

    soffice = None
    for name in ("soffice", "libreoffice"):
        import shutil

        soffice = shutil.which(name)
        if soffice:
            break
    if not soffice:
        if src.suffix.lower() in (".docx", ".xlsx", ".csv", ".txt", ".md"):
            ctx.progress(2, 3, ctx.M("تبدیل داخلی (بدون آفیس)…", "Built-in conversion (no Office)…"))
            return _office_to_pdf_builtin(ctx, src, dest)
        raise ToolError(ctx.M(
            "برای تبدیل این نوع فایل به PDF یا Microsoft Office نصب باشد یا LibreOffice.\n"
            "راه سریع: فایل را در Word باز کنید و Save as PDF بزنید.",
            "Install Microsoft Office or LibreOffice to convert this file type.\n"
            "Quick fix: open the file in Word and use 'Save as PDF'.",
        ))
    ctx.progress(2, 3, ctx.M("اجرای LibreOffice…", "Running LibreOffice…"))
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            capture_output=True, text=True, timeout=180,
        )
        produced = Path(tmp) / f"{src.stem}.pdf"
        if not produced.exists():
            raise ToolError(ctx.M(f"تبدیل ناموفق بود:\n{proc.stderr or proc.stdout}",
                                  f"Conversion failed:\n{proc.stderr or proc.stdout}"))
        dest.write_bytes(produced.read_bytes())
    return RunResult(message=ctx.M("تبدیل با LibreOffice انجام شد.", "Converted with LibreOffice."),
                     outputs=[str(dest)], open_target=str(dest))


def _docx_to_html(ctx: RunContext, src: Path) -> str:
    """python-docx → simple HTML (headings, runs, lists, tables, direction)."""
    docx = require("docx", "python-docx", ctx)
    from docx.oxml.ns import qn  # type: ignore

    document = docx.Document(str(src))
    esc = persian_pdf.html_escape
    parts: List[str] = []

    def _para_html(paragraph, tag_override: str = "") -> str:
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        text = paragraph.text
        rtl = persian_pdf.is_rtl(text) or paragraph._p.pPr is not None and paragraph._p.pPr.find(qn("w:bidi")) is not None
        runs = []
        for run in paragraph.runs:
            chunk = esc(run.text).replace("\n", "<br/>")
            if run.bold:
                chunk = f"<b>{chunk}</b>"
            if run.italic:
                chunk = f"<i>{chunk}</i>"
            if run.underline:
                chunk = f"<u>{chunk}</u>"
            runs.append(chunk)
        body = "".join(runs) or esc(text)
        if not body.strip():
            return ""
        tag = tag_override or "p"
        if style.startswith("Heading") or style == "Title":
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            tag = f"h{min(3, int(level))}"
        if "List" in style and not tag_override:
            body = "• " + body
        align = "right" if rtl else "left"
        try:
            from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore

            if paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
                align = "center"
            elif paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
                align = "justify"
        except Exception:
            pass
        return f'<{tag} dir="{"rtl" if rtl else "ltr"}" style="text-align:{align}">{body}</{tag}>'

    body_el = document.element.body
    for child in body_el.iterchildren():
        if child.tag == qn("w:p"):
            from docx.text.paragraph import Paragraph as DocxParagraph  # type: ignore

            parts.append(_para_html(DocxParagraph(child, document)))
        elif child.tag == qn("w:tbl"):
            from docx.table import Table as DocxTable  # type: ignore

            table = DocxTable(child, document)
            rows_html = []
            sample = " ".join(cell.text for row in table.rows[:2] for cell in row.cells)
            rtl = persian_pdf.is_rtl(sample)
            for row in table.rows:
                cells = [cell for cell in row.cells]
                if rtl:
                    cells = list(reversed(cells))  # MuPDF lays tables out LTR; mirror for RTL
                cells_html = "".join(
                    f'<td dir="{"rtl" if persian_pdf.is_rtl(cell.text) else "ltr"}" '
                    f'style="text-align:{"right" if persian_pdf.is_rtl(cell.text) else "left"}">'
                    f'{esc(cell.text).replace(chr(10), "<br/>")}</td>' for cell in cells)
                rows_html.append(f"<tr>{cells_html}</tr>")
            parts.append(f'<table dir="{"rtl" if rtl else "ltr"}">{"".join(rows_html)}</table><p></p>')
    return "".join(parts) or "<p>&nbsp;</p>"


def _sheet_to_html(ctx: RunContext, src: Path) -> str:
    esc = persian_pdf.html_escape
    rows: List[List[str]] = []
    if src.suffix.lower() == ".csv":
        import csv

        with open(src, encoding="utf-8-sig", errors="replace", newline="") as fh:
            rows = [[str(cell) for cell in row] for row in csv.reader(fh)]
        sheets = [(src.stem, rows)]
    else:
        openpyxl = require("openpyxl", "openpyxl", ctx)
        workbook = openpyxl.load_workbook(str(src), read_only=True, data_only=True)
        sheets = []
        for sheet in workbook.worksheets:
            data = [["" if cell is None else str(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
            sheets.append((sheet.title, data[:2000]))
    parts = []
    for title, data in sheets:
        data = [row for row in data if any(cell.strip() for cell in row)]
        if not data:
            continue
        rtl = persian_pdf.is_rtl(" ".join(" ".join(row) for row in data[:5]))
        parts.append(f'<h2 dir="{"rtl" if rtl else "ltr"}" style="text-align:{"right" if rtl else "left"}">{esc(title)}</h2>')
        body = []
        for r_index, row in enumerate(data):
            cells = list(reversed(row)) if rtl else row
            tag = "th" if r_index == 0 else "td"
            body.append("<tr>" + "".join(
                f'<{tag} dir="{"rtl" if persian_pdf.is_rtl(c) else "ltr"}" '
                f'style="text-align:{"right" if persian_pdf.is_rtl(c) else "left"}">{esc(c)}</{tag}>' for c in cells) + "</tr>")
        parts.append(f"<table>{''.join(body)}</table><p></p>")
    return "".join(parts) or "<p>&nbsp;</p>"


def _office_to_pdf_builtin(ctx: RunContext, src: Path, dest: Path) -> RunResult:
    """No Office / LibreOffice: render Word, Excel, CSV, text and Markdown through MuPDF."""
    fitz = _fitz(ctx)
    suffix = src.suffix.lower()
    if suffix == ".docx":
        html = _docx_to_html(ctx, src)
    elif suffix in (".xlsx", ".csv"):
        html = _sheet_to_html(ctx, src)
    elif suffix == ".md":
        try:
            import markdown as md_mod  # type: ignore

            html = md_mod.markdown(src.read_text(encoding="utf-8", errors="replace"), extensions=["tables", "fenced_code"])
            html = persian_pdf.direction_html(html)
        except Exception:
            html = persian_pdf.text_to_html(src.read_text(encoding="utf-8", errors="replace"))
    else:
        html = persian_pdf.text_to_html(src.read_text(encoding="utf-8", errors="replace"))
    doc = fitz.open()
    try:
        pages = persian_pdf.flow_html_pages(doc, html, size_pt=11)
        doc.set_metadata({"title": src.stem, "producer": "WinGenius", "creator": "WinGenius"})
        persian_pdf.save_pdf(doc, dest)
    finally:
        doc.close()
    return RunResult(
        message=ctx.M(f"تبدیل داخلی انجام شد ({pages} صفحه). برای حفظ دقیق چیدمان اصلی، Word یا LibreOffice نصب کنید.",
                      f"Built-in conversion done ({pages} page(s)). Install Word or LibreOffice for pixel-exact layout."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------ pdf tools done
TOOL_COUNT = 13
