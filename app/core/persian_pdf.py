"""Persian / RTL text and PDF engine shared by the PDF, OCR, Office and text tools.

Why this module exists
----------------------
Persian PDFs break in three independent ways and every tool used to hit at least one:

1. **Unmapped OpenType glyphs.** Modern Persian fonts (Vazirmatn, IRANSans, Dana,
   B Nazanin …) shape initial/medial/stretched letters through ``GSUB`` into glyphs
   that have *no* Unicode code point, so they are missing from the ``/ToUnicode``
   CMap. Extractors then leak the raw glyph id as Cyrillic/Greek junk ("ѫ", "ѱ", "ӳ").
   We parse the embedded font (``post`` names, ``cmap`` and ``GSUB`` lookups) and map
   every glyph back to its real letter – and we can write the repaired CMap back into
   the PDF so Acrobat / Chrome / Word copy clean Persian too.
2. **Presentation forms and reversed glyph order.** Some extractors emit Arabic
   Presentation Forms (U+FB50–U+FEFF) and/or visual (reversed) order. We normalise
   the forms, restore ``ی``/``ک``, keep ZWNJ where the glyph geometry shows a
   half-space, and flip reversed runs back to logical order.
3. **Tofu when writing.** ReportLab's Helvetica cannot shape Arabic script; every
   stamped watermark, page number or OCR text layer rendered as boxes. Everything
   here renders through MuPDF's HarfBuzz-shaped HTML engine with the bundled
   Vazirmatn family, so output is correct in both directions.

The module is import-safe (no heavy imports at module import time) and used by the
CLI, the tools and the indexer alike.
"""
from __future__ import annotations

import io
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .paths import resource_path

# ----------------------------------------------------------------------- fonts
FONT_FAMILY = "Vazirmatn"
FONT_FILES = {
    "regular": "Vazirmatn-Regular.ttf",
    "medium": "Vazirmatn-Medium.ttf",
    "semibold": "Vazirmatn-SemiBold.ttf",
    "bold": "Vazirmatn-Bold.ttf",
    "extrabold": "Vazirmatn-ExtraBold.ttf",
}


def fonts_dir() -> Path:
    """Folder that holds the bundled Vazirmatn family (works frozen and from source)."""
    return resource_path("fonts")


def font_path(weight: str = "regular") -> Path:
    return fonts_dir() / FONT_FILES.get(weight, FONT_FILES["regular"])


def fonts_available() -> bool:
    return font_path("regular").exists() and font_path("bold").exists()


def font_css(size_pt: float = 11.0, *, color: str = "#111111", extra: str = "") -> str:
    """CSS that registers the bundled family and sets sane RTL defaults."""
    faces = (
        f"@font-face {{ font-family: {FONT_FAMILY}; src: url({FONT_FILES['regular']}); }}\n"
        f"@font-face {{ font-family: {FONT_FAMILY}; src: url({FONT_FILES['bold']}); font-weight: bold; }}\n"
        f"@font-face {{ font-family: {FONT_FAMILY}; src: url({FONT_FILES['medium']}); font-weight: 500; }}\n"
        f"@font-face {{ font-family: {FONT_FAMILY}; src: url({FONT_FILES['semibold']}); font-weight: 600; }}\n"
    )
    return (
        faces
        + f"body {{ font-family: {FONT_FAMILY}, 'Segoe UI', sans-serif; font-size: {size_pt}pt; "
        f"color: {color}; margin: 0; line-height: 1.75; }}\n"
        "h1, h2, h3 { margin: 0.3em 0; line-height: 1.4; }\n"
        "p { margin: 0 0 0.45em 0; }\n"
        "table { border-collapse: collapse; width: 100%; }\n"
        "td, th { border: 0.6pt solid #666; padding: 2pt 4pt; }\n"
        + extra
    )


_FONT_ARCHIVE_CACHE: Dict[str, Any] = {}


def font_archive():
    """Cached `pymupdf.Archive` pointing at the bundled fonts (or None when unavailable)."""
    folder = fonts_dir()
    key = str(folder)
    if key in _FONT_ARCHIVE_CACHE:
        return _FONT_ARCHIVE_CACHE[key]
    if not folder.exists():
        return None
    fitz = _fitz()
    try:
        arc = fitz.Archive(key)
        _FONT_ARCHIVE_CACHE[key] = arc
        return arc
    except Exception:
        return None


def _fitz():
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except Exception:
        import fitz  # type: ignore

        return fitz


# -------------------------------------------------------------- script helpers
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
LATIN_DIGITS = "0123456789"
_TO_PERSIAN_DIGITS = str.maketrans(LATIN_DIGITS + ARABIC_DIGITS, PERSIAN_DIGITS * 2)
_TO_LATIN_DIGITS = str.maketrans(PERSIAN_DIGITS + ARABIC_DIGITS, LATIN_DIGITS * 2)

ARABIC_TO_PERSIAN = {
    "\u064A": "\u06CC",  # ي → ی
    "\u0649": "\u06CC",  # ى → ی
    "\u06CD": "\u06CC",
    "\u0643": "\u06A9",  # ك → ک
    "\u06AA": "\u06A9",
    "\u0629": "\u0647",  # ة → ه
    "\u06C0": "\u0647",  # ۀ → ه (hamza-above heh)
    "\u0640": "",        # tatweel
    "\u202A": "", "\u202B": "", "\u202C": "", "\u202D": "", "\u202E": "",  # legacy bidi controls
    "\u200E": "", "\u200F": "",  # LRM / RLM
    "\u200B": "", "\uFEFF": "",  # zero-width space / BOM
}
_ARABIC_TRANS = {ord(k): v for k, v in ARABIC_TO_PERSIAN.items()}

RTL_RANGE = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
PERSIAN_LETTER = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
PRESENTATION_FORM = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
LATIN_LETTER = re.compile(r"[A-Za-z]")

# letters that never join to the following letter (so a ZWNJ after them is pointless)
NON_JOINERS = set("اآأإدذرزژوؤء")
# verb prefixes / suffixes that take a half-space in standard Persian orthography
ZWNJ_PREFIXES = ("نمی", "می")
ZWNJ_SUFFIXES = ("هایی", "هایم", "هایت", "هایش", "هایمان", "هایتان", "هایشان", "های", "ها",
                 "ترین", "تر", "ام", "ات", "اش", "ایم", "اید", "اند")
# words that are commonly written attached to «می» but are not verbs (keep the space)
_MI_EXCEPTIONS = {"میز", "میان", "میهن", "میل", "میوه", "میدان", "میخ", "مین", "میرزا", "میم", "میش", "میکرو"}
# present/past stems of the most frequent Persian verbs (for glued «می»+stem repairs)
_VERB_STEMS = "|".join((
    "شو", "شد", "کن", "کرد", "باش", "توان", "توانست", "رو", "رفت", "گو", "گفت", "ده", "داد", "گیر", "گرفت",
    "آ", "آی", "آمد", "خواه", "خواست", "بین", "دید", "دان", "دانست", "یاب", "یافت", "مان", "ماند", "ساز", "ساخت",
    "پرداز", "پرداخت", "رس", "رسید", "زن", "زد", "خور", "خورد", "افت", "افتاد", "نویس", "نوشت", "خوان", "خواند",
    "گذار", "گذاشت", "شناس", "شناخت", "پذیر", "پذیرفت", "بند", "بست", "انداز", "انداخت", "بر", "برد", "آور", "آورد",
    "کش", "کشید", "نما", "نمود", "گرد", "گشت", "فرست", "فرستاد", "خر", "خرید", "فروش", "فروخت", "پرس", "پرسید",
    "ایست", "ایستاد", "نشین", "نشست", "خواب", "خوابید", "شمار", "شمرد", "سوز", "سوخت", "ریز", "ریخت", "پوش", "پوشید",
    "دار", "داشت", "گذر", "گذشت", "افزا", "افزود", "کاه", "کاست", "آموز", "آموخت", "اندیش", "اندیشید", "کوش", "کوشید",
    "خند", "خندید", "بخش", "بخشید", "ترس", "ترسید", "چرخ", "چرخید", "دو", "دوید", "شکن", "شکست", "بار", "بارید",
    "پز", "پخت", "رقص", "رقصید", "جنگ", "جنگید", "لرز", "لرزید", "ورز", "ورزید", "پیوند", "پیوست", "بوس", "بوسید",
    "فهم", "فهمید", "طلب", "طلبید", "سنج", "سنجید", "گنج", "گنجید", "چسب", "چسبید", "دزد", "دزدید", "خز", "خزید",
))
_VERB_ENDINGS = "م|ی|د|یم|ید|ند|ه|ه‌ام|ه‌ای|ه‌ایم|ه‌اید|ه‌اند"


def _presentation_shape(ch: str) -> str:
    """'initial' / 'medial' / 'final' / 'isolated' / '' for an Arabic presentation form."""
    code = ord(ch)
    if not (0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF):
        return ""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return ""
    for shape in ("INITIAL", "MEDIAL", "FINAL", "ISOLATED"):
        if name.endswith(shape + " FORM"):
            return shape.lower()
    return ""


def is_rtl(text: str) -> bool:
    """True when the text is mostly right-to-left script."""
    if not text:
        return False
    rtl = len(RTL_RANGE.findall(text))
    ltr = len(LATIN_LETTER.findall(text))
    return rtl > 0 and rtl >= ltr


def has_persian(text: str) -> bool:
    return bool(text) and bool(PERSIAN_LETTER.search(text))


def to_persian_digits(text: str) -> str:
    return text.translate(_TO_PERSIAN_DIGITS)


def to_latin_digits(text: str) -> str:
    return text.translate(_TO_LATIN_DIGITS)


