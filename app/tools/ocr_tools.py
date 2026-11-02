"""OCR and codes: text from images/PDFs, barcode generation, QR decoding."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_COLOR, K_DIR, K_FILE, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import ensure_dir, first_file, out_dir, require, save_target

IMG_FILTER = "تصاویر (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;همه فایل‌ها (*)"
IS_WINDOWS = sys.platform.startswith("win")

OCR_HINT_FA = (
    "برای خواندن متن از تصویر دو چیز لازم است:\n"
    "۱) برنامه‌ی Tesseract OCR را نصب کنید (ویندوز: winget install UB-Mannheim.TesseractOCR)\n"
    "۲) بسته‌ی پایتونی را نصب کنید: pip install pytesseract\n\n"
    "سپس دوباره تلاش کنید."
)
OCR_HINT_EN = (
    "Reading text from images needs two things:\n"
    "1) Install the Tesseract OCR engine (Windows: winget install UB-Mannheim.TesseractOCR)\n"
    "2) Install the Python package: pip install pytesseract\n\n"
    "Then try again."
)


def _tesseract_ready(ctx: RunContext):
    # find_tesseract() also honours the path saved by the "OCR setup" tool, so users
    # who installed Tesseract outside PATH no longer have to fight the environment.
    exe = find_tesseract()
    if not exe:
        raise ToolError(ctx.M(OCR_HINT_FA, OCR_HINT_EN))
    try:
        pytesseract = require("pytesseract", "pytesseract", ctx)
    except ToolError:
        raise ToolError(ctx.M(OCR_HINT_FA, OCR_HINT_EN))
    pytesseract.pytesseract.tesseract_cmd = exe
    return pytesseract, exe


@tool(
    id="image_ocr", category="image", name="خواندن متن از تصویر (OCR)",
    name_en="Read text from image (OCR)",
    desc="متن داخل عکس، اسکرین‌شات یا عکسِ سند را استخراج می‌کند (فارسی و انگلیسی).",
    desc_en="Extract text from photos, screenshots and scans (Persian and English).",
    icon="text", order=150,
    keywords=("ocr", "text from image", "خواندن متن عکس", "استخراج متن", "فارسی ocr", "اسکرین شات"),
    fields=[
        Field("src", K_FILE, "تصویر", "Image", required=True, filt=IMG_FILTER),
        Field("lang", K_CHOICE, "زبان", "Language", default="fas+eng",
              choices=[("fas+eng", "فارسی + انگلیسی", "Persian + English"),
                       ("eng", "انگلیسی", "English"), ("fas", "فارسی", "Persian"),
                       ("ara", "عربی", "Arabic")]),
        Field("psm", K_INT, "حالت چیدمان (PSM)", "Layout mode (PSM)", default=3, minimum=0, maximum=13,
              help="۳ = صفحه‌ی کامل، ۶ = یک بلوک متن، ۷ = یک خط. برای اسکرین‌شات‌ها ۶ بهتر است.",
              help_en="3 = full page, 6 = one block, 7 = one line. 6 often works better on screenshots."),
        Field("out", K_SAVE, "ذخیره در فایل", "Save to file", filt="متن (*.txt)"),
    ],
)
def image_ocr(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    pytesseract, _exe = _tesseract_ready(ctx)
    Image = require("PIL.Image", "Pillow", ctx)
    src = first_file(v, "src", ctx)
    ctx.progress(1, 3, ctx.M("آماده‌سازی تصویر", "Preparing image"))
    image = Image.open(src)
    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")
    ctx.progress(2, 3, ctx.M("خواندن متن", "Recognising text"))
    config = f"--oem 3 --psm {int(v.get('psm') or 3)}"
    try:
        text = pytesseract.image_to_string(image, lang=str(v.get("lang") or "fas+eng"), config=config)
    except Exception as exc:
        raise ToolError(ctx.M(
            f"خواندن متن ناموفق بود. اگر خطا از زبان فارسی است، بسته‌ی زبان را نصب کنید:\n{exc}",
            f"OCR failed. If the error mentions a language, install its data file:\n{exc}"))
    ctx.progress(3, 3, "")
    text = text.strip()
    if not text:
        return RunResult(message=ctx.M("متنی در تصویر پیدا نشد.", "No text was found in the image."))
    outputs: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        dest = save_target(ctx, requested, f"{src.stem}.txt")
        dest.write_text(text, encoding="utf-8")
        outputs.append(str(dest))
    words = len(text.split())
    return RunResult(
        message=ctx.M(f"{words} کلمه از تصویر خوانده شد.", f"Extracted {words} word(s).")
        + (f"\n{outputs[0]}" if outputs else ""),
        outputs=outputs, details=text[:15000])


@tool(
    id="pdf_ocr", category="pdf", name="PDF اسکن‌شده به متن (OCR)",
    name_en="OCR a scanned PDF",
    desc="PDF اسکن‌شده را به متن قابل جست‌وجو تبدیل می‌کند و یک PDF با لایه‌ی متن می‌سازد.",
    desc_en="Turn a scanned PDF into searchable text and a PDF with a text layer.",
    icon="scan", order=130,
    keywords=("ocr pdf", "scanned pdf", "پی دی اف اسکن", "قابل جستجو کردن پی دی اف"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt="PDF (*.pdf);;همه فایل‌ها (*)"),
        Field("lang", K_CHOICE, "زبان", "Language", default="fas+eng",
              choices=[("fas+eng", "فارسی + انگلیسی", "Persian + English"),
                       ("eng", "انگلیسی", "English"), ("fas", "فارسی", "Persian")]),
        Field("out_text", K_BOOL, "ذخیره‌ی متن در TXT", "Save text to TXT", default=True),
        Field("out_pdf", K_BOOL, "ساخت PDF متنی از نتیجه", "Build a text PDF from the result", default=True),
    ],
)
def pdf_ocr(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _tesseract_ready(ctx)
    from .pdf_tools import _fitz

    fitz = _fitz(ctx)
    src = first_file(v, "src", ctx)
    language = str(v.get("lang") or "fas+eng")
    language = "fas" if "fas" in language else "eng"
    doc = fitz.open(str(src))
    outputs: List[str] = []
    try:
        total = doc.page_count
        pages_text: List[str] = []
        for index in range(total):
            ctx.progress(index, total, ctx.M(f"OCR صفحه {index + 1} از {total}", f"OCR page {index + 1} of {total}"))
            page = doc.load_page(index)
            try:
                textpage = page.get_textpage_ocr(flags=0, language=language, dpi=200, full=True)
                pages_text.append(page.get_text(textpage=textpage))
            except Exception as exc:
                pages_text.append(ctx.M(f"[صفحه {index + 1}: OCR ناموفق — {exc}]",
                                        f"[page {index + 1}: OCR failed — {exc}]"))
        if v.get("out_pdf", True):
            dest_pdf = unique_path(out_dir(ctx) / f"{src.stem}_ocr.pdf")
            _write_text_pdf(ctx, dest_pdf, src.stem, pages_text)
            outputs.append(str(dest_pdf))
        if v.get("out_text", True):
            dest_txt = unique_path(out_dir(ctx) / f"{src.stem}_ocr.txt")
            dest_txt.write_text("\n\n".join(pages_text), encoding="utf-8")
            outputs.append(str(dest_txt))
        words = sum(len(part.split()) for part in pages_text)
        return RunResult(
            message=ctx.M(f"OCR {total} صفحه انجام شد ({words:,} کلمه).",
                          f"OCR finished for {total} page(s) ({words:,} words)."),
            outputs=outputs, open_target=str(Path(outputs[0]).parent) if outputs else None,
            details="\n\n".join(pages_text)[:12000])
    finally:
        doc.close()


def _write_text_pdf(ctx: RunContext, dest: Path, title: str, pages: List[str]) -> None:
    """Build a simple, selectable PDF from OCR output (keeps the text searchable)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    doc = canvas.Canvas(str(dest), pagesize=A4)
    width, height = A4
    doc.setTitle(title)
    for page_index, text in enumerate(pages, start=1):
        doc.setFont("Helvetica", 10)
        y = height - 50
        doc.drawString(40, y, f"{title} - {ctx.M('صفحه', 'page')} {page_index}")
        y -= 24
        for line in (text or "").splitlines():
            while len(line) > 95:
                doc.drawString(40, y, line[:95])
                line = line[95:]
                y -= 14
                if y < 50:
                    doc.showPage()
                    doc.setFont("Helvetica", 10)
                    y = height - 50
            doc.drawString(40, y, line)
            y -= 14
            if y < 50:
                doc.showPage()
                doc.setFont("Helvetica", 10)
                y = height - 50
        doc.showPage()
    doc.save()


