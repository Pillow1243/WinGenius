"""The assistant's brain: turn one Persian/English sentence into a real plan.

The planner understands a few dozen intents ("این عکس‌ها را کم‌حجم کن و PDF بساز")
and returns a :class:`~app.core.workflows.Workflow` built from the tools that
already exist.  No model, no network — just careful token matching with word
order, file extensions and required/forbidden terms, which keeps it instant and
predictable.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pathlib import Path

from .registry import get as get_tool
from .workflows import Step, Workflow, autofill, chain_values, input_field

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def normalize(text: str) -> str:
    """Lower-case, unify Arabic/Persian letters, drop ZWNJ and punctuation."""
    text = (text or "").replace("\u200c", " ").translate(_FA_DIGITS)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("أ", "ا")
    text = text.replace("‌", " ").lower()
    text = re.sub(r"[^0-9a-zآ-ی ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------- intents
# groups: at least one token of every group must appear
# order:  (i, j) => the token matched for group i must come before group j
INTENTS: List[Dict[str, Any]] = [
    {
        "id": "photos_to_pdf",
        "name": "عکس‌ها → PDF", "name_en": "Photos → PDF",
        "icon": "image",
        "groups": [["عکس", "تصویر", "image", "photo", "picture", "pics"], ["pdf", "پی دی اف", "پی‌دی‌اف"]],
        "order": (0, 1),
        "exts": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"),
        "steps": [("image_compress", {"quality": 80, "max_side": 2400}), ("images_to_pdf", {})],
        "example": "این عکس‌ها را کم‌حجم کن و PDF بساز",
        "example_en": "compress these photos and make a PDF",
    },
    {
        "id": "pdf_to_images",
        "name": "PDF → عکس", "name_en": "PDF → images",
        "icon": "pdf", "danger": False,
        "groups": [["pdf", "پی دی اف"], ["عکس", "تصویر", "image", "png", "jpg", "عکسها"]],
        "order": (0, 1),
        "exts": (".pdf",),
        "steps": [("pdf_to_images", {"fmt": "png", "dpi": 150})],
        "example": "PDF را به عکس تبدیل کن",
        "example_en": "convert PDF to images",
    },
    {
        "id": "pdf_to_text",
        "name": "PDF → متن", "name_en": "PDF → text",
        "icon": "text",
        "groups": [["pdf", "پی دی اف"], ["متن", "text", "txt", "word", "ورد"]],
        "order": (0, 1),
        "exts": (".pdf",),
        "steps": [("pdf_to_text", {})],
        "example": "متن PDF را استخراج کن",
        "example_en": "extract the text of a PDF",
    },
    {
        "id": "pdf_shrink",
        "name": "کم‌حجم کردن PDF", "name_en": "Shrink PDF",
        "icon": "pdf",
        "groups": [["pdf", "پی دی اف"], ["حجم", "کوچک", "فشرده", "compress", "shrink", "smaller", "reduce"]],
        "exts": (".pdf",),
        "steps": [("pdf_compress", {"mode": "strong", "dpi": 110})],
        "example": "حجم این PDF را کم کن",
        "example_en": "make this PDF smaller",
    },
    {
        "id": "pdf_protect",
        "name": "رمزگذاری PDF", "name_en": "Protect PDF",
        "icon": "lock",
        "groups": [["pdf", "پی دی اف"], ["رمز", "پسورد", "password", "protect", "قفل", "encrypt"]],
        "exts": (".pdf",),
        "steps": [("pdf_compress", {"mode": "light"}), ("pdf_encrypt", {"password": ""})],
        "example": "PDF را با رمز قفل کن",
        "example_en": "password protect this PDF",
    },
    {
        "id": "pdf_merge",
        "name": "ادغام PDF + شماره صفحه", "name_en": "Merge PDFs + numbers",
        "icon": "pdf",
        "groups": [["pdf", "پی دی اف"], ["ادغام", "یکی", "چسباندن", "merge", "combine", "join"]],
        "exts": (".pdf",),
        "steps": [("pdf_merge", {"sort": "name"}), ("pdf_page_numbers", {"start": 1})],
        "example": "این PDFها را ادغام کن و شماره صفحه بزن",
        "example_en": "merge these PDFs and add page numbers",
    },
    {
        "id": "scan_ocr",
        "name": "PDF اسکن → متن", "name_en": "Scanned PDF → text",
        "icon": "text",
        "groups": [["pdf", "پی دی اف", "اسکن", "scan"], ["ocr", "متن", "text", "خواندن", "استخراج"]],
        "exts": (".pdf",),
        "steps": [("pdf_ocr", {"lang": "fas+eng", "out_text": True, "out_pdf": True})],
        "example": "این PDF اسکن‌شده را OCR کن",
        "example_en": "OCR this scanned PDF",
    },
    {
        "id": "image_ocr",
        "name": "OCR عکس", "name_en": "Image → text (OCR)",
        "icon": "image",
        "groups": [["عکس", "تصویر", "image", "photo", "scan"], ["ocr", "متن", "text", "خواندن"]],
        "exts": (".png", ".jpg", ".jpeg", ".webp", ".bmp"),
        "steps": [("image_ocr", {"lang": "fas+eng", "psm": 3})],
        "example": "متن داخل این عکس را بخوان",
        "example_en": "read the text in this image",
    },
    {
        "id": "screenshot_ocr",
        "name": "اسکرین‌شات → متن", "name_en": "Screenshot → text",
        "icon": "camera",
        "groups": [["اسکرین", "screenshot", "صفحه"], ["متن", "ocr", "text", "خواندن"]],
        "steps": [("screenshot", {"monitor": "all"}), ("image_ocr", {"lang": "fas+eng"})],
        "example": "از صفحه عکس بگیر و متنش را استخراج کن",
        "example_en": "screenshot the screen and OCR it",
    },
    {
        "id": "screenshot",
        "name": "اسکرین‌شات", "name_en": "Screenshot",
        "icon": "camera",
        "groups": [["اسکرین", "screenshot", "عکس بگیر", "capture"]],
        "steps": [("screenshot", {"monitor": "all"})],
        "example": "یک اسکرین‌شات بگیر",
        "example_en": "take a screenshot",
    },
    {
        "id": "image_shrink",
        "name": "کم‌حجم کردن عکس", "name_en": "Compress images",
        "icon": "image",
        "groups": [["عکس", "تصویر", "image", "photo"], ["حجم", "کوچک", "فشرده", "compress", "reduce", "smaller"]],
        "exts": (".png", ".jpg", ".jpeg", ".webp", ".bmp"),
        "steps": [("image_compress", {"quality": 72, "max_side": 1920})],
        "example": "حجم عکس‌ها را کم کن",
        "example_en": "compress these images",
    },
    {
        "id": "image_resize",
        "name": "تغییر اندازه عکس", "name_en": "Resize images",
        "icon": "image",
        "groups": [["عکس", "تصویر", "image", "photo"], ["اندازه", "resize", "ابعاد", "size", "کوچک‌تر"]],
        "exts": (".png", ".jpg", ".jpeg", ".webp", ".bmp"),
        "steps": [("image_resize", {"mode": "max_side", "max_side": 1280})],
        "example": "اندازه عکس‌ها را ۱۲۸۰ کن",
        "example_en": "resize these images to 1280",
    },
    {
        "id": "image_watermark",
        "name": "واترمارک + PDF", "name_en": "Watermark + PDF",
        "icon": "brush",
        "groups": [["واترمارک", "watermark", "لوگو", "امضا", "brand"]],
        "steps": [("image_watermark", {"text": "@WinGenius"}), ("images_to_pdf", {"quality": 88})],
        "example": "روی عکس‌ها واترمارک بزن و PDF بساز",
        "example_en": "watermark images and make a PDF",
    },
    {
        "id": "video_audio",
        "name": "ویدیو → صدا", "name_en": "Video → audio",
        "icon": "film",
        "groups": [["ویدیو", "فیلم", "video", "movie", "clip"], ["صدا", "audio", "mp3", "آهنگ"]],
        "exts": (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v"),
        "steps": [("video_to_audio", {"fmt": "mp3", "bitrate": "192k"}),
                  ("audio_normalize", {"target": -16.0})],
        "example": "صدای این ویدیو را جدا کن",
        "example_en": "extract the audio from this video",
    },
    {
        "id": "video_gif",
        "name": "ویدیو → GIF", "name_en": "Video → GIF",
        "icon": "film",
        "groups": [["ویدیو", "فیلم", "video", "clip"], ["gif", "گیف", "انیمیشن"]],
        "exts": (".mp4", ".mkv", ".avi", ".mov", ".webm"),
        "steps": [("video_to_gif", {"duration": 5, "fps": 12, "width": 480})],
        "example": "از این فیلم یک گیف بساز",
        "example_en": "make a GIF from this video",
    },
    {
        "id": "zip_files",
        "name": "ساخت ZIP", "name_en": "Zip files",
        "icon": "archive",
        "groups": [["zip", "زیپ", "آرشیو", "archive", "فشرده کن"]],
        "steps": [("zip_create", {"level": "normal"})],
        "example": "این فایل‌ها را زیپ کن",
        "example_en": "zip these files",
    },
    {
        "id": "encrypt_files",
        "name": "ZIP رمزدار", "name_en": "Encrypted archive",
        "icon": "lock",
        "groups": [["رمز", "پسورد", "password", "encrypt", "قفل"], ["فایل", "files", "زیپ", "zip", "آرشیو", "پوشه", "folder"]],
        "steps": [("zip_create", {"level": "normal"}), ("file_encrypt", {"password": ""})],
        "example": "این فایل‌ها را رمزگذاری کن",
        "example_en": "encrypt these files",
    },
    {
        "id": "qr_make",
        "name": "ساخت QR", "name_en": "Make a QR code",
        "icon": "qr",
        "groups": [["qr", "کیو آر", "بارکد", "barcode", "کد"]],
        "steps": [("qr_generate", {"mode": "text", "size": 600})],
        "example": "یک QR کد برای این لینک بساز",
        "example_en": "make a QR code for this link",
    },
    {
        "id": "password_make",
        "name": "ساخت رمز قوی", "name_en": "Strong password",
        "icon": "shield",
        "groups": [["رمز", "پسورد", "password", "گذرواژه"], ["بساز", "generate", "قوی", "strong", "جدید"]],
        "steps": [("password_generate", {"length": 20})],
        "example": "یک رمز عبور قوی بساز",
        "example_en": "generate a strong password",
    },
    {
        "id": "duplicates",
        "name": "فایل‌های تکراری", "name_en": "Find duplicates",
        "icon": "copy",
        "groups": [["تکراری", "duplicate", "دوبار", "همسان"]],
        "steps": [("find_duplicates", {})],
        "example": "فایل‌های تکراری این پوشه را پیدا کن",
        "example_en": "find duplicate files in this folder",
    },
    {
        "id": "big_files",
        "name": "فایل‌های حجیم", "name_en": "Large files",
        "icon": "folder",
        "groups": [["حجیم", "بزرگ", "large", "big"], ["فایل", "files", "پوشه", "folder", "disk"]],
        "steps": [("large_files", {})],
        "example": "فایل‌های بزرگ دیسک را نشان بده",
        "example_en": "show the largest files",
    },
    {
        "id": "rename_batch",
        "name": "تغییر نام گروهی", "name_en": "Batch rename",
        "icon": "pencil",
        "groups": [["نام", "rename", "اسم"], ["گروهی", "دسته", "batch", "همه", "all"]],
        "steps": [("batch_rename", {"mode": "template", "template": "{name}_{n}", "preview": True})],
        "example": "اسم همه‌ی فایل‌های این پوشه را عوض کن",
        "example_en": "rename all files in this folder",
    },
    {
        "id": "hash_files",
        "name": "اثر انگشت فایل", "name_en": "File hashes",
        "icon": "shield",
        "groups": [["هش", "hash", "sha", "md5", "اثر انگشت", "checksum"]],
        "steps": [("hash_files", {"algo": "sha256"})],
        "example": "هش SHA-256 این فایل‌ها را بگیر",
        "example_en": "hash these files",
    },
    {
        "id": "secret_scan",
        "name": "اسکن رمز در پروژه", "name_en": "Scan hidden secrets",
        "icon": "shield",
        "groups": [["رمز", "توکن", "کلید", "secret", "token", "api"], ["پروژه", "پوشه", "folder", "project", "فایل‌ها", "code"]],
        "steps": [("secret_scan", {})],
        "example": "این پوشه را برای رمز و توکن اسکن کن",
        "example_en": "scan this folder for secrets",
    },
    {
        "id": "system_health",
        "name": "سلامت سیستم", "name_en": "System health",
        "icon": "monitor",
        "groups": [["سیستم", "system", "کامپیوتر", "pc"], ["سلامت", "health", "وضعیت", "status", "کند", "slow"]],
        "steps": [("system_health", {})],
        "example": "وضعیت سیستم را بررسی کن",
        "example_en": "check how healthy my system is",
    },
    {
        "id": "temp_clean",
        "name": "پاک‌سازی موقت", "name_en": "Clean temp files",
        "icon": "monitor",
        "groups": [["پاک", "clean", "تمیز", "free"], ["موقت", "temp", "cache", "زباله", "space", "جا"]],
        "steps": [("temp_clean", {})],
        "example": "فایل‌های موقت را پاک کن",
        "example_en": "clean temporary files",
    },
    {
        "id": "wifi_passwords",
        "name": "رمز وای‌فای‌ها", "name_en": "Wi-Fi passwords",
        "icon": "wifi",
        "groups": [["وای", "wifi", "وای‌فای", "شبکه"], ["رمز", "password", "پسورد"]],
        "steps": [("wifi_passwords", {})],
        "example": "رمز وای‌فای‌های ذخیره‌شده را نشان بده",
        "example_en": "show saved Wi-Fi passwords",
    },
    {
        "id": "excel_csv",
        "name": "اکسل → CSV", "name_en": "Excel → CSV",
        "icon": "table",
        "groups": [["اکسل", "excel", "xlsx", "sheet"], ["csv", "متن", "تبدیل"]],
        "order": (0, 1),
        "exts": (".xlsx", ".xls"),
        "steps": [("sheet_to_csv", {})],
        "example": "اکسل را به CSV تبدیل کن",
        "example_en": "convert Excel to CSV",
    },
    {
        "id": "office_pdf",
        "name": "Word → PDF", "name_en": "Word → PDF",
        "icon": "pdf",
        "groups": [["word", "ورد", "docx", "office", "اکسل", "excel"], ["pdf", "پی دی اف"]],
        "order": (0, 1),
        "exts": (".docx", ".doc", ".pptx", ".xlsx"),
        "steps": [("docx_to_pdf", {})],
        "example": "این فایل ورد را PDF کن",
        "example_en": "convert this Word file to PDF",
    },
    {
        "id": "index_build",
        "name": "ایندکس جست‌وجو", "name_en": "Build a search index",
        "icon": "search",
        "groups": [["ایندکس", "index", "جست", "search"], ["پوشه", "folder", "فایل", "files", "همه", "all"]],
        "steps": [("index_build", {})],
        "example": "همه‌ی فایل‌های این پوشه را ایندکس کن",
        "example_en": "index every file in this folder",
    },
    {
        "id": "screen_record_note",
        "name": "پاکت گزارش", "name_en": "Report pack",
        "icon": "report",
        "groups": [["گزارش", "report"], ["سیستم", "system", "pdf", "فایل"]],
        "steps": [("system_health", {})],
        "example": "یک گزارش سیستم بساز",
        "example_en": "build a system report",
    },
]


def _match_score(intent: Dict[str, Any], text: str, extensions: Sequence[str]) -> int:
    for key in ("tokens",):
        for token in intent.get(key) or []:
            if token not in text:
                return 0
    groups: List[Sequence[str]] = list(intent.get("groups") or [])
    positions: List[int] = []
    score = 0
    for group in groups:
        found = -1
        for token in group:
            index = text.find(token)
            if index >= 0 and (found < 0 or index < found):
                found = index
                matched = token
        if found < 0:
            return 0
        positions.append(found)
        score += 4 + min(len(matched), 6)
    order = intent.get("order")
    if order and len(positions) >= 2:
        first, second = order
        if positions[first] >= positions[second]:
            return 0
        score += 6
    exts = intent.get("exts") or ()
    if exts and extensions:
        if any(ext in exts for ext in extensions):
            score += 5
        else:
            score -= 3
    for banned in intent.get("unless") or []:
        if banned in text:
            return 0
    return score


def _extensions(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for path in paths or []:
        name = str(path).lower()
        if "." in name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
            out.append("." + name.rsplit(".", 1)[-1])
    return out


def match_intent(text: str, paths: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    normalized = normalize(text)
    if not normalized:
        return None
    extensions = _extensions(paths)
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for intent in INTENTS:
        score = _match_score(intent, normalized, extensions)
        if score > best_score:
            best_score = score
            best = intent
    if best is not None:
        best = dict(best)
        best["score"] = best_score
    return best


def build_plan(text: str, paths: Sequence[str] = (), lang: str = "fa") -> Optional[Workflow]:
    """Return a ready-to-run workflow for the sentence, or None."""
    intent = match_intent(text, paths)
    if intent is None:
        return None
    steps: List[Step] = []
    for index, (tool_id, extra) in enumerate(intent["steps"]):
        spec = get_tool(tool_id)
        if spec is None:
            return None
        if index == 0:
            values = autofill(spec, list(paths))
        else:
            values = chain_values(spec)
        values.update({key: value for key, value in (extra or {}).items()})
        steps.append(Step(tool_id=tool_id, values=values,
                          note=(spec.desc_for(lang) or "")[:90]))
    if not steps:
        return None
    workflow = Workflow(
        id=f"plan-{intent['id']}-{datetime.now().strftime('%H%M%S')}",
        name=str(intent.get("name") or intent["id"]) if lang != "en" else str(intent.get("name_en") or intent["id"]),
        name_en=str(intent.get("name_en") or ""),
        desc=str(intent.get("example") or ""),
        desc_en=str(intent.get("example_en") or ""),
        icon=str(intent.get("icon") or "layers"),
        source="plan",
        steps=steps,
    )
    if paths:
        workflow.desc = f"{len(paths)} فایل انتخاب شد"
        workflow.desc_en = f"{len(paths)} file(s) selected"
    return workflow


def suggestions(lang: str = "fa", limit: int = 8) -> List[Tuple[str, str]]:
    """(sentence, intent_id) pairs for the assistant's chips."""
    picked: List[Tuple[str, str]] = []
    favourite = ("photos_to_pdf", "pdf_to_images", "image_shrink", "video_audio", "scan_ocr",
                 "screenshot_ocr", "zip_files", "system_health")
    for intent in INTENTS:
        if intent["id"] in favourite:
            text = intent["example_en"] if lang == "en" else intent["example"]
            picked.append((text, intent["id"]))
    return picked[:limit]


def all_intents() -> List[Dict[str, Any]]:
    return list(INTENTS)


def describe(workflow: Workflow, lang: str = "fa") -> str:
    """One line per step, used by the plan sheet and the CLI."""
    lines: List[str] = []
    for index, step in enumerate(workflow.steps, start=1):
        spec = get_tool(step.tool_id)
        title = spec.name_for(lang) if spec else step.tool_id
        filled = []
        for key, value in (step.values or {}).items():
            if not isinstance(value, (str, list)):
                continue
            if isinstance(value, list):
                if not value:
                    continue
                text = ", ".join(Path(p).name if isinstance(p, str) else str(p) for p in value[:3])
            else:
                text = value
            if text in ("", "None") or text == "{prev}":
                continue
            filled.append(f"{key}={text}")
        suffix = f" ({', '.join(filled[:3])})" if filled and lang != "en" else ""
        lines.append(f"{index}. {title}{suffix}")
    return "\n".join(lines)


def tool_for_text(text: str) -> Optional[str]:
    """Best single tool id for a sentence (used for clipboard suggestions)."""
    intent = match_intent(text)
    if intent and intent["steps"]:
        return intent["steps"][0][0]
    return None