def strip_diacritics(text: str) -> str:
    """Remove Arabic harakat / tashdid / tanwin (used for sorting & searching)."""
    return re.sub(r"[\u064B-\u0652\u0670\u0653-\u0655\u06D6-\u06ED]", "", text)


def normalize_presentation_forms(text: str) -> str:
    """Turn Arabic Presentation Forms (ﺑ ﻼ ﯾ …) back into plain letters.

    Only the presentation-form characters are decomposed; ``NFKC`` on the whole string
    would also mangle things like ``½`` or ``ﬁ`` that the user may want to keep.
    """
    if not PRESENTATION_FORM.search(text):
        return text
    out: List[str] = []
    for ch in text:
        if PRESENTATION_FORM.match(ch):
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


def looks_reversed(text: str) -> bool:
    """Heuristic: presentation forms stored in visual (reversed) order.

    In logical order an initial form opens a word and a final form closes it. When a
    text comes out of a legacy extractor reversed, we see the opposite pattern.
    """
    words = [w for w in re.split(r"\s+", text) if len(w) >= 2 and PRESENTATION_FORM.search(w)]
    if not words:
        return False
    good = bad = 0
    for word in words:
        first, last = _presentation_shape(word[0]), _presentation_shape(word[-1])
        if first == "initial" or last == "final":
            good += 1
        if first == "final" or last == "initial":
            bad += 1
    return bad > good


def visual_to_logical(text: str) -> str:
    """Reverse RTL runs of a visually ordered line (keeps Latin words readable)."""
    lines = []
    for line in text.split("\n"):
        if not line or not looks_reversed(line):
            lines.append(line)
            continue
        # split into runs of RTL vs. non-RTL so numbers/Latin keep their order
        runs = re.findall(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF\s\u200C،؛؟«»()\-]+|[^\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]+", line)
        rebuilt = []
        for run in reversed(runs):
            if RTL_RANGE.search(run):
                rebuilt.append(run[::-1])
            else:
                rebuilt.append(run)
        lines.append("".join(rebuilt))
    return "\n".join(lines)


def smart_zwnj(text: str) -> str:
    """Insert half-spaces where standard Persian orthography wants them.

    Handles ``می/نمی`` verb prefixes, plural/comparative/possessive suffixes and
    cleans doubled or misplaced ZWNJs. Conservative on purpose: every rule needs a
    Persian letter on both sides and never touches Latin or numbers.
    """
    if not has_persian(text):
        return text
    # drop ZWNJ that does nothing (next to spaces, punctuation, line ends, or after non-joiners)
    text = re.sub(r"[\u200C]{2,}", "\u200C", text)
    text = re.sub(r"(^|[\s\d\W])\u200C+", r"\1", text)
    text = re.sub(r"\u200C+(?=$|[\s\d]|[^\w\u0600-\u06FF])", "", text)
    text = re.sub(r"(?<=[اآأإدذرزژوؤء])\u200C", "", text)

    # «می شود» → «می‌شود», «نمی توانم» → «نمی‌توانم»
    def _prefix(match: re.Match) -> str:
        prefix, word = match.group(1), match.group(2)
        if (prefix + word) in _MI_EXCEPTIONS or len(word) < 2:
            return match.group(0)
        return f"{prefix}\u200C{word}"
    text = re.sub(r"(?<![\u0600-\u06FF\u200C])(نمی|می) +([\u0600-\u06FF]{2,})", _prefix, text)

    # «میباشد» / «نمیشود» (prefix glued without ZWNJ) → «می‌باشد» / «نمی‌شود»
    def _glued(match: re.Match) -> str:
        prefix, stem, ending = match.group(1), match.group(2), match.group(3) or ""
        if match.group(0) in _MI_EXCEPTIONS or (prefix + stem) in _MI_EXCEPTIONS:
            return match.group(0)
        return f"{prefix}\u200C{stem}{ending}"
    text = re.sub(rf"(?<![\u0600-\u06FF\u200C])(نمی|می)({_VERB_STEMS})({_VERB_ENDINGS})?(?![\u0600-\u06FF])", _glued, text)

    # «کتاب ها» → «کتاب‌ها», «بزرگ تر» → «بزرگ‌تر», «خانه ام» → «خانه‌ام»
    suffixes = "|".join(ZWNJ_SUFFIXES)

    def _suffix(match: re.Match) -> str:
        word, suffix = match.group(1), match.group(2)
        if len(word) < 2:
            return match.group(0)
        return f"{word}\u200C{suffix}"
    text = re.sub(rf"([\u0600-\u06FF]{{2,}}) +({suffixes})(?![\u0600-\u06FF])", _suffix, text)
    return text


_MIRROR_PAIRS = (("«", "»"), ("(", ")"), ("[", "]"), ("{", "}"))


def fix_mirrored_brackets(text: str) -> str:
    """Undo bidi mirroring leaks: RTL lines that read «»-swapped (``»سلام«``).

    Fonts substitute the mirrored glyph for brackets inside RTL runs; when the
    ``/ToUnicode`` of that glyph names the *mirrored* character, extracted text ends
    up with every pair swapped. Only lines where the first bracket seen is a closing
    one and the counts match are touched, so normal text is left alone.
    """
    out_lines = []
    for line in text.split("\n"):
        if is_rtl(line):
            for open_ch, close_ch in _MIRROR_PAIRS:
                opens, closes = line.count(open_ch), line.count(close_ch)
                if opens and opens == closes and line.find(close_ch) < line.find(open_ch):
                    line = line.translate(str.maketrans({open_ch: close_ch, close_ch: open_ch}))
        out_lines.append(line)
    return "\n".join(out_lines)


def normalize_persian(text: str, *, digits: Optional[str] = None, zwnj: bool = True,
                      fix_reversed: bool = True) -> str:
    """Full clean-up pipeline used by every extractor and by `persian_fix`.

    digits: None = keep, "fa" = Persian, "en" = Latin.
    """
    if not text:
        return text
    if fix_reversed and PRESENTATION_FORM.search(text):
        text = visual_to_logical(text)
    text = normalize_presentation_forms(text)
    text = text.translate(_ARABIC_TRANS)
    # the Lam-Alef ligature decomposes to U+0644 U+0627 — already fine after NFKC
    text = fix_mirrored_brackets(text)
    text = re.sub(r"[ \t\u00A0]{2,}", " ", text)
    text = re.sub(r" +([،؛:!؟?.,])", r"\1", text)
    text = re.sub(r"([،؛؟])(?=[^\s\d\n])", r"\1 ", text)
    if zwnj:
        text = smart_zwnj(text)
    if digits == "fa":
        text = to_persian_digits(text)
    elif digits == "en":
        text = to_latin_digits(text)
    return text


def persian_sort_key(line: str) -> Tuple:
    """Collation key for the Persian alphabet (آ/ا first, diacritics ignored, numbers numeric)."""
    alphabet = "آاءأإئبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی"
    order = {ch: i for i, ch in enumerate(alphabet)}
    order["آ"] = order["ا"]  # آ sorts together with ا
    text = strip_diacritics(normalize_persian(line.strip(), zwnj=False))
    text = to_latin_digits(text).replace("\u200C", "")
    key: List[Tuple[int, Any]] = []
    for part in re.split(r"(\d+)", text):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, tuple(order.get(ch, 200 + ord(ch)) for ch in part.lower())))
    return tuple(key)


# -------------------------------------------------------- embedded font parsing
def _ttf_tables(data: bytes) -> Dict[str, Tuple[int, int]]:
    if len(data) < 12:
        return {}
    tag = data[:4]
    offset = 0
    if tag == b"ttcf":  # font collection: use the first font
        if len(data) < 16:
            return {}
        offset = struct.unpack_from(">I", data, 12)[0]
    if offset + 12 > len(data):
        return {}
    num_tables = struct.unpack_from(">H", data, offset + 4)[0]
    tables: Dict[str, Tuple[int, int]] = {}
    for i in range(num_tables):
        pos = offset + 12 + i * 16
        if pos + 16 > len(data):
            break
        name, _, start, length = struct.unpack_from(">4sIII", data, pos)
        if start < len(data):
            tables[name.decode("latin1", "ignore")] = (start, min(length, len(data) - start))
    return tables


def _post_names(data: bytes, tables: Dict[str, Tuple[int, int]]) -> Dict[int, str]:
    """GID → PostScript glyph name from a format-2.0 ``post`` table."""
    if "post" not in tables:
        return {}
    start, length = tables["post"]
    if start + 34 > len(data) or struct.unpack_from(">I", data, start)[0] != 0x00020000:
        return {}
    count = struct.unpack_from(">H", data, start + 32)[0]
    if start + 34 + 2 * count > len(data):
        return {}
    indices = struct.unpack_from(f">{count}H", data, start + 34)
    pos = start + 34 + 2 * count
    end = min(len(data), start + length)
    names: List[str] = []
    while pos < end:
        n = data[pos]
        pos += 1
        names.append(data[pos:pos + n].decode("latin1", "ignore"))
        pos += n
    out: Dict[int, str] = {}
    for gid, index in enumerate(indices):
        if index >= 258 and index - 258 < len(names):
            out[gid] = names[index - 258]
    return out


def _cff_names(data: bytes, tables: Dict[str, Tuple[int, int]]) -> Dict[int, str]:
    """GID → glyph name for CFF-flavoured OpenType (post table is format 3 there)."""
    if "CFF " not in tables:
        return {}
    start, length = tables["CFF "]
    cff = data[start:start + length]
    try:
        return _parse_cff_charset(cff)
    except Exception:
        return {}


_CFF_STANDARD_STRINGS_COUNT = 391


