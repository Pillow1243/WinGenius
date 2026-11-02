"""PDF toolkit: convert, merge, split, rotate, compress, protect, watermark."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_PASSWORD, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, parse_ranges, unique_path
from ._helpers import (
    ensure_dir, files_or_raise, first_file, fmt_out_message, need_windows, out_dir,
    require, save_target,
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
    desc="متن قابل انتخاب PDF را در فایل TXT ذخیره می‌کند.",
    desc_en="Extract selectable text into a TXT file.",
    icon="text", order=20, keywords=("pdf to text", "استخراج متن", "متن پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("per_page", K_BOOL, "جدا کردن متن هر صفحه", "Separate text per page", default=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="Text (*.txt)"),
    ],
)
def pdf_to_text(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    total = len(reader.pages)
    chunks: List[str] = []
    empty = 0
    for i, page in enumerate(reader.pages):
        ctx.progress(i, total, ctx.M(f"صفحه {i + 1} از {total}", f"Page {i + 1} of {total}"))
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            empty += 1
        chunks.append(f"===== Page {i + 1} =====\n{text}" if v.get("per_page") else text)
    body = "\n\n".join(chunks)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.txt")
    dest.write_text(body, encoding="utf-8")
    note = ""
    if empty == total:
        note = ctx.M(
            "\n\nتوجه: این PDF متن قابل انتخاب ندارد (تصویری است)؛ برای استخراج متن به OCR نیاز است.",
            "\n\nNote: this PDF has no selectable text (scanned); OCR would be required.",
        )
    return RunResult(
        message=ctx.M(f"متن استخراج شد ({len(body):,} کاراکتر).{note}", f"Text extracted ({len(body):,} chars).{note}"),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------ pdf → docx
@tool(
    id="pdf_to_docx", category="pdf", name="PDF به Word", name_en="PDF → Word",
    desc="متن PDF را به یک فایل قابل ویرایش Word (.docx) تبدیل می‌کند.",
    desc_en="Turn PDF text into an editable Word (.docx) document.",
    icon="doc", order=30, keywords=("pdf to word", "pdf to docx", "پی دی اف به ورد"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="Word (*.docx)"),
        Field("page_break", K_BOOL, "شکستن صفحه در ابتدای هر صفحه", "Page break per PDF page", default=True),
    ],
)
def pdf_to_docx(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    docx = require("docx", "python-docx", ctx)
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    document = docx.Document()
    document.add_heading(src.stem, level=1)
    total = len(reader.pages)
    added = 0
    for i, page in enumerate(reader.pages):
        ctx.progress(i, total, ctx.M(f"صفحه {i + 1} از {total}", f"Page {i + 1} of {total}"))
        if i and v.get("page_break"):
            document.add_page_break()
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        for block in text.split("\n"):
            if block.strip():
                document.add_paragraph(block.strip())
                added += 1
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.docx")
    if added == 0:
        document.add_paragraph(ctx.M(
            "(این PDF متن قابل استخراج نداشت.)", "(This PDF had no extractable text.)"
        ))
    document.save(str(dest))
    return RunResult(
        message=ctx.M(
            f"فایل Word ساخته شد با {added} پاراگراف.\nتوجه: چیدمان و تصاویر PDF منتقل نمی‌شوند (تبدیل متنی).",
            f"Word file created with {added} paragraphs.\nNote: layout and images are not carried over (text conversion).",
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
            groups.append(parse_ranges(part, total_pages))
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
    try:
        targets = set(parse_ranges(str(v.get("pages") or ""), total))
    except ValueError as exc:
        raise ToolError(ctx.M(f"بازه‌ی صفحه‌ها نامعتبر است: {exc}", f"Invalid page range: {exc}"))
    angle = int(v.get("angle") or 90)
    for index, page in enumerate(reader.pages):
        if index in targets:
            page.rotate(angle)
        ctx.progress(index, total, "")
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_rotated.pdf")
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with open(dest, "wb") as fh:
        writer.write(fh)
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
    pypdf = _pypdf(ctx)
    Canvas = require("reportlab.pdfgen.canvas", "reportlab", ctx).Canvas
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    writer = pypdf.PdfWriter()
    text = str(v.get("text") or "CONFIDENTIAL")
    total = len(reader.pages)
    cache: Dict[tuple, Any] = {}
    for index, page in enumerate(reader.pages):
        ctx.progress(index, total, ctx.M(f"صفحه {index + 1} از {total}", f"Page {index + 1} of {total}"))
        try:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
        except Exception:
            width, height = 595.0, 842.0
        key = (round(width, 1), round(height, 1))
        if key not in cache:
            buffer = io.BytesIO()
            c = Canvas(buffer, pagesize=(width, height))
            c.setFillColorRGB(0.55, 0.55, 0.60)
            try:
                c.setFillAlpha(float(v.get("opacity") or 0.14))
            except Exception:
                pass
            c.setFont("Helvetica-Bold", int(v.get("size") or 54))
            c.saveState()
            c.translate(width / 2, height / 2)
            c.rotate(45)
            c.drawCentredString(0, 0, text)
            c.restoreState()
            c.save()
            buffer.seek(0)
            cache[key] = pypdf.PdfReader(buffer).pages[0]
        page.merge_page(cache[key])
        writer.add_page(page)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_watermarked.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
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
    try:
        indices = parse_ranges(str(v.get("pages") or ""), total)
    except ValueError as exc:
        raise ToolError(ctx.M(f"بازه نامعتبر است: {exc}", f"Invalid range: {exc}"))
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
    desc="پایین هر صفحه شماره‌ی صفحه اضافه می‌کند.",
    desc_en="Stamp page numbers at the bottom of each page.",
    icon="hash", order=140, keywords=("page numbers", "شماره صفحه", "صفحه بندی"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("start", K_INT, "شروع از شماره", "Start at", default=1, minimum=1, maximum=100000),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_page_numbers(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pypdf = _pypdf(ctx)
    Canvas = require("reportlab.pdfgen.canvas", "reportlab", ctx).Canvas
    src = first_file(v, "src", ctx)
    reader = _open_reader(ctx, src)
    writer = pypdf.PdfWriter()
    total = len(reader.pages)
    start = int(v.get("start") or 1)
    for index, page in enumerate(reader.pages):
        ctx.progress(index, total, "")
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        buffer = io.BytesIO()
        c = Canvas(buffer, pagesize=(width, height))
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.35, 0.35, 0.4)
        c.drawCentredString(width / 2, max(14, height * 0.03), f"{start + index} / {start + total - 1}")
        c.save()
        buffer.seek(0)
        page.merge_page(pypdf.PdfReader(buffer).pages[0])
        writer.add_page(page)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_numbered.pdf")
    with open(dest, "wb") as fh:
        writer.write(fh)
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
        raise ToolError(ctx.M(
            "برای تبدیل فایل‌های آفیس به PDF یا Microsoft Office نصب باشد یا LibreOffice.\n"
            "راه سریع: فایل را در Word باز کنید و Save as PDF بزنید.",
            "Install Microsoft Office or LibreOffice to convert Office files.\n"
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


# ------------------------------------------------------------------ pdf tools done
TOOL_COUNT = 13