@tool(
    id="barcode_generate", category="convert", name="ساخت بارکد و QR",
    name_en="Create barcode & QR",
    desc="QR یا بارکد خطی (Code128/EAN13) با رنگ و اندازه‌ی دلخواه می‌سازد.",
    desc_en="Generate QR codes or 1-D barcodes (Code128/EAN13) with custom size and colours.",
    icon="qr", order=205,
    keywords=("qr", "barcode", "بارکد", "کیو آر", "کد پستی", "code128"),
    fields=[
        Field("kind", K_CHOICE, "نوع", "Type", default="qr",
              choices=[("qr", "QR Code", "QR Code"), ("code128", "بارکد Code128", "Code128 barcode"),
                       ("ean13", "بارکد EAN-13", "EAN-13 barcode")]),
        Field("data", K_TEXT, "محتوا", "Content", required=True, placeholder="https://example.com"),
        Field("size", K_INT, "اندازه (پیکسل)", "Size (px)", default=600, minimum=100, maximum=2000, step=50),
        Field("color", K_COLOR, "رنگ", "Colour", default="#000000"),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="تصویر (*.png)"),
    ],
)
def barcode_generate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    data = str(v.get("data") or "").strip()
    if not data:
        raise ToolError(ctx.M("محتوا را وارد کنید.", "Enter the content."))
    kind = str(v.get("kind") or "qr")
    size = max(100, int(v.get("size") or 600))
    colour = str(v.get("color") or "#000000")
    dest = save_target(ctx, str(v.get("out") or ""), f"{kind}.png")
    ctx.progress(1, 2, ctx.M("ساخت کد", "Rendering"))
    if kind == "qr":
        qrcode = require("qrcode", "qrcode", ctx)
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=10, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        image = qr.make_image(fill_color=colour, back_color="#FFFFFF")
        image = image.resize((size, size))
        image.save(dest)
    else:
        barcode = require("barcode", "python-barcode", ctx)
        barcode_class = (barcode.get_barcode_class("code128") if kind == "code128"
                         else barcode.get_barcode_class("ean13"))
        try:
            from barcode.writer import ImageWriter

            code = barcode_class(data, writer=ImageWriter())
        except Exception as exc:
            raise ToolError(ctx.M(f"این محتوا برای {kind} معتبر نیست: {exc}",
                                  f"Content is not valid for {kind}: {exc}"))
        try:
            written = code.save(str(dest.with_suffix("")),
                                options={"write_text": True, "module_height": 12.0, "font_size": 9})
        except Exception as exc:
            raise ToolError(ctx.M(f"ساخت بارکد ناموفق بود: {exc}", f"Rendering the barcode failed: {exc}"))
        if isinstance(written, str) and Path(written).exists():
            dest = Path(written)
        generated = dest.with_suffix(".png")
        if generated.exists() and generated != dest:
            generated.replace(dest)
        elif dest.with_suffix(".svg").exists() and not dest.exists():
            raise ToolError(ctx.M("کتابخانه‌ی بارکد فایل PNG نساخت؛ python-barcode و Pillow را نصب کنید.",
                                  "The barcode library did not produce a PNG; install python-barcode and Pillow."))
    ctx.progress(2, 2, "")
    return RunResult(
        message=ctx.M(f"{kind.upper()} ساخته شد ({human_size(dest.stat().st_size)}).\n{dest}",
                      f"{kind.upper()} created ({human_size(dest.stat().st_size)}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest))


@tool(
    id="qr_scan", category="convert", name="خواندن QR از تصویر", name_en="Decode QR code",
    desc="QR موجود در عکس یا اسکرین‌شات را می‌خواند و متن/لینک داخلش را می‌دهد.",
    desc_en="Decode a QR code from a photo or screenshot and show the text/link inside.",
    icon="scan", order=206,
    keywords=("read qr", "qr scanner", "خواندن کیو آر", "اسکن qr"),
    fields=[
        Field("src", K_FILE, "تصویر", "Image", required=True, filt=IMG_FILTER),
    ],
)
def qr_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    cv2 = require("cv2", "opencv-python-headless", ctx)
    src = first_file(v, "src", ctx)
    ctx.progress(1, 2, ctx.M("آنالیز تصویر", "Analysing image"))
    image = cv2.imread(str(src))
    if image is None:
        raise ToolError(ctx.M("تصویر خوانده نشد.", "Could not read the image."))
    detector = cv2.QRCodeDetector()
    data, _points, _straight = detector.detectAndDecode(image)
    ctx.progress(2, 2, "")
    if not data and hasattr(detector, "detectAndDecodeMulti"):
        ok, decoded, _pts, _straight = detector.detectAndDecodeMulti(image)
        if ok and decoded:
            data = decoded[0]
    if not data:
        raise ToolError(ctx.M("هیچ QR سالمی در تصویر پیدا نشد (تصویر را واضح‌تر یا بزرگ‌تر کنید).",
                              "No readable QR code was found (try a sharper or larger image)."))
    kind = "link" if data.lower().startswith(("http://", "https://")) else "text"
    return RunResult(
        message=ctx.M(f"QR خوانده شد ({kind}).", f"QR decoded ({kind})."),
        details=data)


# ------------------------------------------------------------------ OCR setup
TRAINEDDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/{lang}.traineddata"
LANG_LABELS = {"fas": "فارسی", "eng": "انگلیسی", "ara": "عربی", "deu": "آلمانی", "fra": "فرانسوی"}


# Discovery lives in core so the search index can use it without importing tools.
from ..core.tesseract import (                            # noqa: E402  (kept together)
    find_tesseract as _core_find_tesseract,
    installed_langs as _core_installed_langs,
    tessdata_dir as _core_tessdata_dir,
)


def find_tesseract() -> str:
    return _core_find_tesseract()


def tessdata_dir(exe: str) -> Path:
    return _core_tessdata_dir(exe)


@tool(
    id="ocr_setup", category="system", name="راه‌اندازی OCR (موتور و زبان‌ها)",
    name_en="OCR setup (engine and languages)",
    desc=("وضعیت Tesseract را نشان می‌دهد، مسیر نصب غیرمعمول را ثبت می‌کند و بسته‌ی زبان "
          "فارسی/انگلیسی را دانلود می‌کند تا ابزارهای OCR از همان اول کار کنند."),
    desc_en=("Shows the Tesseract status, remembers an unusual install path and downloads "
             "Persian/English language data so the OCR tools work right away."),
    icon="scan", order=190,
    keywords=("ocr setup", "tesseract", "install ocr", "نصب ocr", "زبان فارسی ocr",
              "traineddata", "راه‌اندازی tess"),
    fields=[
        Field("action", K_CHOICE, "کار", "Action", default="status",
              choices=[("status", "بررسی وضعیت", "Check status"),
                       ("download", "دانلود بسته‌ی زبان", "Download language data"),
                       ("set_path", "ثبت مسیر نصب", "Save install path")]),
        Field("lang", K_CHOICE, "زبان بسته", "Language pack", default="fas",
              choices=[("fas", "فارسی", "Persian"), ("eng", "انگلیسی", "English"),
                       ("ara", "عربی", "Arabic")], visible_when=("action", "download")),
        Field("path", K_FILE, "مسیر tesseract.exe", "Path to tesseract.exe",
              visible_when=("action", "set_path"),
              help="اگر Tesseract را جای غیرمعمولی نصب کرده‌اید، اینجا انتخابش کنید.",
              help_en="Pick it here when Tesseract lives somewhere unusual."),
    ],
)
def ocr_setup(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    action = str(v.get("action") or "status")
    from ..core.settings import Settings

    settings = Settings()

    if action == "set_path":
        raw = str(v.get("path") or "").strip()
        if not raw:
            raise ToolError(ctx.M("مسیر خالی است.", "The path is empty."))
        candidate = Path(raw)
        if candidate.is_dir():
            for name in ("tesseract.exe", "tesseract"):
                if (candidate / name).exists():
                    candidate = candidate / name
                    break
        if not candidate.exists():
            raise ToolError(ctx.M(f"این مسیر وجود ندارد: {candidate}",
                                  f"That path does not exist: {candidate}"))
        settings.set("ocr_tesseract_path", str(candidate))
        langs = _installed_langs(candidate)
        return RunResult(
            message=ctx.M(f"مسیر ثبت شد: {candidate}",
                          f"Path saved: {candidate}"),
            details=ctx.M("زبان‌های نصب‌شده: ", "Installed languages: ")
                    + (", ".join(sorted(langs)) or ctx.M("هیچ", "none")),
        )

    exe = find_tesseract()
    if action == "download":
        lang = str(v.get("lang") or "fas")
        target_dir = _writable_tessdata(exe, ctx)
        target = target_dir / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 100_000:
            return RunResult(
                message=ctx.M(f"بسته‌ی {LANG_LABELS.get(lang, lang)} از قبل نصب است.",
                              f"The {lang} pack is already installed."),
                details=str(target), outputs=[],
            )
        try:
            import requests
        except Exception:
            raise ToolError(ctx.M("کتابخانه‌ی requests نصب نیست: pip install requests",
                                  "The requests package is missing: pip install requests"))
        url = TRAINEDDATA_URL.format(lang=lang)
        ctx.progress(1, 3, ctx.M("دانلود بسته‌ی زبان…", "Downloading language data…"))
        try:
            response = requests.get(url, timeout=60, stream=True)
            response.raise_for_status()
        except Exception as exc:
            raise ToolError(ctx.M(
                f"دانلود نشد: {exc}\n"
                f"می‌توانید فایل را دستی از {url} بگیرید و در این پوشه بگذارید:\n{target_dir}",
                f"Download failed: {exc}\n"
                f"You can fetch {url} manually and put it in:\n{target_dir}",
            ))
        target_dir.mkdir(parents=True, exist_ok=True)
        size = 0
        temp = target.with_suffix(".part")
        with temp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=262_144):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
        if size < 100_000:
            temp.unlink(missing_ok=True)
            raise ToolError(ctx.M("فایل دانلودشده معتبر نیست (خیلی کوچک است).",
                                  "The downloaded file looks invalid (too small)."))
        temp.replace(target)
        langs = _installed_langs(exe) if exe else set()
        message = ctx.M(f"بسته‌ی {LANG_LABELS.get(lang, lang)} نصب شد ({human_size(size)})",
                        f"The {lang} pack is installed ({human_size(size)})")
        if exe and lang not in langs:
            message += ctx.M(" · برای دیده‌شدن، برنامه را دوباره باز کنید",
                             " · restart the app to pick it up")
        return RunResult(message=message, details=str(target), outputs=[str(target)])

    # ---------------------------------------------------------------- status
    if not exe:
        return RunResult(
            message=ctx.M("موتور Tesseract نصب نیست.",
                          "The Tesseract engine is not installed."),
            details=ctx.M(
                "ویندوز:  winget install UB-Mannheim.TesseractOCR\n"
                "سپس:     pip install pytesseract\n"
                "و بعد همین ابزار را با «دانلود بسته‌ی زبان» اجرا کنید.",
                "Windows: winget install UB-Mannheim.TesseractOCR\n"
                "Then:    pip install pytesseract\n"
                "Then run this tool with “Download language data”.",
            ),
        )
    version = ""
    try:
        import subprocess

        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        version = (out.stdout or out.stderr or "").splitlines()[0].strip()
    except Exception:
        version = ctx.M("نسخه خوانده نشد", "version unavailable")
    langs = _installed_langs(exe)
    python_ready = True
    try:
        import pytesseract  # noqa: F401
    except Exception:
        python_ready = False
    persian = "fas" in langs
    details = "\n".join([
        f"{ctx.M('موتور', 'Engine')}: {exe}",
        f"{ctx.M('نسخه', 'Version')}: {version}",
        f"{ctx.M('پوشه‌ی زبان‌ها', 'Language folder')}: {tessdata_dir(exe)}",
        f"{ctx.M('زبان‌های نصب‌شده', 'Installed languages')}: {', '.join(sorted(langs)) or '—'}",
        f"pytesseract: {'OK' if python_ready else ctx.M('نصب نیست', 'not installed')}",
    ])
    if not python_ready:
        message = ctx.M("موتور هست، ولی pytesseract نصب نیست: pip install pytesseract",
                        "The engine exists but pytesseract is missing: pip install pytesseract")
    elif not persian:
        message = ctx.M("آماده است، ولی بسته‌ی فارسی ندارد — «دانلود بسته‌ی زبان» را اجرا کنید.",
                        "Ready, but without Persian — run “Download language data”.")
    else:
        message = ctx.M("OCR کامل آماده است (فارسی + انگلیسی).",
                        "OCR is fully ready (Persian + English).")
    return RunResult(message=message, details=details)


def _writable_tessdata(exe: str, ctx: RunContext) -> Path:
    """Prefer the engine's own folder; fall back to a per-user one we can write.

    A machine-wide Tesseract install (Program Files / /usr/share) is usually
    read-only for the current user, so downloading a language pack there would fail.
    In that case we keep the pack in the app's data folder and point Tesseract at it
    with TESSDATA_PREFIX, which both pytesseract and the CLI honour.
    """
    primary = tessdata_dir(exe)
    try:
        primary.mkdir(parents=True, exist_ok=True)
        probe = primary / ".wg_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return primary
    except OSError:
        pass
    from ..core.paths import data_dir

    fallback = data_dir() / "tessdata"
    fallback.mkdir(parents=True, exist_ok=True)
    os.environ["TESSDATA_PREFIX"] = str(fallback)
    try:
        from ..core.settings import Settings

        Settings().set("ocr_tessdata_path", str(fallback))
    except Exception:
        pass
    ctx.log(f"tessdata folder is read-only; using {fallback}")
    return fallback


def _installed_langs(exe: str) -> set:
    return _core_installed_langs(exe)