def _cff_read_index(buf: bytes, pos: int) -> Tuple[List[bytes], int]:
    count = struct.unpack_from(">H", buf, pos)[0]
    pos += 2
    if count == 0:
        return [], pos
    off_size = buf[pos]
    pos += 1
    offsets = []
    for i in range(count + 1):
        chunk = buf[pos + i * off_size: pos + (i + 1) * off_size]
        offsets.append(int.from_bytes(chunk, "big"))
    pos += (count + 1) * off_size
    base = pos - 1
    items = [buf[base + offsets[i]: base + offsets[i + 1]] for i in range(count)]
    return items, base + offsets[-1]


def _cff_parse_dict(buf: bytes) -> Dict[int, List[float]]:
    out: Dict[int, List[float]] = {}
    operands: List[float] = []
    i = 0
    while i < len(buf):
        b0 = buf[i]
        if b0 <= 21:
            op = b0
            i += 1
            if b0 == 12:
                op = 1200 + buf[i]
                i += 1
            out[op] = operands
            operands = []
        elif b0 == 28:
            operands.append(struct.unpack_from(">h", buf, i + 1)[0])
            i += 3
        elif b0 == 29:
            operands.append(struct.unpack_from(">i", buf, i + 1)[0])
            i += 5
        elif b0 == 30:  # real number: skip nibbles
            i += 1
            while i < len(buf):
                byte = buf[i]
                i += 1
                if byte & 0x0F == 0x0F or byte >> 4 == 0x0F:
                    break
            operands.append(0.0)
        elif 32 <= b0 <= 246:
            operands.append(b0 - 139)
            i += 1
        elif 247 <= b0 <= 250:
            operands.append((b0 - 247) * 256 + buf[i + 1] + 108)
            i += 2
        elif 251 <= b0 <= 254:
            operands.append(-(b0 - 251) * 256 - buf[i + 1] - 108)
            i += 2
        else:
            i += 1
    return out


def _parse_cff_charset(cff: bytes) -> Dict[int, str]:
    hdr_size = cff[2]
    pos = hdr_size
    _, pos = _cff_read_index(cff, pos)           # Name INDEX
    top_dicts, pos = _cff_read_index(cff, pos)   # Top DICT INDEX
    strings, pos = _cff_read_index(cff, pos)     # String INDEX
    top = _cff_parse_dict(top_dicts[0])
    if 1230 in top:  # CIDFont: charset maps to CIDs, not names
        return {}
    charstrings_off = int(top.get(17, [0])[0])
    if not charstrings_off:
        return {}
    charstrings, _ = _cff_read_index(cff, charstrings_off)
    nglyphs = len(charstrings)
    charset_off = int(top.get(15, [0])[0])
    if charset_off in (0, 1, 2):
        return {}

    def sid_name(sid: int) -> str:
        if sid < _CFF_STANDARD_STRINGS_COUNT:
            return ""
        index = sid - _CFF_STANDARD_STRINGS_COUNT
        return strings[index].decode("latin1", "ignore") if index < len(strings) else ""

    fmt = cff[charset_off]
    pos = charset_off + 1
    out: Dict[int, str] = {}
    gid = 1
    if fmt == 0:
        while gid < nglyphs and pos + 2 <= len(cff):
            sid = struct.unpack_from(">H", cff, pos)[0]
            pos += 2
            name = sid_name(sid)
            if name:
                out[gid] = name
            gid += 1
    elif fmt in (1, 2):
        while gid < nglyphs and pos < len(cff):
            first = struct.unpack_from(">H", cff, pos)[0]
            pos += 2
            if fmt == 1:
                n_left = cff[pos]
                pos += 1
            else:
                n_left = struct.unpack_from(">H", cff, pos)[0]
                pos += 2
            for k in range(n_left + 1):
                if gid >= nglyphs:
                    break
                name = sid_name(first + k)
                if name:
                    out[gid] = name
                gid += 1
    return out


def _cmap_reverse(data: bytes, tables: Dict[str, Tuple[int, int]]) -> Dict[int, int]:
    """GID → Unicode from the font's own cmap (formats 4 and 12)."""
    if "cmap" not in tables:
        return {}
    start, _ = tables["cmap"]
    if start + 4 > len(data):
        return {}
    count = struct.unpack_from(">H", data, start + 2)[0]
    out: Dict[int, int] = {}
    for i in range(count):
        pos = start + 4 + i * 8
        if pos + 8 > len(data):
            break
        platform, encoding, offset = struct.unpack_from(">HHI", data, pos)
        sub = start + offset
        if sub + 4 > len(data):
            continue
        fmt = struct.unpack_from(">H", data, sub)[0]
        try:
            if fmt == 4:
                seg_x2 = struct.unpack_from(">H", data, sub + 6)[0]
                segs = seg_x2 // 2
                ends = struct.unpack_from(f">{segs}H", data, sub + 14)
                starts = struct.unpack_from(f">{segs}H", data, sub + 16 + seg_x2)
                deltas = struct.unpack_from(f">{segs}h", data, sub + 16 + 2 * seg_x2)
                range_pos = sub + 16 + 3 * seg_x2
                range_offsets = struct.unpack_from(f">{segs}H", data, range_pos)
                for s in range(segs):
                    if starts[s] > ends[s] or ends[s] == 0xFFFF and starts[s] == 0xFFFF:
                        continue
                    for code in range(starts[s], min(ends[s], 0xFFFE) + 1):
                        if range_offsets[s] == 0:
                            gid = (code + deltas[s]) & 0xFFFF
                        else:
                            addr = range_pos + s * 2 + range_offsets[s] + (code - starts[s]) * 2
                            if addr + 2 > len(data):
                                continue
                            gid = struct.unpack_from(">H", data, addr)[0]
                            if gid:
                                gid = (gid + deltas[s]) & 0xFFFF
                        if gid and gid not in out:
                            out[gid] = code
            elif fmt == 12:
                ngroups = struct.unpack_from(">I", data, sub + 12)[0]
                for g in range(min(ngroups, 20000)):
                    gpos = sub + 16 + g * 12
                    if gpos + 12 > len(data):
                        break
                    c0, c1, g0 = struct.unpack_from(">III", data, gpos)
                    for k, code in enumerate(range(c0, min(c1, c0 + 2000) + 1)):
                        gid = g0 + k
                        if gid not in out:
                            out[gid] = code
        except struct.error:
            continue
    return out


def _coverage(data: bytes, pos: int) -> List[int]:
    if pos + 4 > len(data):
        return []
    fmt, count = struct.unpack_from(">HH", data, pos)
    glyphs: List[int] = []
    if fmt == 1:
        if pos + 4 + 2 * count <= len(data):
            glyphs = list(struct.unpack_from(f">{count}H", data, pos + 4))
    elif fmt == 2:
        for r in range(count):
            rpos = pos + 4 + r * 6
            if rpos + 6 > len(data):
                break
            start, end, _ = struct.unpack_from(">HHH", data, rpos)
            if end >= start and end - start < 5000:
                glyphs.extend(range(start, end + 1))
    return glyphs


def _gsub_reverse(data: bytes, tables: Dict[str, Tuple[int, int]], known: Dict[int, str]) -> Dict[int, str]:
    """Follow single & ligature substitutions backwards so shaped glyphs inherit text.

    Only the lookup types that keep a 1:1 (type 1) or n:1 (type 4) relation are
    followed; contextual lookups just chain to them. Iterates until stable so
    ``init → init.alt → init.alt.swash`` style chains resolve.
    """
    if "GSUB" not in tables:
        return {}
    start, length = tables["GSUB"]
    end = min(len(data), start + length)
    if start + 10 > len(data):
        return {}
    lookup_list = start + struct.unpack_from(">H", data, start + 8)[0]
    if lookup_list + 2 > end:
        return {}
    lookup_count = struct.unpack_from(">H", data, lookup_list)[0]
    singles: List[Tuple[int, int]] = []           # (input gid, output gid)
    ligatures: List[Tuple[Tuple[int, ...], int]] = []  # (components, ligature gid)
    for i in range(min(lookup_count, 512)):
        lpos_ptr = lookup_list + 2 + i * 2
        if lpos_ptr + 2 > end:
            break
        lookup = lookup_list + struct.unpack_from(">H", data, lpos_ptr)[0]
        if lookup + 6 > end:
            continue
        ltype, _flag, sub_count = struct.unpack_from(">HHH", data, lookup)
        for s in range(min(sub_count, 64)):
            sptr = lookup + 6 + s * 2
            if sptr + 2 > end:
                break
            sub = lookup + struct.unpack_from(">H", data, sptr)[0]
            actual_type = ltype
            if ltype == 7 and sub + 8 <= end:  # extension
                _, actual_type, ext = struct.unpack_from(">HHI", data, sub)
                sub = sub + ext
            if sub + 6 > end:
                continue
            if actual_type == 1:
                fmt, cov_off = struct.unpack_from(">HH", data, sub)
                coverage = _coverage(data, sub + cov_off)
                if fmt == 1:
                    delta = struct.unpack_from(">h", data, sub + 4)[0]
                    singles.extend((g, (g + delta) & 0xFFFF) for g in coverage)
                elif fmt == 2:
                    count = struct.unpack_from(">H", data, sub + 4)[0]
                    if sub + 6 + 2 * count <= end:
                        outs = struct.unpack_from(f">{count}H", data, sub + 6)
                        singles.extend(zip(coverage, outs))
            elif actual_type == 4:
                _fmt, cov_off, set_count = struct.unpack_from(">HHH", data, sub)
                coverage = _coverage(data, sub + cov_off)
                for si in range(min(set_count, len(coverage))):
                    set_ptr = sub + 6 + si * 2
                    if set_ptr + 2 > end:
                        break
                    lig_set = sub + struct.unpack_from(">H", data, set_ptr)[0]
                    if lig_set + 2 > end:
                        continue
                    lig_count = struct.unpack_from(">H", data, lig_set)[0]
                    for li in range(min(lig_count, 64)):
                        lptr = lig_set + 2 + li * 2
                        if lptr + 2 > end:
                            break
                        lig = lig_set + struct.unpack_from(">H", data, lptr)[0]
                        if lig + 4 > end:
                            continue
                        lig_glyph, comp_count = struct.unpack_from(">HH", data, lig)
                        comps = (coverage[si],)
                        if comp_count > 1 and lig + 4 + 2 * (comp_count - 1) <= end:
                            comps += struct.unpack_from(f">{comp_count - 1}H", data, lig + 4)
                        ligatures.append((comps, lig_glyph))
    resolved: Dict[int, str] = dict(known)
    for _ in range(6):
        changed = False
        for src, dst in singles:
            if dst not in resolved and src in resolved:
                resolved[dst] = resolved[src]
                changed = True
        for comps, lig in ligatures:
            if lig not in resolved and all(c in resolved for c in comps):
                resolved[lig] = "".join(resolved[c] for c in comps)
                changed = True
        if not changed:
            break
    return {gid: text for gid, text in resolved.items() if gid not in known}


