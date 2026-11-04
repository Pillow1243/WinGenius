"""Persian-first PDF tools: repair copy/paste of Persian PDFs and write Persian PDFs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core import persian_pdf
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILE, K_INT, K_MULTILINE, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size
from ._helpers import first_file, read_text_file, save_target

PDF_FILTER = "PDF (*.pdf);;همه فایل‌ها (*)"


def _fitz(ctx: RunContext):
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except Exception:
        try:
            import fitz  # type: ignore

            return fitz
        except Exception as exc:
            raise ToolError(ctx.M(f"PyMuPDF در دسترس نیست: {exc}", f"PyMuPDF is not available: {exc}"))


# ------------------------------------------------------------ fix persian copy
@tool(
    id="pdf_fix_persian", category="pdf", name="تعمیر متن فارسی PDF",
    name_en="Repair Persian text layer",
    desc=("نقشه‌ی قلم‌های PDF را طوری تعمیر می‌کند که کپی، جست‌وجو و استخراج متن فارسی در هر نرم‌افزاری "
          "(Acrobat، Chrome، Word) درست کار کند؛ حروف چسبان و نویسه‌های به‌هم‌ریخته (ѫ ѱ ӳ) از بین می‌روند."),
    desc_en=("Repairs the font maps of a PDF so copying, searching and extracting Persian text works in every "
             "viewer (Acrobat, Chrome, Word) — joined letters and garbage glyphs (ѫ ѱ ӳ) are gone."),
    icon="text", order=23,
    keywords=("persian pdf", "copy paste", "garbled", "tounicode", "متن فارسی", "کپی", "به هم ریخته",
              "جستجو", "حروف جدا", "تعمیر"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("preview", K_BOOL, "نمایش نمونه‌ی متن قبل و بعد", "Show a before/after text sample", default=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def pdf_fix_persian(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    src = first_file(v, "src", ctx)
    try:
        doc = fitz.open(str(src))
    except Exception as exc:
        raise ToolError(ctx.M(f"این PDF باز نشد: {exc}", f"Could not open the PDF: {exc}"))
    if doc.is_encrypted and doc.needs_pass:
        doc.close()
        raise ToolError(ctx.M("این PDF رمز دارد؛ ابتدا رمز را بردارید.", "This PDF is encrypted; remove the password first."))
    before = ""
    if v.get("preview", True) and doc.page_count:
        before = doc[0].get_text("text")[:600]
    ctx.progress(1, 3, ctx.M("تحلیل قلم‌های جاسازی‌شده…", "Analysing embedded fonts…"))
    repairs = persian_pdf.repair_tounicode(doc, aggressive=True, normalize_forms=True)
    ctx.progress(2, 3, ctx.M("ذخیره…", "Saving…"))
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_fixed.pdf")
    try:
        doc.save(str(dest), garbage=3, deflate=True)
    finally:
        doc.close()
    after = ""
    if v.get("preview", True):
        try:
            fixed = fitz.open(str(dest))
            after = fixed[0].get_text("text")[:600] if fixed.page_count else ""
            fixed.close()
        except Exception:
            after = ""
    fonts = len(repairs)
    added = sum(r.added for r in repairs)
    fixed_count = sum(r.fixed for r in repairs)
    lines: List[str] = []
    for repair in repairs:
        lines.append(f"• {repair.name}: +{repair.added} / ~{repair.fixed}" + (" (new map)" if repair.created else ""))
    if v.get("preview", True):
        lines.append("")
        lines.append(ctx.M("— قبل —", "— before —"))
        lines.append(before.strip() or ctx.M("(بدون متن)", "(no text)"))
        lines.append("")
        lines.append(ctx.M("— بعد —", "— after —"))
        lines.append(persian_pdf.normalize_persian(after).strip() or ctx.M("(بدون متن)", "(no text)"))
    if not repairs:
        message = ctx.M("نقشه‌ی قلم‌ها از قبل کامل بود؛ فایل بدون تغییر ذخیره شد.",
                        "The font maps were already complete; the file was saved unchanged.")
    else:
        message = ctx.M(
            f"{fonts} قلم تعمیر شد: {added} نگاشت جدید و {fixed_count} نگاشت اصلاح‌شده. ({human_size(dest.stat().st_size)})",
            f"{fonts} font(s) repaired: {added} new and {fixed_count} corrected mappings. ({human_size(dest.stat().st_size)})")
    return RunResult(message=message, details="\n".join(lines), outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------------ text → pdf
@tool(
    id="text_to_pdf", category="pdf", name="متن/مارک‌داون به PDF فارسی",
    name_en="Text / Markdown → Persian PDF",
    desc=("متن ساده یا Markdown را با قلم وزیرمتن، چینش راست‌به‌چپ خودکار، تیتر، فهرست و جدول "
          "به یک PDF تمیز تبدیل می‌کند — بدون نیاز به Word."),
    desc_en=("Turns plain text or Markdown into a clean PDF with the bundled Vazirmatn font, automatic RTL, "
             "headings, lists and tables — no Word required."),
    icon="pdf", order=24,
    keywords=("text to pdf", "markdown to pdf", "persian pdf", "متن به pdf", "مارک داون", "فارسی", "وزیر"),
    fields=[
        Field("source", K_CHOICE, "ورودی", "Input", default="text",
              choices=[("text", "متن", "Text"), ("file", "فایل (.txt/.md)", "File (.txt/.md)")]),
        Field("text", K_MULTILINE, "متن یا Markdown", "Text or Markdown", visible_when=("source", "text"),
              placeholder="# عنوان\n\nمتن فارسی با **تأکید** و فهرست:\n- یک\n- دو"),
        Field("file", K_FILE, "فایل متنی", "Text file", filt="متن (*.txt *.md *.markdown);;همه فایل‌ها (*)",
              visible_when=("source", "file")),
        Field("format", K_CHOICE, "قالب ورودی", "Input format", default="auto",
              choices=[("auto", "تشخیص خودکار", "Detect"), ("markdown", "Markdown", "Markdown"),
                       ("plain", "متن ساده", "Plain text")]),
        Field("title", K_TEXT, "عنوان سند (اختیاری)", "Document title (optional)"),
        Field("size", K_INT, "اندازه‌ی قلم", "Font size", default=12, minimum=8, maximum=24),
        Field("page", K_CHOICE, "اندازه‌ی صفحه", "Page size", default="a4",
              choices=[("a4", "A4", "A4"), ("a5", "A5", "A5"), ("letter", "Letter", "Letter")]),
        Field("digits", K_CHOICE, "ارقام", "Digits", default="keep",
              choices=[("keep", "بدون تغییر", "Keep"), ("fa", "فارسی ۱۲۳", "Persian ۱۲۳"),
                       ("en", "لاتین 123", "Latin 123")]),
        Field("numbers", K_BOOL, "شماره‌ی صفحه", "Page numbers", default=True),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="PDF (*.pdf)"),
    ],
)
def text_to_pdf(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    fitz = _fitz(ctx)
    source = str(v.get("source") or "text")
    name = "document"
    if source == "file":
        path = first_file(v, "file", ctx)
        text = read_text_file(path, ctx)
        name = path.stem
    else:
        text = str(v.get("text") or "")
    if not text.strip():
        raise ToolError(ctx.M("متنی وارد نشده است.", "No text given."))
    digits = str(v.get("digits") or "keep")
    text = persian_pdf.normalize_persian(text, digits=digits if digits in ("fa", "en") else None,
                                         zwnj=False, fix_reversed=False)
    fmt = str(v.get("format") or "auto")
    if fmt == "auto":
        import re

        fmt = "markdown" if re.search(r"(^|\n)(#{1,6} |[-*] |\d+\. |\|.*\|)|\*\*|`", text) else "plain"
    title = str(v.get("title") or "").strip()
    if fmt == "markdown":
        try:
            import markdown as md_mod  # type: ignore

            html = md_mod.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
        except Exception:
            html = persian_pdf.text_to_html(text)
        html = persian_pdf.direction_html(html)
    else:
        html = persian_pdf.text_to_html(text, size_pt=float(v.get("size") or 12))
    if title:
        html = persian_pdf.paragraph_html(title, tag="h1", bold=True) + html
    sizes = {"a4": (595.0, 842.0), "a5": (420.0, 595.0), "letter": (612.0, 792.0)}
    page_size = sizes.get(str(v.get("page") or "a4"), sizes["a4"])
    size_pt = float(v.get("size") or 12)
    doc = fitz.open()
    ctx.progress(1, 3, ctx.M("صفحه‌بندی…", "Laying out…"))
    try:
        pages = persian_pdf.flow_html_pages(doc, html, size_pt=size_pt, page_size=page_size,
                                            css=persian_pdf.font_css(size_pt, extra=(
                                                "h1{font-size:1.9em;} h2{font-size:1.5em;} h3{font-size:1.25em;}"
                                                "li{margin:0.15em 0;} code,pre{font-family:monospace; font-size:0.9em;}"
                                                "blockquote{border-inline-start:3pt solid #888; padding:0 8pt; color:#444;}"
                                                "th{background:#eee; font-weight:bold;}")))
        if v.get("numbers", True) and pages > 1:
            rtl = persian_pdf.is_rtl(text)
            for index in range(doc.page_count):
                label = f"{index + 1} / {doc.page_count}"
                persian_pdf.stamp_line(doc[index], persian_pdf.to_persian_digits(label) if rtl else label,
                                       position="bottom-center", size_pt=9)
        doc.set_metadata({"title": title or name, "producer": "WinGenius", "creator": "WinGenius"})
        ctx.progress(2, 3, ctx.M("ذخیره…", "Saving…"))
        dest = save_target(ctx, str(v.get("out") or ""), f"{title or name}.pdf".replace("/", "-"))
        persian_pdf.save_pdf(doc, dest)
    finally:
        doc.close()
    return RunResult(
        message=ctx.M(f"PDF ساخته شد ({pages} صفحه، {human_size(dest.stat().st_size)}).",
                      f"PDF created ({pages} page(s), {human_size(dest.stat().st_size)})."),
        outputs=[str(dest)], open_target=str(dest),
    )


TOOL_COUNT = 2
