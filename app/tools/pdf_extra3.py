"""PDF hygiene: size-aware splitting, link harvesting, annotation flattening, repair."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_INT, K_SAVE, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir, human_size, unique_path
from ._helpers import first_file, out_dir, save_target

PDF_FILTER = "PDF (*.pdf)"


def _pypdf(ctx: RunContext):
    try:
        from pypdf import PdfReader, PdfWriter

        return PdfReader, PdfWriter
    except Exception:
        raise ToolError(ctx.M(
            "برای این ابزار pypdf لازم است: pip install pypdf",
            "This tool needs pypdf: pip install pypdf"))


# ------------------------------------------------------------ split by size
@tool(
    id="pdf_split_by_size", category="pdf", name="شکستن PDF بر اساس حجم",
    name_en="Split a PDF by file size",
    desc="PDF را به بخش‌هایی می‌شکند که هر کدام زیر حجم دلخواه باشند (برای ایمیل و آپلود).",
    desc_en="Splits a PDF into parts that each stay under a size limit (email, upload portals).",
    icon="pdf", order=60,
    keywords=("split", "size", "mb", "email", "شکستن", "حجم", "مگابایت", "ایمیل"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("max_mb", K_INT, "حداکثر حجم هر بخش (مگابایت)", "Max size per part (MB)",
              default=10, minimum=1, maximum=500),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def pdf_split_by_size(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    max_mb = max(1, int(v.get("max_mb") or 10))
    limit = max_mb * 1024 * 1024
    reader = PdfReader(str(source))
    total_pages = len(reader.pages)
    if not total_pages:
        raise ToolError(ctx.M("این PDF هیچ صفحه‌ای ندارد.", "This PDF has no pages."))

    def page_size(index: int) -> int:
        probe = PdfWriter()
        probe.add_page(reader.pages[index])
        buffer = io.BytesIO()
        probe.write(buffer)
        return buffer.tell()

    sizes = []
    for index in range(total_pages):
        ctx.progress(index + 1, total_pages * 2, f"اندازه‌گیری صفحه {index + 1}")
        sizes.append(page_size(index))

    requested_dir = str(v.get("out_dir") or "").strip()
    folder = ensure_dir(Path(requested_dir) if requested_dir else out_dir(ctx, "split"))
    parts: List[str] = []
    current = PdfWriter()
    current_bytes = 0
    current_pages: List[int] = []
    for index, size in enumerate(sizes):
        ctx.progress(total_pages + index + 1, total_pages * 2, f"بسته‌بندی صفحه {index + 1}")
        if current_pages and current_bytes + size > limit:
            target = unique_path(folder / f"{source.stem}_part{len(parts) + 1}.pdf")
            with target.open("wb") as handle:
                current.write(handle)
            parts.append(str(target))
            current = PdfWriter()
            current_bytes = 0
            current_pages = []
        current.add_page(reader.pages[index])
        current_bytes += size
        current_pages.append(index)
    if current_pages:
        target = unique_path(folder / f"{source.stem}_part{len(parts) + 1}.pdf")
        with target.open("wb") as handle:
            current.write(handle)
        parts.append(str(target))

    biggest = max(Path(path).stat().st_size for path in parts)
    return RunResult(
        message=ctx.M(
            f"{len(parts)} بخش ساخته شد؛ بزرگ‌ترین {human_size(biggest)} "
            f"(سقف {max_mb} مگابایت).",
            f"{len(parts)} part(s) written; largest is {human_size(biggest)} "
            f"(limit {max_mb} MB)."),
        details="\n".join(parts), outputs=parts)


# ------------------------------------------------------------- extract links
@tool(
    id="pdf_extract_links", category="pdf", name="استخراج لینک‌های PDF",
    name_en="Extract PDF links",
    desc="همه‌ی لینک‌های وب و ارجاع‌های داخلی PDF را با شماره‌ی صفحه فهرست می‌کند و CSV می‌سازد.",
    desc_en="Lists every web link and internal reference in a PDF with its page number, and can save a CSV.",
    icon="link", order=61,
    keywords=("link", "url", "hyperlink", "reference", "لینک", "پیوند", "آدرس", "ارجاع"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("include_internal", K_BOOL, "ارجاع‌های داخلی هم بیاید", "Include internal jumps",
              default=True),
        Field("out", K_SAVE, "ذخیره‌ی CSV", "Save CSV", filt="CSV (*.csv)"),
    ],
)
def pdf_extract_links(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, _ = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    reader = PdfReader(str(source))
    include_internal = bool(v.get("include_internal", True))
    rows: List[tuple] = []
    for index, page in enumerate(reader.pages, start=1):
        ctx.progress(index, len(reader.pages), f"صفحه {index}")
        annotations = page.get("/Annots") or []
        for annotation in annotations:
            try:
                obj = annotation.get_object()
            except Exception:                      # noqa: BLE001 - damaged annotation
                continue
            if str(obj.get("/Subtype")) != "/Link":
                continue
            action = obj.get("/A") or {}
            uri = str(action.get("/URI") or "").strip() if hasattr(action, "get") else ""
            if uri:
                rows.append((index, "web", uri))
                continue
            if include_internal and hasattr(action, "get") and action.get("/S") is not None:
                rows.append((index, "internal", str(action.get("/S"))))
    if not rows:
        return RunResult(message=ctx.M("هیچ لینکی در این PDF پیدا نشد.",
                                       "No link was found in this PDF."), details="—")
    unique_web = sorted({row[2] for row in rows if row[1] == "web"})
    lines = [f"{page}\t{kind}\t{target}" for page, kind, target in rows]
    saved: List[str] = []
    requested = str(v.get("out") or "").strip()
    if requested:
        target = save_target(ctx, requested, f"{source.stem}_links.csv")
        body = "page,kind,target\n" + "\n".join(
            f'{page},{kind},"{url.replace(chr(34), chr(34) * 2)}"'
            for page, kind, url in rows)
        target.write_text(body, encoding="utf-8")
        saved.append(str(target))
    return RunResult(
        message=ctx.M(
            f"{len(rows)} لینک در {len(reader.pages)} صفحه پیدا شد "
            f"({len(unique_web)} آدرس یکتا).",
            f"{len(rows)} link(s) across {len(reader.pages)} page(s) "
            f"({len(unique_web)} unique URL(s))."),
        details="\n".join(lines[:400]), outputs=saved)


# --------------------------------------------------------- flatten annotations
@tool(
    id="pdf_flatten_annotations", category="pdf", name="پاک‌سازی یادداشت و فرم PDF",
    name_en="Flatten PDF annotations & forms",
    desc="یادداشت‌ها، هایلایت‌ها، کامنت‌ها و فیلدهای فرم را از PDF حذف می‌کند تا نسخه‌ی تمیز بسازد.",
    desc_en="Removes notes, highlights, comments and form fields so the PDF is clean before sharing.",
    icon="note", order=62,
    keywords=("annotation", "comment", "highlight", "form", "flatten", "یادداشت", "کامنت",
              "فرم", "هایلایت", "پاک‌سازی"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("drop_widgets", K_BOOL, "فیلدهای فرم هم حذف شوند", "Drop form fields too",
              default=True),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_flatten_annotations(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    reader = PdfReader(str(source))
    writer = PdfWriter()
    removed = 0
    for index, page in enumerate(reader.pages, start=1):
        ctx.progress(index, len(reader.pages) + 1, f"صفحه {index}")
        writer.add_page(page)
        target = writer.pages[-1]
        if "/Annots" in target:
            try:
                removed += len(target["/Annots"])
            except Exception:                        # noqa: BLE001 - malformed array
                removed += 1
            del target["/Annots"]
    root = writer._root_object                     # noqa: SLF001 - pypdf has no public API
    if bool(v.get("drop_widgets", True)) and "/AcroForm" in root:
        del root["/AcroForm"]
        removed += 1
    requested = str(v.get("out") or "").strip()
    target_path = save_target(ctx, requested, f"{source.stem}_flat.pdf")
    with target_path.open("wb") as handle:
        writer.write(handle)
    return RunResult(
        message=ctx.M(
            f"{removed} مورد (یادداشت/فرم) حذف شد؛ PDF تمیز ساخته شد.",
            f"{removed} annotation/form item(s) removed; clean PDF written."),
        outputs=[str(target_path)], open_target=str(target_path))


# ---------------------------------------------------------------------- repair
@tool(
    id="pdf_repair", category="pdf", name="تعمیر PDF خراب",
    name_en="Repair a broken PDF",
    desc="PDF آسیب‌دیده را بازخوانی و از نو می‌نویسد؛ صفحه‌های سالم را نجات می‌دهد و گزارش می‌دهد.",
    desc_en="Re-reads a damaged PDF and rewrites it, rescuing the readable pages and reporting what failed.",
    icon="refresh", order=63,
    keywords=("repair", "fix", "broken", "damaged", "corrupt", "تعمیر", "خراب", "آسیب", "نجات"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt=PDF_FILTER),
    ],
)
def pdf_repair(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    PdfReader, PdfWriter = _pypdf(ctx)
    source = first_file(v, "src", ctx)
    writer = PdfWriter()
    rescued: List[int] = []
    broken: List[str] = []
    try:
        reader = PdfReader(str(source), strict=False)
        total = len(reader.pages)
    except Exception as exc:                          # noqa: BLE001 - the point of the tool
        raise ToolError(ctx.M(f"فایل حتی قابل بازخوانی نیست: {exc}",
                              f"The file cannot even be parsed: {exc}"))
    for index in range(total):
        ctx.progress(index + 1, total, f"صفحه {index + 1}")
        try:
            writer.add_page(reader.pages[index])
            rescued.append(index + 1)
        except Exception as exc:                       # noqa: BLE001 - per-page damage
            broken.append(f"{index + 1}: {exc}")
    if not rescued:
        raise ToolError(ctx.M("هیچ صفحه‌ی سالمی پیدا نشد.", "No readable page was found."))
    try:
        if reader.metadata:
            writer.add_metadata({str(k): str(val) for k, val in reader.metadata.items()
                                 if k and val is not None})
    except Exception:                                  # noqa: BLE001 - metadata is optional
        pass
    requested = str(v.get("out") or "").strip()
    target = save_target(ctx, requested, f"{source.stem}_repaired.pdf")
    with target.open("wb") as handle:
        writer.write(handle)
    details = (f"rescued: {len(rescued)}/{total}\n" +
               ("\n".join(broken) if broken else "no broken page"))
    return RunResult(
        message=ctx.M(
            f"{len(rescued)} از {total} صفحه نجات یافت" +
            (f" · {len(broken)} صفحه خوانده نشد" if broken else " · همه‌ی صفحه‌ها سالم بودند"),
            f"{len(rescued)} of {total} page(s) rescued" +
            (f" · {len(broken)} unreadable" if broken else " · every page was fine")),
        details=details, outputs=[str(target)])