_GLYPH_NAME_ALIASES = {
    "space": " ", "zwnj": "\u200C", "uni200C": "\u200C", "zerowidthnonjoiner": "\u200C",
    "zwj": "\u200D", "nbspace": "\u00A0", "hyphen": "-", "period": ".", "comma": ",",
    "colon": ":", "semicolon": ";", "slash": "/", "parenleft": "(", "parenright": ")",
    "percent": "%", "exclam": "!", "question": "?", "quotedbl": '"', "quotesingle": "'",
    "guillemotleft": "«", "guillemotright": "»", "endash": "–", "emdash": "—",
    "afii57388": "،", "afii57403": "؛", "afii57407": "؟", "afii57409": "ء", "afii57410": "آ",
    "afii57411": "أ", "afii57412": "ؤ", "afii57413": "إ", "afii57414": "ئ", "afii57415": "ا",
    "afii57416": "ب", "afii57417": "ة", "afii57418": "ت", "afii57419": "ث", "afii57420": "ج",
    "afii57421": "ح", "afii57422": "خ", "afii57423": "د", "afii57424": "ذ", "afii57425": "ر",
    "afii57426": "ز", "afii57427": "س", "afii57428": "ش", "afii57429": "ص", "afii57430": "ض",
    "afii57431": "ط", "afii57432": "ظ", "afii57433": "ع", "afii57434": "غ", "afii57440": "ـ",
    "afii57441": "ف", "afii57442": "ق", "afii57443": "ك", "afii57444": "ل", "afii57445": "م",
    "afii57446": "ن", "afii57470": "ه", "afii57448": "و", "afii57449": "ى", "afii57450": "ي",
    "afii57392": "۰", "afii57393": "۱", "afii57394": "۲", "afii57395": "۳", "afii57396": "۴",
    "afii57397": "۵", "afii57398": "۶", "afii57399": "۷", "afii57400": "۸", "afii57401": "۹",
    "afii57506": "پ", "afii57507": "چ", "afii57508": "ژ", "afii57509": "گ", "afii57511": "ک",
    "afii57519": "ی", "afii57534": "ۀ",
}
_ARABIC_GLYPH_WORDS = {
    "alef": "ا", "beh": "ب", "peh": "پ", "teh": "ت", "theh": "ث", "jeem": "ج", "tcheh": "چ",
    "hah": "ح", "khah": "خ", "dal": "د", "thal": "ذ", "reh": "ر", "zain": "ز", "jeh": "ژ",
    "seen": "س", "sheen": "ش", "sad": "ص", "dad": "ض", "tah": "ط", "zah": "ظ", "ain": "ع",
    "ghain": "غ", "feh": "ف", "qaf": "ق", "kaf": "ک", "keheh": "ک", "gaf": "گ", "lam": "ل",
    "meem": "م", "noon": "ن", "heh": "ه", "waw": "و", "yeh": "ی", "farsiyeh": "ی",
    "alefmaksura": "ی", "hamza": "ء", "alefmadda": "آ", "alefhamzaabove": "أ",
    "alefhamzabelow": "إ", "wawhamza": "ؤ", "yehhamza": "ئ", "tehmarbuta": "ة",
    "hehgoal": "ہ", "kasra": "\u0650", "fatha": "\u064E", "damma": "\u064F",
    "shadda": "\u0651", "sukun": "\u0652", "tatweel": "", "kashida": "",
}


def glyph_name_to_text(name: str) -> str:
    """Decode a PostScript glyph name (``uni06CC.medi``, ``lam_alef.fina``, ``afii57415``…)."""
    if not name or name in (".notdef", ".null", "nonmarkingreturn"):
        return ""
    base = name.split(".", 1)[0]
    if base in _GLYPH_NAME_ALIASES:
        return _GLYPH_NAME_ALIASES[base]
    pieces: List[str] = []
    for part in base.split("_"):
        if not part:
            continue
        if part in _GLYPH_NAME_ALIASES:
            pieces.append(_GLYPH_NAME_ALIASES[part])
            continue
        m = re.fullmatch(r"uni((?:[0-9A-Fa-f]{4})+)", part)
        if m:
            hexes = m.group(1)
            pieces.append("".join(chr(int(hexes[i:i + 4], 16)) for i in range(0, len(hexes), 4)))
            continue
        m = re.fullmatch(r"u([0-9A-Fa-f]{4,6})", part)
        if m:
            pieces.append(chr(int(m.group(1), 16)))
            continue
        key = re.sub(r"(init|medi|fina|isol|ini|med|fin|iso)$", "", part.lower())
        if key in _ARABIC_GLYPH_WORDS:
            pieces.append(_ARABIC_GLYPH_WORDS[key])
            continue
        if len(part) == 1:
            pieces.append(part)
            continue
        return ""  # unknown component: better to leave the glyph alone than guess wrong
    text = unicodedata.normalize("NFKC", "".join(pieces))
    return text.translate(_ARABIC_TRANS)


def glyph_map_from_font(data: bytes) -> Dict[int, str]:
    """GID → Unicode text for an embedded TrueType/OpenType font program."""
    if not data or len(data) < 12:
        return {}
    if data[:4] == b"%!PS" or data[:1] == b"\x01":  # bare CFF / Type1: no sfnt wrapper
        try:
            names = _parse_cff_charset(data) if data[:1] == b"\x01" else {}
        except Exception:
            names = {}
        return {gid: glyph_name_to_text(n) for gid, n in names.items() if glyph_name_to_text(n)}
    tables = _ttf_tables(data)
    if not tables:
        return {}
    out: Dict[int, str] = {}
    for gid, code in _cmap_reverse(data, tables).items():
        if 0x20 <= code <= 0x10FFFF:
            ch = unicodedata.normalize("NFKC", chr(code)).translate(_ARABIC_TRANS) if 0xFB50 <= code <= 0xFEFF else chr(code)
            out[gid] = ch
    for gid, name in {**_cff_names(data, tables), **_post_names(data, tables)}.items():
        text = glyph_name_to_text(name)
        if text and (gid not in out or len(out[gid]) == 0):
            out[gid] = text
        elif text and gid in out and out[gid] != text and len(text) >= len(out[gid]):
            # names are more precise than cmap for ligature glyphs (lam_alef, allah …)
            if "_" in name or len(text) > 1:
                out[gid] = text
    out.update(_gsub_reverse(data, tables, out))
    return out


# ------------------------------------------------------------- CMap utilities
def _parse_tounicode(stream: bytes) -> Dict[int, str]:
    """Codes already covered by an existing ToUnicode CMap (bfchar + bfrange)."""
    text = stream.decode("latin1", "ignore")
    mapped: Dict[int, str] = {}

    def _hex_to_text(hx: str) -> str:
        hx = hx.strip()
        if len(hx) % 4 == 0 and len(hx) >= 4:
            units = [int(hx[i:i + 4], 16) for i in range(0, len(hx), 4)]
            try:
                return "".join(map(chr, units)).encode("utf-16", "surrogatepass").decode("utf-16")
            except Exception:
                return "".join(chr(u) for u in units)
        try:
            return chr(int(hx, 16))
        except ValueError:
            return ""

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, flags=re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapped[int(src, 16)] = _hex_to_text(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, flags=re.S):
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if hi_i - lo_i > 65535:
                continue
            if dst.startswith("["):
                items = re.findall(r"<([0-9A-Fa-f]*)>", dst)
                for k, item in enumerate(items):
                    mapped[lo_i + k] = _hex_to_text(item)
            else:
                base = _hex_to_text(dst.strip("<>"))
                for k in range(hi_i - lo_i + 1):
                    if base:
                        mapped[lo_i + k] = base[:-1] + chr(ord(base[-1]) + k) if len(base) else ""
    return mapped


def _utf16_hex(text: str) -> str:
    return text.encode("utf-16-be", "surrogatepass").hex().upper()


