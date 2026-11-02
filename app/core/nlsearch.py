"""Natural-language / fuzzy tool search — the "assistant brain".

The user can type things like:
    «پی‌دی‌اف رو به عکس تبدیل کن»
    "compress this image"
    "پسورد قوی بساز"

and we score every registered tool against the query.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Tuple

from .registry import CATEGORY_MAP, ToolSpec, all_tools
from .spec import Field

# Persian/Arabic normalisation map
_CHAR_MAP = {
    "ي": "ی", "ك": "ک", "ﮐ": "ک", "ﻙ": "ک", "ٱ": "ا", "أ": "ا", "إ": "ا",
    "آ": "آ", "ۀ": "ه", "ة": "ه", "ؤ": "و", "ئ": "ی",
}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

STOPWORDS = {
    "به", "را", "رو", "و", "در", "از", "با", "برای", "یه", "یک", "کن", "کنید", "کنم",
    "می", "خواهم", "میخوام", "می‌خواهم", "بکن", "the", "a", "an", "to", "of", "my",
    "this", "that", "please", "file", "files", "فایل", "فایلها", "کن،", "تبدیل",
    "and", "or", "into", "on", "in", "it",
}

# words that should boost a whole category
CATEGORY_HINTS: Dict[str, Tuple[str, ...]] = {
    "pdf": ("pdf", "پی دی اف", "پیدیاف", "پی‌دی‌اف", "قفل پی دی اف"),
    "image": ("عکس", "تصویر", "image", "photo", "picture", "png", "jpg", "jpeg", "عکسها"),
    "media": ("ویدیو", "فیلم", "صدا", "اهنگ", "آهنگ", "video", "audio", "mp3", "mp4", "gif", "media"),
    "files": ("فایل", "پوشه", "file", "folder", "زیپ", "zip", "rename", "اسم", "نام", "حجم پوشه"),
    "system": ("سیستم", "system", "رم", "cpu", "پردازنده", "سرویس", "service", "پاکسازی", "پاک‌سازی",
               "تمیز", "cleanup", "startup", "استارتاپ", "پروسه", "process", "باطری", "باتری", "هارد", "دیسک"),
    "network": ("شبکه", "network", "وای فای", "وای‌فای", "wifi", "wi-fi", "ip", "پینگ", "ping", "dns",
                "پورت", "port", "اینترنت", "internet"),
    "security": ("امنیت", "security", "رمز", "پسورد", "password", "رمزگذاری", "encrypt", "قفل", "hash", "هش"),
    "convert": ("تبدیل", "convert", "base64", "json", "csv", "اکسل", "excel", "واحد", "unit", "رنگ", "color",
                "qr", "کیو ار", "uuid", "متن", "text", "زمان", "تاریخ", "timestamp"),
    "windows": ("ویندوز", "windows", "ابزار ویندوز", "پاور", "power", "خاموش", "shutdown", "ریستارت",
                "restart", "اسکرین شات", "screenshot", "دیوار", "wallpaper", "تنظیمات", "settings", "device manager"),
}

# extra per-tool aliases (id -> words)
TOOL_HINTS: Dict[str, Tuple[str, ...]] = {
    "pdf_to_images": ("پی دی اف به عکس", "pdf to image", "pdf to png", "pdf to jpg"),
    "pdf_to_docx": ("پی دی اف به ورد", "pdf to word", "pdf to docx"),
    "images_to_pdf": ("عکس به پی دی اف", "image to pdf", "jpg to pdf"),
    "pdf_merge": ("ادغام", "چند پی دی اف", "merge pdf", "join pdf"),
    "pdf_split": ("جدا کردن صفحات", "split pdf", "تفکیک"),
    "pdf_compress": ("کم کردن حجم پی دی اف", "compress pdf", "فشرده سازی پی دی اف"),
    "image_resize": ("تغییر اندازه عکس", "resize image", "کوچک کردن عکس"),
    "image_compress": ("کم کردن حجم عکس", "compress image"),
    "image_convert": ("تبدیل فرمت عکس", "convert image", "png to jpg", "jpg to png", "webp"),
    "video_to_audio": ("استخراج صدا از فیلم", "extract audio", "mp4 to mp3", "ویدیو به صدا"),
    "video_to_gif": ("فیلم به گیف", "video to gif", "mp4 to gif"),
    "password_generate": ("ساخت پسورد", "پسورد قوی", "generate password", "رمز عبور"),
    "system_info": ("مشخصات سیستم", "system info", "اطلاعات کامپیوتر"),
    "temp_clean": ("پاکسازی فایلهای موقت", "clean temp", "تمیز کردن سیستم"),
    "wifi_passwords": ("پسورد وای فای", "wifi password", "رمز وایفای"),
    "screenshot": ("اسکرین شات", "screenshot", "عکس از صفحه"),
    "qr_generate": ("ساخت کیو ار", "qr code", "کیو ار کد"),
    "batch_rename": ("تغییر نام دسته جمعی", "batch rename"),
    "find_duplicates": ("فایلهای تکراری", "duplicate files"),
    "docx_to_pdf": ("ورد به پی دی اف", "word to pdf"),
    "text_transform": ("متن فارسی", "اصلاح متن", "نیم فاصله", "تبدیل اعداد"),
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text or ""))
    text = text.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    text = "".join(_CHAR_MAP.get(ch, ch) for ch in text)
    text = text.translate(_DIGITS)
    text = text.lower()
    text = re.sub(r"[^\w\u0600-\u06FF]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _field_words(f: Field) -> str:
    return " ".join(filter(None, (f.label, f.label_en, f.key)))


def strip_stopwords(text: str) -> str:
    """Normalise and drop filler words, keeping word order."""
    return " ".join(tok for tok in normalize(text).split(" ") if tok and tok not in STOPWORDS)


def _tool_phrases(spec: ToolSpec) -> List[str]:
    """Order-preserving key phrases for exact-ish phrase matching."""
    phrases = [
        strip_stopwords(spec.name), strip_stopwords(spec.name_en), spec.id.replace("_", " "),
    ]
    phrases += [strip_stopwords(kw) for kw in spec.keywords]
    phrases += [strip_stopwords(hint) for hint in TOOL_HINTS.get(spec.id, ())]
    return [p for p in phrases if p]


def _tool_text(spec: ToolSpec) -> str:
    parts = [
        spec.id.replace("_", " "),
        spec.name,
        spec.name_en,
        spec.category,
        CATEGORY_MAP[spec.category].name if spec.category in CATEGORY_MAP else "",
        CATEGORY_MAP[spec.category].name_en if spec.category in CATEGORY_MAP else "",
        spec.desc,
        spec.desc_en,
        " ".join(spec.keywords),
        " ".join(_field_words(f) for f in spec.fields),
        " ".join(TOOL_HINTS.get(spec.id, ())),
    ]
    return normalize(" ".join(parts))


def tokenize(query: str) -> List[str]:
    tokens = []
    for tok in normalize(query).split(" "):
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


def score_tool(spec: ToolSpec, tokens: List[str], text: str) -> float:
    if not tokens:
        return 0.0
    score = 0.0
    name_text = normalize(f"{spec.name} {spec.name_en} {spec.id}")
    for tok in tokens:
        if tok in name_text:
            score += 7
        elif tok in text:
            score += 3
        elif len(tok) > 3 and any(tok in w for w in name_text.split()):
            score += 4
    return score


def search(query: str, limit: int = 12) -> List[Tuple[float, ToolSpec]]:
    """Return ranked (score, tool) pairs for a free-text query."""
    tokens = tokenize(query)
    if not tokens:
        return []
    norm_query = " ".join(tokens)
    boosted: Dict[str, float] = {}
    for cat, words in CATEGORY_HINTS.items():
        for word in words:
            w = normalize(word)
            if w and w in norm_query:
                boosted[cat] = boosted.get(cat, 0.0) + 4.0
                break
    query_key = " ".join(tokens)
    results: List[Tuple[float, ToolSpec]] = []
    for spec in all_tools():
        text = _tool_text(spec)
        score = score_tool(spec, tokens, text)
        score += boosted.get(spec.category, 0.0)
        # order-sensitive phrase bonus: "عکس به پی‌دی‌اف" must beat "پی‌دی‌اف به عکس"
        for phrase in _tool_phrases(spec):
            if query_key and query_key == phrase:
                score += 18
                break
            if query_key and query_key in phrase:
                score += 11
                break
        if len(tokens) >= 2:
            if query_key in text:
                score += 4
            coverage = sum(1 for tok in tokens if tok in text) / len(tokens)
            score += 3 * coverage
        if score > 0:
            results.append((score, spec))
    results.sort(key=lambda item: (-item[0], item[1].category, item[1].name))
    return results[:limit]


def best(query: str) -> ToolSpec | None:
    hits = search(query, limit=1)
    return hits[0][1] if hits else None