def _build_cmap(entries: Dict[int, str], two_byte: bool = True) -> bytes:
    width = 4 if two_byte else 2
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo <</Registry (Adobe) /Ordering (UCS) /Supplement 0>> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", f"<{'0' * width}> <{'F' * width}>", "endcodespacerange",
    ]
    items = sorted(entries.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines.extend(f"<{code:0{width}X}> <{_utf16_hex(txt)}>" for code, txt in chunk)
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end", ""]
    return "\n".join(lines).encode("latin1")


def _is_junk_mapping(text: str, *, forms_are_junk: bool = False) -> bool:
    """Mappings that are useless or wrong for Persian script (keeps the good ones)."""
    if not text:
        return True
    if forms_are_junk and PRESENTATION_FORM.search(text):
        return True
    for ch in text:
        code = ord(ch)
        if code == 0xFFFD or 0xE000 <= code <= 0xF8FF or 0x0370 <= code <= 0x052F:
            return True
    return False


@dataclass
class FontRepair:
    xref: int
    name: str
    added: int = 0
    fixed: int = 0
    created: bool = False


def _cid_to_gid(doc, font_xref: int) -> Optional[Dict[int, int]]:
    """Explicit /CIDToGIDMap stream (rare; Identity is the norm)."""
    try:
        kind, value = doc.xref_get_key(font_xref, "DescendantFonts")
        m = re.search(r"(\d+)\s+0\s+R", value or "")
        if kind == "array" and m:
            desc = int(m.group(1))
        else:
            desc = font_xref
        kind, value = doc.xref_get_key(desc, "CIDToGIDMap")
        m = re.search(r"(\d+)\s+0\s+R", value or "")
        if kind == "xref" and m:
            raw = doc.xref_stream(int(m.group(1)))
            return {i: struct.unpack_from(">H", raw, i * 2)[0] for i in range(len(raw) // 2)}
    except Exception:
        return None
    return None


def repair_tounicode(doc, *, aggressive: bool = True, normalize_forms: bool = True) -> List[FontRepair]:
    """Rewrite incomplete ``/ToUnicode`` CMaps of every embedded font in ``doc``.

    Every glyph that the font program can name gets a Unicode mapping; existing
    mappings that point at PUA / Cyrillic junk are replaced when ``aggressive`` is
    True, and mappings to Arabic Presentation Forms are replaced by plain letters
    when ``normalize_forms`` is True (what viewers should copy). Extraction keeps
    the forms (``normalize_forms=False``) because they reveal legacy visual-order
    files. The document is changed in memory only; callers save it. Returns one
    record per touched font.
    """
    repairs: List[FontRepair] = []
    seen: set = set()
    for page in doc:
        try:
            fonts = page.get_fonts(full=True)
        except Exception:
            continue
        for info in fonts:
            xref = info[0]
            if xref in seen or xref <= 0:
                continue
            seen.add(xref)
            try:
                basefont = info[3]
                ext, _ftype, _, data = doc.extract_font(xref)
            except Exception:
                continue
            if not data:
                continue
            gmap = glyph_map_from_font(data)
            if not gmap:
                continue
            kind, value = doc.xref_get_key(xref, "ToUnicode")
            existing: Dict[int, str] = {}
            tu_xref = 0
            if kind == "xref":
                m = re.search(r"(\d+)\s+0\s+R", value or "")
                if m:
                    tu_xref = int(m.group(1))
                    try:
                        existing = _parse_tounicode(doc.xref_stream(tu_xref) or b"")
                    except Exception:
                        existing = {}
            subtype = (doc.xref_get_key(xref, "Subtype")[1] or "").strip("/")
            two_byte = subtype == "Type0"
            cid_map = _cid_to_gid(doc, xref) if two_byte else None
            if two_byte:
                if cid_map:
                    gid_to_code = {gid: cid for cid, gid in cid_map.items()}
                    code_map = {gid_to_code[g]: t for g, t in gmap.items() if g in gid_to_code}
                else:
                    code_map = dict(gmap)
            else:
                # simple fonts: codes are 1 byte; only fix codes whose glyph we can name
                code_map = {}
                try:
                    enc_kind, enc = doc.xref_get_key(xref, "Encoding")
                except Exception:
                    enc_kind, enc = None, None
                if enc_kind in (None, "null") or not existing:
                    continue  # nothing reliable to work with
            merged = dict(existing)
            added = fixed = 0
            for code, text in code_map.items():
                if code not in merged or not merged[code]:
                    merged[code] = text
                    added += 1
                elif aggressive and _is_junk_mapping(merged[code], forms_are_junk=normalize_forms) \
                        and not _is_junk_mapping(text, forms_are_junk=normalize_forms):
                    merged[code] = text
                    fixed += 1
            if not added and not fixed:
                continue
            stream = _build_cmap(merged, two_byte=two_byte)
            record = FontRepair(xref=xref, name=str(basefont), added=added, fixed=fixed)
            try:
                if tu_xref:
                    doc.update_stream(tu_xref, stream)
                else:
                    new_xref = doc.get_new_xref()
                    doc.update_object(new_xref, "<<>>")
                    doc.update_stream(new_xref, stream)
                    doc.xref_set_key(xref, "ToUnicode", f"{new_xref} 0 R")
                    record.created = True
            except Exception:
                continue
            repairs.append(record)
    return repairs


def open_repaired(path_or_bytes, *, ocr_pages: bool = False):
    """Open a PDF, repair font CMaps in memory and return a fresh document.

    MuPDF caches parsed fonts per document, so the repaired bytes are reopened to
    make the new mappings visible to ``get_text``. Falls back to the original when
    nothing needed fixing.
    """
    fitz = _fitz()
    if isinstance(path_or_bytes, (bytes, bytearray)):
        doc = fitz.open("pdf", bytes(path_or_bytes))
    else:
        doc = fitz.open(str(path_or_bytes))
    if doc.is_encrypted:
        try:
            doc.authenticate("")
        except Exception:
            pass
    try:
        repairs = repair_tounicode(doc, normalize_forms=False)
    except Exception:
        repairs = []
    if not repairs:
        return doc
    try:
        data = doc.tobytes(garbage=0, deflate=True)
        doc.close()
        return fitz.open("pdf", data)
    except Exception:
        return doc


# ---------------------------------------------------------- layout extraction
@dataclass
class Span:
    text: str
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    color: str = "#000000"
    font: str = ""


@dataclass
class Paragraph:
    spans: List[Span] = field(default_factory=list)
    rtl: bool = False
    align: str = "start"     # start | center | end
    heading: int = 0         # 0 = body, 1..3 = heading levels
    bullet: bool = False
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    @property
    def size(self) -> float:
        sizes = [span.size for span in self.spans if span.text.strip()]
        return max(sizes) if sizes else 11.0


@dataclass
class Table:
    rows: List[List[str]]
    rtl: bool = False
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class ImageRef:
    data: bytes
    ext: str
    width: int
    height: int
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass
class PageContent:
    number: int
    width: float
    height: float
    items: List[Any] = field(default_factory=list)  # Paragraph | Table | ImageRef in reading order
    rtl: bool = False

    @property
    def paragraphs(self) -> List[Paragraph]:
        return [item for item in self.items if isinstance(item, Paragraph)]

    @property
    def text(self) -> str:
        out: List[str] = []
        for item in self.items:
            if isinstance(item, Paragraph):
                out.append(item.text)
            elif isinstance(item, Table):
                out.extend(" | ".join(cell for cell in row) for row in item.rows)
        return "\n".join(out)


def _color_hex(value: int) -> str:
    try:
        return f"#{int(value) & 0xFFFFFF:06X}"
    except Exception:
        return "#000000"



@dataclass
class _Char:
    c: str
    bbox: Tuple[float, float, float, float]
    size: float
    bold: bool
    italic: bool
    color: str
    font: str


@dataclass
class _Line:
    chars: List[_Char]
    bbox: Tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return "".join(ch.c for ch in self.chars)

    @property
    def size(self) -> float:
        sizes = [ch.size for ch in self.chars if ch.c.strip()]
        return max(sizes) if sizes else 11.0

    @property
    def baseline(self) -> float:
        return self.bbox[3]


def _median(values: Sequence[float], default: float = 11.0) -> float:
    if not values:
        return default
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _bbox_union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _point_in(bbox, x: float, y: float, tolerance: float = 1.5) -> bool:
    return bbox[0] - tolerance <= x <= bbox[2] + tolerance and bbox[1] - tolerance <= y <= bbox[3] + tolerance


def _intervals(spans: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Split overlapping cell extents into the finest non-overlapping grid lines."""
    edges = sorted({v for span in spans for v in span})
    out: List[Tuple[float, float]] = []
    for a, b in zip(edges, edges[1:]):
        if b - a >= 3.0:
            out.append((a, b))
    return out


def _interval_index(intervals: List[Tuple[float, float]], value: float) -> Optional[int]:
    for index, (a, b) in enumerate(intervals):
        if a - 1.5 <= value <= b + 1.5:
            return index
    return None


def _recover_zwnj(chars: List[_Char]) -> None:
    """MuPDF renders ZWNJ as a zero-advance "space" that overlaps its neighbour.

    A real space never overlaps the glyph before it; a ZWNJ "space" does (or has no
    width at all). Turning those back into U+200C keeps «می‌شود» and «کتاب‌ها» intact.
    """
    for i, ch in enumerate(chars):
        if ch.c != " " or i == 0:
            continue
        prev = chars[i - 1]
        x0, _, x1, _ = ch.bbox
        px0, _, px1, _ = prev.bbox
        width = max(0.0, x1 - x0)
        overlap = min(x1, px1) - max(x0, px0)
        if width <= 0.05 or (overlap > 0 and overlap >= 0.45 * width):
            # only meaningful between two joining Persian letters
            nxt = chars[i + 1].c if i + 1 < len(chars) else ""
            if has_persian(prev.c) and has_persian(nxt):
                ch.c = "\u200C"
            elif width <= 0.05:
                ch.c = ""


def _page_lines(page, body_hint: float = 11.0) -> Tuple[List[_Line], List[Dict[str, Any]]]:
    """All text lines of a page with per-character style, plus raw image blocks."""
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return [], []
    lines: List[_Line] = []
    images: List[Dict[str, Any]] = []
    for block in raw.get("blocks", []):
        if block.get("type") == 1:
            images.append(block)
            continue
        for line in block.get("lines", []):
            chars: List[_Char] = []
            for span in line.get("spans", []):
                flags = int(span.get("flags", 0))
                font = str(span.get("font") or "")
                bold = bool(flags & 16) or bool(re.search(r"bold|black|heavy|semibold|extrabold", font, re.I))
                italic = bool(flags & 2) or bool(re.search(r"italic|oblique", font, re.I))
                color = _color_hex(span.get("color", 0))
                size = float(span.get("size", body_hint))
                for item in span.get("chars", []):
                    chars.append(_Char(c=item.get("c", ""), bbox=tuple(float(v) for v in item.get("bbox", (0, 0, 0, 0))),
                                       size=size, bold=bold, italic=italic, color=color, font=font))
            if not chars or not "".join(c.c for c in chars).strip():
                continue
            for fragment in _split_wide_gaps(chars):
                lines.append(_Line(chars=fragment, bbox=_chars_bbox(fragment)))
    return lines, images


_LTR_TOKEN = r"[0-9\u06F0-\u06F9\u0660-\u0669A-Za-z]+(?:[.,:/\u066C\u066B'%\-][0-9\u06F0-\u06F9\u0660-\u0669A-Za-z]+)*"
_LTR_RUN = re.compile(rf"{_LTR_TOKEN}(?: {_LTR_TOKEN})*")


def _merge_legacy_rows(lines: List[_Line]) -> List[_Line]:
    """Glue fragments of one visual row when the row carries presentation forms.

    MuPDF cuts legacy (pre-shaped) RTL lines wherever a number or Latin word
    interrupts the Arabic run; the geometric re-ordering needs the whole row.
    """
    if not any(PRESENTATION_FORM.search(line.text) for line in lines):
        return lines
    ordered = sorted(lines, key=lambda l: (round(l.bbox[3] / 2), l.bbox[0]))
    out: List[_Line] = []
    for line in ordered:
        if out:
            prev = out[-1]
            size = max(line.size, prev.size)
            same_row = abs(line.baseline - prev.baseline) <= 0.35 * size
            legacy = PRESENTATION_FORM.search(prev.text) or PRESENTATION_FORM.search(line.text)
            gap = line.bbox[0] - prev.bbox[2]
            if same_row and legacy and gap <= 0.6 * size:
                prev.chars.extend(line.chars)
                prev.bbox = _bbox_union(prev.bbox, line.bbox)
                continue
        out.append(line)
    return out


def _reorder_legacy_line(line: _Line) -> None:
    """Visual → logical order from glyph geometry for pre-shaped (legacy) PDFs.

    Generators that write Arabic Presentation Forms (ReportLab + arabic_reshaper,
    old Word/Zarnegar exports …) position glyphs visually and have no bidi
    information; MuPDF then only reverses runs between numbers, which scrambles
    word order. Reading the glyphs right-to-left and flipping Latin/number runs
    back restores the true sentence. Modern PDFs never trigger this.
    """
    text = line.text
    if not PRESENTATION_FORM.search(text) or not is_rtl(text):
        return
    _recover_zwnj(line.chars)   # before dropping spaces: a ZWNJ "space" must survive the sweep
    ordered = sorted((ch for ch in line.chars if ch.c.strip()), key=lambda ch: -(ch.bbox[0] + ch.bbox[2]) / 2)
    # re-insert spaces between glyphs that are visibly apart
    rebuilt: List[_Char] = []
    for ch in ordered:
        if rebuilt:
            prev = rebuilt[-1]
            gap = prev.bbox[0] - ch.bbox[2]
            if gap > 0.16 * max(ch.size, prev.size, 1.0):
                rebuilt.append(_Char(" ", (ch.bbox[2], ch.bbox[1], prev.bbox[0], ch.bbox[3]), ch.size, ch.bold, ch.italic, ch.color, ch.font))
        rebuilt.append(ch)
    joined = "".join(ch.c for ch in rebuilt)
    # numbers and Latin words were read backwards by the RTL sweep: flip them back
    flipped = list(rebuilt)
    for match in _LTR_RUN.finditer(joined):
        start, end = match.span()
        flipped[start:end] = reversed(rebuilt[start:end])
    line.chars = flipped
    line.bbox = _chars_bbox(flipped)


def _chars_bbox(chars: List[_Char]) -> Tuple[float, float, float, float]:
    boxes = [c.bbox for c in chars if c.bbox[2] > c.bbox[0]] or [c.bbox for c in chars]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _split_wide_gaps(chars: List[_Char]) -> List[List[_Char]]:
    """Cut a MuPDF line where neighbouring glyphs are far apart (table cells, columns)."""
    fragments: List[List[_Char]] = [[]]
    prev: Optional[_Char] = None
    for ch in chars:
        if prev is not None and ch.c.strip() and prev.c.strip():
            gap = max(ch.bbox[0] - prev.bbox[2], prev.bbox[0] - ch.bbox[2])
            if gap > max(5.0, 0.9 * max(ch.size, prev.size)):
                fragments.append([])
        fragments[-1].append(ch)
        prev = ch if ch.c.strip() else prev
    return [f for f in fragments if "".join(c.c for c in f).strip()]


def _merge_same_baseline(lines: List[_Line]) -> List[_Line]:
    """MuPDF splits a visual line at zero-width glyphs; glue those fragments back."""
    if not lines:
        return lines
    merged: List[_Line] = [lines[0]]
    for line in lines[1:]:
        prev = merged[-1]
        same_row = abs(line.baseline - prev.baseline) <= 0.35 * max(line.size, prev.size)
        if not same_row:
            merged.append(line)
            continue
        # horizontal gap between the fragments (in either direction); a split caused by a
        # zero-width glyph leaves (almost) no gap, a neighbouring table cell/column does
        gap = max(line.bbox[0] - prev.bbox[2], prev.bbox[0] - line.bbox[2])
        size = max(line.size, prev.size)
        if gap > 0.4 * size:
            merged.append(line)
            continue
        if gap > 0.12 * size and prev.chars and line.chars and prev.chars[-1].c.strip() and line.chars[0].c.strip():
            last = prev.chars[-1]
            prev.chars.append(_Char(" ", last.bbox, last.size, last.bold, last.italic, last.color, last.font))
        prev.chars.extend(line.chars)
        prev.bbox = _bbox_union(prev.bbox, line.bbox)
    return merged


def _spans_from_chars(chars: List[_Char]) -> List[Span]:
    spans: List[Span] = []
    for ch in chars:
        if not ch.c:
            continue
        key = (ch.bold, ch.italic, ch.color, round(ch.size, 1))
        if spans and (spans[-1].bold, spans[-1].italic, spans[-1].color, round(spans[-1].size, 1)) == key:
            spans[-1].text += ch.c
        else:
            spans.append(Span(text=ch.c, size=ch.size, bold=ch.bold, italic=ch.italic, color=ch.color, font=ch.font))
    return spans


_BULLET_RE = re.compile(r"^\s*([•·▪●■◦‣]|[-–—*]|\(?\d{1,3}[.)]|\(?[۰-۹]{1,3}[.)])\s+")


def extract_page(doc, index: int, *, tables: bool = True, images: bool = False,
                 normalize: bool = True) -> PageContent:
    """Structured reading-order content of one page (paragraphs, tables, images).

    Works line-first instead of trusting MuPDF blocks: Persian PDFs frequently come
    with one block per line (or per word), so paragraphs are rebuilt from geometry —
    gaps, font-size jumps, short last lines, bullets and alignment.
    """
    page = doc[index]
    rect = page.rect
    content = PageContent(number=index + 1, width=float(rect.width), height=float(rect.height))
    lines, image_blocks = _page_lines(page)
    lines = _merge_legacy_rows(lines)
    for line in lines:
        _reorder_legacy_line(line)
    body = _median([line.size for line in lines])

    # ------------------------------------------------------------------ tables
    table_specs: List[Tuple[Tuple[float, float, float, float], List[Tuple[float, float]], List[Tuple[float, float]]]] = []
    if tables and lines:
        try:
            finder = page.find_tables()
            for table in getattr(finder, "tables", []):
                try:
                    cells = [tuple(float(v) for v in cell) for row in table.rows for cell in row.cells if cell]
                except Exception:
                    continue
                if len(cells) < 4:
                    continue
                col_edges = _intervals(sorted({(round(c[0], 1), round(c[2], 1)) for c in cells}))
                row_edges = _intervals(sorted({(round(c[1], 1), round(c[3], 1)) for c in cells}))
                if len(col_edges) < 2 or len(row_edges) < 2:
                    continue
                table_specs.append((tuple(float(v) for v in table.bbox), row_edges, col_edges))
        except Exception:
            table_specs = []

    table_cells: List[List[List[List[_Line]]]] = [
        [[[] for _ in cols] for _ in rows] for _bbox, rows, cols in table_specs
    ]
    free_lines: List[_Line] = []
    for line in lines:
        cx = (line.bbox[0] + line.bbox[2]) / 2
        cy = (line.bbox[1] + line.bbox[3]) / 2
        placed = False
        for t_index, (tbbox, rows, cols) in enumerate(table_specs):
            if not _point_in(tbbox, cx, cy):
                continue
            r_index = _interval_index(rows, cy)
            c_index = _interval_index(cols, cx)
            if r_index is not None and c_index is not None:
                table_cells[t_index][r_index][c_index].append(line)
                placed = True
                break
        if not placed:
            free_lines.append(line)

    free_lines = _merge_same_baseline(free_lines)
    for line in free_lines:
        _recover_zwnj(line.chars)

    items: List[Tuple[float, float, Any]] = []
    for t_index, (tbbox, _rows, _cols) in enumerate(table_specs):
        grid: List[List[str]] = []
        for row_lines in table_cells[t_index]:
            for c_index, cell in enumerate(row_lines):
                merged_cell = _merge_same_baseline(sorted(cell, key=lambda l: (round(l.bbox[3]), l.bbox[0])))
                for line in merged_cell:
                    _recover_zwnj(line.chars)
                row_lines[c_index] = merged_cell
            grid.append([_spans_text(sorted(cell, key=lambda l: l.bbox[1]), normalize).strip() for cell in row_lines])
        if not any(any(cell for cell in row) for row in grid):
            continue
        grid = _compact_grid(grid)
        joined = " ".join(" ".join(row) for row in grid)
        table_rtl = is_rtl(joined)
        if table_rtl:
            grid = [list(reversed(row)) for row in grid]  # cells were collected left→right
        items.append((tbbox[1], tbbox[0], Table(rows=grid, rtl=table_rtl, bbox=tbbox)))

    # ------------------------------------------------------------------ images
    if images:
        for block in image_blocks:
            try:
                data = block.get("image")
                if data:
                    bbox = tuple(float(v) for v in block.get("bbox", (0, 0, 0, 0)))
                    if (bbox[2] - bbox[0]) < 12 or (bbox[3] - bbox[1]) < 12:
                        continue
                    items.append((bbox[1], bbox[0], ImageRef(
                        data=bytes(data), ext=str(block.get("ext") or "png"),
                        width=int(block.get("width") or 0), height=int(block.get("height") or 0), bbox=bbox)))
            except Exception:
                continue

    # -------------------------------------------------------------- paragraphs
    free_lines.sort(key=lambda l: (round(l.bbox[1] / 3), l.bbox[0]))
    text_left = min((l.bbox[0] for l in free_lines), default=0.0)
    text_right = max((l.bbox[2] for l in free_lines), default=content.width)
    column_w = max(60.0, text_right - text_left)
    rtl_votes = 0
    current: Optional[Paragraph] = None
    current_chars: List[_Char] = []
    prev: Optional[_Line] = None

    def _flush() -> None:
        nonlocal current, current_chars
        if current is None:
            return
        current.spans = _spans_from_chars(current_chars)
        current_chars = []
        current = None

    for line in free_lines:
        text = line.text
        line_rtl = is_rtl(text)
        rtl_votes += 1 if line_rtl else -1
        size = line.size
        start_new = current is None
        if prev is not None and current is not None:
            gap = line.bbox[1] - prev.bbox[3]
            prev_w = prev.bbox[2] - prev.bbox[0]
            size_jump = abs(prev.size - size) > max(0.9, 0.12 * body)
            big_gap = gap > 0.65 * max(size, prev.size)
            short_prev = prev_w < 0.72 * column_w and (line.bbox[2] - line.bbox[0]) > prev_w * 0.9
            heading_like = size >= body * 1.22 or prev.size >= body * 1.22
            bullet_start = bool(_BULLET_RE.match(text))
            dir_change = line_rtl != is_rtl(prev.text) and bool(prev.text.strip()) and \
                (has_persian(text) != has_persian(prev.text))
            if size_jump or big_gap or short_prev or heading_like or bullet_start or dir_change:
                start_new = True
        if start_new:
            _flush()
            current = Paragraph(rtl=line_rtl, bbox=line.bbox)
            items.append((line.bbox[1], line.bbox[0], current))
            lw = line.bbox[2] - line.bbox[0]
            centre_off = abs((line.bbox[0] + line.bbox[2]) / 2 - (text_left + text_right) / 2)
            if lw < 0.75 * column_w and centre_off < 0.06 * column_w and column_w > 150:
                current.align = "center"
            elif lw < 0.75 * column_w:
                near_left = abs(line.bbox[0] - text_left) < 0.04 * column_w
                near_right = abs(text_right - line.bbox[2]) < 0.04 * column_w
                if line_rtl and near_left and not near_right:
                    current.align = "end"
                elif not line_rtl and near_right and not near_left:
                    current.align = "end"
        else:
            if current_chars and current_chars[-1].c not in (" ", "\u200C", "-", "\u00AD"):
                last = current_chars[-1]
                current_chars.append(_Char(" ", last.bbox, last.size, last.bold, last.italic, last.color, last.font))
            assert current is not None
            current.bbox = _bbox_union(current.bbox, line.bbox)
        current_chars.extend(line.chars)
        prev = line
    _flush()

    content.rtl = rtl_votes > 0
    items.sort(key=lambda t: (round(t[0] / 4), -t[1] if content.rtl else t[1]))
    for _, _, item in items:
        if isinstance(item, Paragraph):
            for span in item.spans:
                span.text = normalize_persian(span.text, fix_reversed=True) if normalize else span.text
            item.spans = [s for s in item.spans if s.text]
            if not item.spans:
                continue
            item.spans[0].text = item.spans[0].text.lstrip()
            item.spans[-1].text = item.spans[-1].text.rstrip()
            item.rtl = is_rtl(item.text)
            size = item.size
            words = len(item.text.split())
            all_bold = all(s.bold for s in item.spans if s.text.strip())
            if size >= body * 1.55 and words <= 20:
                item.heading = 1
            elif size >= body * 1.25 and words <= 24:
                item.heading = 2
            elif size >= body * 1.1 and words <= 16 and all_bold:
                item.heading = 3
            match = _BULLET_RE.match(item.text)
            if match and not item.heading and words > 1:
                item.bullet = True
                first = item.spans[0]
                first.text = _BULLET_RE.sub("", first.text, count=1) if _BULLET_RE.match(first.text) else first.text
        content.items.append(item)
    return content


def _compact_grid(grid: List[List[str]]) -> List[List[str]]:
    """Collapse the fine-grained grid: drop empty rows/columns and fuse split columns.

    Two neighbouring columns are fused when no row uses both — that is what a header
    cell straddling two body columns (or a slightly misaligned ruler) looks like.
    """
    width = max((len(row) for row in grid), default=0)
    grid = [[(row[c] if c < len(row) else "") for c in range(width)] for row in grid]
    grid = [row for row in grid if any(cell.strip() for cell in row)]
    if not grid:
        return grid
    changed = True
    while changed and width > 1:
        changed = False
        for c in range(width - 1):
            if not any(row[c].strip() and row[c + 1].strip() for row in grid):
                for row in grid:
                    row[c] = (row[c] + " " + row[c + 1]).strip()
                    del row[c + 1]
                width -= 1
                changed = True
                break
    return grid


def _spans_text(cell_lines: List[_Line], normalize: bool) -> str:
    text = " ".join(line.text.strip() for line in cell_lines)
    return normalize_persian(text) if normalize else text

def extract_text(doc, *, per_page: bool = False, normalize: bool = True) -> List[str]:
    """Clean logical-order text per page (uses the repaired document)."""
    pages: List[str] = []
    for index in range(doc.page_count):
        try:
            content = extract_page(doc, index, tables=False, images=False, normalize=normalize)
            text = content.text
        except Exception:
            text = doc[index].get_text("text")
            if normalize:
                text = normalize_persian(text)
        pages.append(text)
    return pages


def pdf_text(path: Path | str, *, normalize: bool = True) -> str:
    """One-call helper: full text of a PDF with every Persian fix applied."""
    doc = open_repaired(path)
    try:
        return "\n\n".join(extract_text(doc, normalize=normalize))
    finally:
        doc.close()


# ------------------------------------------------------------------ rendering
def html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def paragraph_html(text: str, *, tag: str = "p", align: Optional[str] = None,
                   bold: bool = False, size_pt: Optional[float] = None, color: Optional[str] = None) -> str:
    rtl = is_rtl(text)
    style = []
    if align:
        style.append(f"text-align:{align}")
    else:
        style.append("text-align:right" if rtl else "text-align:left")
    if size_pt:
        style.append(f"font-size:{size_pt:.1f}pt")
    if color:
        style.append(f"color:{color}")
    body = html_escape(text).replace("\n", "<br/>")
    if bold:
        body = f"<b>{body}</b>"
    return f'<{tag} dir="{"rtl" if rtl else "ltr"}" style="{";".join(style)}">{body}</{tag}>'


def text_to_html(text: str, *, size_pt: float = 11.0) -> str:
    """Plain multi-line text → HTML paragraphs with per-paragraph direction."""
    parts = []
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        lines = [line for line in block.split("\n")]
        if not any(line.strip() for line in lines):
            continue
        parts.append(paragraph_html("\n".join(lines), size_pt=size_pt))
    return "".join(parts) or "<p>&nbsp;</p>"


_BLOCK_TAG_RE = re.compile(r"<(p|h[1-6]|li|td|th|blockquote|div)(\s[^>]*)?>(.*?)</\1>", re.S | re.I)


def direction_html(html: str) -> str:
    """Give every block element a ``dir``/``text-align`` that matches its own text.

    Markdown converters emit direction-less HTML; MuPDF's layout engine then
    renders Persian paragraphs left-aligned with trailing punctuation on the wrong
    side. This keeps mixed documents (Persian + English) correct per paragraph.
    """
    def _fix(match: re.Match) -> str:
        tag, attrs, inner = match.group(1), match.group(2) or "", match.group(3)
        if re.search(r"\bdir\s*=", attrs, re.I):
            return match.group(0)
        plain = re.sub(r"<[^>]+>", "", inner)
        if not plain.strip():
            return match.group(0)
        rtl = is_rtl(plain)
        align = "right" if rtl else "left"
        if re.search(r"text-align", attrs, re.I):
            style = ""
        else:
            style = f' style="text-align:{align}"'
        return f'<{tag}{attrs} dir="{"rtl" if rtl else "ltr"}"{style}>{inner}</{tag}>'
    out = _BLOCK_TAG_RE.sub(_fix, html)
    # tables: mirror column order for RTL tables (MuPDF lays tables out LTR only)
    def _table(match: re.Match) -> str:
        table = match.group(0)
        plain = re.sub(r"<[^>]+>", " ", table)
        if not is_rtl(plain):
            return table
        def _row(row_match: re.Match) -> str:
            cells = re.findall(r"<t[dh]\b.*?</t[dh]>", row_match.group(2), flags=re.S | re.I)
            if len(cells) < 2:
                return row_match.group(0)
            return f"{row_match.group(1)}{''.join(reversed(cells))}{row_match.group(3)}"
        return re.sub(r"(<tr\b[^>]*>)(.*?)(</tr>)", _row, table, flags=re.S | re.I)
    return re.sub(r"<table\b.*?</table>", _table, out, flags=re.S | re.I)


def insert_html(page, rect, html: str, *, css: Optional[str] = None, size_pt: float = 11.0,
                color: str = "#111111", opacity: float = 1.0, rotate: int = 0, scale_low: float = 0.0):
    """HarfBuzz-shaped HTML into a rectangle with the bundled fonts. Returns (spare_height, scale)."""
    archive = font_archive()
    style = css if css is not None else font_css(size_pt, color=color)
    kwargs: Dict[str, Any] = {"css": style, "opacity": opacity, "rotate": rotate, "scale_low": scale_low}
    if archive is not None:
        kwargs["archive"] = archive
    return page.insert_htmlbox(rect, html, **kwargs)


def flow_html_pages(doc, html: str, *, css: Optional[str] = None, size_pt: float = 11.0,
                    page_size: Tuple[float, float] = (595.0, 842.0), margin: float = 48.0,
                    color: str = "#111111") -> int:
    """Lay out long HTML across as many new pages as needed (MuPDF Story)."""
    fitz = _fitz()
    archive = font_archive()
    style = css if css is not None else font_css(size_pt, color=color)
    story = fitz.Story(html=html, user_css=style, archive=archive) if archive is not None else fitz.Story(html=html, user_css=style)
    width, height = page_size
    mediabox = fitz.Rect(0, 0, width, height)
    where = fitz.Rect(margin, margin, width - margin, height - margin)
    buffer = io.BytesIO()
    writer = fitz.DocumentWriter(buffer)
    pages = 0
    more = True
    while more and pages < 2000:
        device = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1
    writer.close()
    buffer.seek(0)
    rendered = fitz.open("pdf", buffer.read())
    doc.insert_pdf(rendered)
    rendered.close()
    return pages


def stamp_text(page, text: str, *, size_pt: float = 48.0, color: str = "#8a8a99", opacity: float = 0.14,
               angle: float = 45.0, bold: bool = True) -> None:
    """Diagonal watermark-style stamp with correct Persian shaping (any angle)."""
    import math

    fitz = _fitz()
    text = text.strip() or " "
    # measure the line with the real font, then render it on a tight temporary page
    try:
        measure = fitz.Font(fontfile=str(font_path("bold" if bold else "regular")))
        text_w = float(measure.text_length(text, fontsize=size_pt))
    except Exception:
        text_w = size_pt * 0.62 * len(text)
    est_w = max(60.0, text_w * 1.08 + size_pt)
    est_h = size_pt * 1.9
    stamp = fitz.open()
    sp = stamp.new_page(width=est_w, height=est_h)
    weight = "bold" if bold else "normal"
    direction = "rtl" if is_rtl(text) else "ltr"
    html = (f'<div dir="{direction}" style="text-align:center; white-space:nowrap; font-weight:{weight};">'
            f'{html_escape(text)}</div>')
    css = font_css(size_pt, color=color, extra="body{line-height:1.4;}")
    insert_html(sp, sp.rect, html, css=css, opacity=opacity, scale_low=0.0)
    rect = page.rect
    W, H = rect.width, rect.height
    # show_pdf_page fits the *rotated* stamp into the target rect: size it accordingly
    rad = math.radians(angle)
    rot_w = est_w * abs(math.cos(rad)) + est_h * abs(math.sin(rad))
    rot_h = est_w * abs(math.sin(rad)) + est_h * abs(math.cos(rad))
    scale = min(1.0, 0.92 * W / rot_w, 0.92 * H / rot_h)
    draw_w, draw_h = rot_w * scale, rot_h * scale
    cx, cy = W / 2, H / 2
    target = fitz.Rect(cx - draw_w / 2, cy - draw_h / 2, cx + draw_w / 2, cy + draw_h / 2)
    page.show_pdf_page(target, stamp, 0, rotate=-angle)
    stamp.close()


def stamp_line(page, text: str, *, position: str = "bottom-center", size_pt: float = 10.0,
               color: str = "#595966", margin: float = 18.0) -> None:
    """Header/footer style single-line stamp (page numbers, labels) with RTL support."""
    fitz = _fitz()
    rect = page.rect
    height = size_pt * 2.2
    if position.startswith("top"):
        box = fitz.Rect(margin, margin * 0.6, rect.width - margin, margin * 0.6 + height)
    else:
        box = fitz.Rect(margin, rect.height - margin * 0.6 - height, rect.width - margin, rect.height - margin * 0.6)
    align = "center"
    if position.endswith("left"):
        align = "left"
    elif position.endswith("right"):
        align = "right"
    html = (f'<div dir="{"rtl" if is_rtl(text) else "ltr"}" style="text-align:{align}; white-space:nowrap;">'
            f'{html_escape(text)}</div>')
    insert_html(page, box, html, css=font_css(size_pt, color=color, extra="body{line-height:1.2;}"))


def finalize(doc) -> List[FontRepair]:
    """Call before saving any PDF written with the bundled fonts: makes copy/paste clean."""
    try:
        return repair_tounicode(doc)
    except Exception:
        return []


def save_pdf(doc, dest: Path | str, *, repair: bool = True, **kwargs) -> Path:
    """Repair CMaps and save (incremental-safe defaults)."""
    if repair:
        finalize(doc)
    options = {"garbage": 3, "deflate": True}
    options.update(kwargs)
    doc.save(str(dest), **options)
    return Path(str(dest))


def pdf_bytes(doc, *, repair: bool = True) -> bytes:
    if repair:
        finalize(doc)
    return doc.tobytes(garbage=3, deflate=True)


# ------------------------------------------------------------ DOCX helpers
def docx_set_rtl(paragraph, rtl: bool = True, *, font: str = FONT_FAMILY) -> None:
    """Make a python-docx paragraph real RTL (w:bidi) with complex-script font on every run."""
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore

    p_pr = paragraph._p.get_or_add_pPr()
    if rtl:
        for tag in ("w:bidi",):
            if p_pr.find(qn(tag)) is None:
                p_pr.append(OxmlElement(tag))
    for run in paragraph.runs:
        docx_style_run(run, rtl=rtl, font=font)


def docx_style_run(run, *, rtl: bool, font: str = FONT_FAMILY, size_pt: Optional[float] = None,
                   bold: Optional[bool] = None, italic: Optional[bool] = None, color: Optional[str] = None) -> None:
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore
    from docx.shared import Pt, RGBColor  # type: ignore

    r_pr = run._r.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), font)
    if rtl and r_pr.find(qn("w:rtl")) is None:
        r_pr.append(OxmlElement("w:rtl"))
    if size_pt:
        run.font.size = Pt(round(size_pt * 2) / 2)
        cs_size = r_pr.find(qn("w:szCs"))
        if cs_size is None:
            cs_size = OxmlElement("w:szCs")
            r_pr.append(cs_size)
        cs_size.set(qn("w:val"), str(int(round(size_pt * 2))))
    if bold is not None:
        run.font.bold = bold
        if bold and r_pr.find(qn("w:bCs")) is None:
            r_pr.append(OxmlElement("w:bCs"))
    if italic is not None:
        run.font.italic = italic
    if color and color.upper() not in ("#000000", "#000"):
        try:
            run.font.color.rgb = RGBColor.from_string(color.lstrip("#").upper()[:6])
        except Exception:
            pass


def docx_table_rtl(table) -> None:
    """Right-to-left column order (w:bidiVisual) for a python-docx table."""
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore

    tbl_pr = table._tbl.tblPr
    if tbl_pr.find(qn("w:bidiVisual")) is None:
        tbl_pr.append(OxmlElement("w:bidiVisual"))


def docx_prepare(document, *, font: str = FONT_FAMILY, size_pt: float = 11.0) -> None:
    """Default style: bundled Persian-friendly font for Latin *and* complex scripts."""
    from docx.oxml import OxmlElement  # type: ignore
    from docx.oxml.ns import qn  # type: ignore
    from docx.shared import Pt  # type: ignore

    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3", "Title", "List Bullet"):
        try:
            style = document.styles[style_name]
        except KeyError:
            continue
        style.font.name = font
        if style_name == "Normal":
            style.font.size = Pt(size_pt)
        r_pr = style.element.get_or_add_rPr()
        fonts = r_pr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            r_pr.insert(0, fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), font)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            if fonts.get(qn(attr)) is not None:
                del fonts.attrib[qn(attr)]


__all__ = [
    "FONT_FAMILY", "fonts_dir", "font_path", "fonts_available", "font_css", "font_archive",
    "is_rtl", "has_persian", "to_persian_digits", "to_latin_digits", "strip_diacritics",
    "normalize_presentation_forms", "visual_to_logical", "fix_mirrored_brackets", "smart_zwnj", "normalize_persian",
    "persian_sort_key", "glyph_map_from_font", "glyph_name_to_text", "repair_tounicode",
    "open_repaired", "extract_page", "extract_text", "pdf_text", "Span", "Paragraph", "Table",
    "ImageRef", "PageContent", "html_escape", "paragraph_html", "text_to_html", "direction_html", "insert_html",
    "flow_html_pages", "stamp_text", "stamp_line", "finalize", "save_pdf", "pdf_bytes",
    "docx_set_rtl", "docx_style_run", "docx_table_rtl", "docx_prepare",
]
