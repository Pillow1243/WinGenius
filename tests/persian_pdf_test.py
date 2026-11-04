"""Persian / RTL PDF engine regressions (pytest).

Covers the three historic failure modes of Persian PDFs in WinGenius:

* unmapped OpenType glyphs leaking as Cyrillic junk (``ѫ ѱ ӳ``) from ``/ToUnicode``,
* presentation forms + visual (reversed) order from legacy generators,
* tofu when *writing* Persian (watermarks, page numbers, OCR layers, Word → PDF).

Everything runs offline with the bundled Vazirmatn fonts; no Office, no Tesseract.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app.core import persian_pdf as pp
from app.core.spec import RunContext
from app.tools import load_tools

load_tools()
from app.core.registry import get as get_tool  # noqa: E402

CYRILLIC = re.compile(r"[\u0370-\u052F]")
FORMS = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")

SAMPLE_HTML = """
<h1 dir="rtl" style="text-align:right">گزارش فصلی وین‌جنیوس</h1>
<p dir="rtl" style="text-align:right">این سند برای آزمایش استخراج متن فارسی ساخته شده است. کتاب‌ها، خانه‌های
بزرگ‌تر و واژه‌هایی که می‌شود و نمی‌توان را شامل می‌شود؛ همچنین «نقل‌قول» و اعداد ۱۲۳٬۴۵۶ و تاریخ ۱۴۰۴/۰۸/۱۲
در آن هست. ضمناً English words like PyMuPDF and version 7.0.0 appear too.</p>
<h2 dir="rtl" style="text-align:right">جدول نمونه</h2>
<table style="width:100%"><tr><th>وضعیت</th><th>تعداد</th><th>نام</th></tr>
<tr><td>تأیید شده</td><td>۱۲</td><td>فاکتور فروش</td></tr>
<tr><td>در انتظار</td><td>3</td><td>Invoice</td></tr></table>
<p dir="rtl" style="text-align:right"><b>نکته‌ی مهم:</b> لام‌الف مثل «سلام» و تشدید مثل «مشدّد» باید درست بمانند.</p>
<p dir="ltr" style="text-align:left">An English paragraph that wraps onto several lines so we can verify that the
paragraph merger joins the lines with spaces instead of emitting one paragraph per visual line.</p>
"""

# Pre-shaped, visually ordered strings exactly as ReportLab + arabic_reshaper + bidi emit them.
LEGACY_LINES = (
    ("ﻥﺎﯿﻨﺑﺶﻧﺍﺩ ﺖﮐﺮﺷ — ۱۲۳۴ ﻩﺭﺎﻤﺷ ﺏﺎﺴﺣﺕﺭﻮﺻ", 18, "Vazirmatn-Bold.ttf"),
    (".ﺪﺷﺎﺑﯽﻣ ﻝﺎﯾﺭ ۲٬۵۰۰٬۰۰۰ ﻞﮐ ﻎﻠﺒﻣ", 12, "Vazirmatn-Regular.ttf"),
)


def _ctx(tmp_path: Path, lang: str = "fa") -> RunContext:
    return RunContext(lang=lang, output_dir=str(tmp_path))


def _run(tmp_path: Path, tool_id: str, **values):
    return get_tool(tool_id).run(_ctx(tmp_path), values)


@pytest.fixture(scope="module")
def fitz():
    return pytest.importorskip("pymupdf")


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory, fitz) -> Path:
    path = tmp_path_factory.mktemp("persian") / "sample.pdf"
    doc = fitz.open()
    pp.flow_html_pages(doc, SAMPLE_HTML, size_pt=12)
    pp.save_pdf(doc, path)
    doc.close()
    return path


@pytest.fixture(scope="module")
def unrepaired_pdf(tmp_path_factory, fitz) -> Path:
    """A PDF written by raw MuPDF: its /ToUnicode lacks the shaped glyphs."""
    path = tmp_path_factory.mktemp("persian") / "unrepaired.pdf"
    doc = fitz.open()
    page = doc.new_page()
    css = "@font-face{font-family:V;src:url(Vazirmatn-Regular.ttf);} body{font-family:V;font-size:16pt}"
    page.insert_htmlbox(fitz.Rect(40, 40, 555, 300), "<p dir=rtl>وین‌جنیوس می‌شود کتابخانه</p>",
                        css=css, archive=fitz.Archive(str(pp.fonts_dir())))
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def legacy_pdf(tmp_path_factory) -> Path:
    """Legacy visual-order PDF (presentation forms) as produced by ReportLab pipelines."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    _ = reportlab
    path = tmp_path_factory.mktemp("persian") / "legacy.pdf"
    for name in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"):
        pdfmetrics.registerFont(TTFont(name, str(pp.fonts_dir() / name)))
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    y = 780
    for text, size, font in LEGACY_LINES:
        c.setFont(font, size)
        c.drawRightString(545, y, text)
        y -= 30
    c.setFont("Helvetica", 12)
    c.drawString(50, y, "Invoice total: 2,500,000 IRR")
    c.showPage()
    c.save()
    return path


# ------------------------------------------------------------------ text layer
def test_fonts_are_bundled():
    assert pp.fonts_available()
    assert (pp.fonts_dir() / "OFL.txt").exists()


def test_normalize_presentation_forms_and_arabic_letters():
    assert pp.normalize_persian("ﮐﺘﺎﺑﺨﺎﻧﻪ") == "کتابخانه"
    assert pp.normalize_persian("كتاب هاي من") == "کتاب‌های من"
    assert "ي" not in pp.normalize_persian("علي") and pp.normalize_persian("علي") == "علی"
    assert pp.normalize_persian("ﻻ") == "لا"


def test_smart_zwnj_rules():
    fixed = pp.smart_zwnj("می شود و نمی توانم و کتاب ها و بزرگ تر و خانه ام و میباشد")
    assert "می\u200cشود" in fixed and "نمی\u200cتوانم" in fixed
    assert "کتاب\u200cها" in fixed and "بزرگ\u200cتر" in fixed and "خانه\u200cام" in fixed
    assert "می\u200cباشد" in fixed
    # nouns that merely start with «می» stay untouched
    assert pp.smart_zwnj("میز بزرگ و میدان شهر") == "میز بزرگ و میدان شهر"
    # a ZWNJ after a non-joining letter is useless and removed
    assert pp.smart_zwnj("در\u200cها") == "درها"


def test_mirrored_brackets_are_restored():
    assert pp.fix_mirrored_brackets("همچنین »نقل قول« و »سلام«") == "همچنین «نقل قول» و «سلام»"
    assert pp.fix_mirrored_brackets("call f(x) and g(y)") == "call f(x) and g(y)"


def test_visual_to_logical_for_reversed_lines():
    reversed_line = "ﺏﺎﺴﺣﺕﺭﻮﺻ"  # "صورتحساب" stored backwards
    assert pp.looks_reversed(reversed_line)
    assert pp.normalize_persian(reversed_line) == "صورتحساب"


def test_persian_sort_key_collation():
    lines = ["ب", "آ", "پ", "الف", "12", "9", "یزد", "كرج", "اَصفهان", "اهواز"]
    ordered = sorted(lines, key=pp.persian_sort_key)
    assert ordered[:2] == ["9", "12"]
    assert ordered.index("آ") < ordered.index("ب") < ordered.index("پ")
    assert ordered.index("اَصفهان") < ordered.index("اهواز") < ordered.index("ب")
    assert ordered.index("كرج") < ordered.index("یزد")


def test_glyph_names_decode_to_text():
    assert pp.glyph_name_to_text("uni06CC.medi") == "ی"
    assert pp.glyph_name_to_text("uniFBFE.long1") == "ی"
    assert pp.glyph_name_to_text("uniFEE3.long") == "م"
    assert pp.glyph_name_to_text("lam_alef.fina") == "لا"
    assert pp.glyph_name_to_text("afii57415") == "ا"
    assert pp.glyph_name_to_text(".notdef") == ""


def test_glyph_map_from_bundled_font_covers_shaped_glyphs():
    data = pp.font_path("regular").read_bytes()
    gmap = pp.glyph_map_from_font(data)
    assert len(gmap) > 600
    # 0x046B is the contextual yeh glyph that leaked as "ѫ" before the fix
    assert gmap.get(0x046B) == "ی"
    assert all(not CYRILLIC.search(text) for text in gmap.values())


# ------------------------------------------------------------- ToUnicode repair
def test_generated_pdf_extracts_cleanly_in_any_reader(sample_pdf, fitz):
    doc = fitz.open(str(sample_pdf))
    text = doc[0].get_text("text")
    doc.close()
    assert not CYRILLIC.search(text), text[:120]
    assert not FORMS.search(text), text[:120]
    assert "گزارش فصلی" in text and "کتابخانه" not in text  # sanity: repaired, not hallucinated


def test_unrepaired_pdf_is_fixed_by_open_repaired(unrepaired_pdf, fitz):
    raw = fitz.open(str(unrepaired_pdf))
    before = raw[0].get_text("text")
    raw.close()
    assert CYRILLIC.search(before), "fixture should reproduce the ѫ/ѱ leak"
    doc = pp.open_repaired(unrepaired_pdf)
    content = pp.extract_page(doc, 0)
    doc.close()
    assert content.text == "وین\u200cجنیوس می\u200cشود کتابخانه"


def test_pdf_fix_persian_tool(unrepaired_pdf, tmp_path, fitz):
    result = _run(tmp_path, "pdf_fix_persian", src=str(unrepaired_pdf), preview=True,
                  out=str(tmp_path / "fixed.pdf"))
    fixed = fitz.open(result.outputs[0])
    text = fixed[0].get_text("text")
    fixed.close()
    assert not CYRILLIC.search(text) and not FORMS.search(text)
    assert "کتابخانه" in text and "وین" in text          # any reader now copies real letters
    assert "می\u200cشود" in pp.pdf_text(result.outputs[0])  # WinGenius keeps the half-space too
    assert "ѫ" in result.details and "— بعد —" in result.details


# ------------------------------------------------------------ layout extraction
def test_extract_page_rebuilds_paragraphs_headings_tables(sample_pdf):
    doc = pp.open_repaired(sample_pdf)
    content = pp.extract_page(doc, 0, tables=True)
    doc.close()
    assert content.rtl
    paragraphs = content.paragraphs
    headings = [p for p in paragraphs if p.heading]
    assert [h.text for h in headings] == ["گزارش فصلی وین\u200cجنیوس", "جدول نمونه"]
    body = next(p for p in paragraphs if p.text.startswith("این سند"))
    assert "کتاب\u200cها" in body.text and "می\u200cشود" in body.text and "«نقل\u200cقول»" in body.text
    assert "۱۲۳٬۴۵۶ و تاریخ ۱۴۰۴/۰۸/۱۲" in body.text
    assert body.text.endswith("appear too.")
    english = next(p for p in paragraphs if p.text.startswith("An English"))
    assert not english.rtl and english.text.endswith("per visual line.")
    assert sum(1 for p in paragraphs if p.text.startswith("An English")) == 1
    tables = [item for item in content.items if isinstance(item, pp.Table)]
    assert len(tables) == 1
    table = tables[0]
    assert table.rtl and table.rows[0] == ["نام", "تعداد", "وضعیت"]
    assert table.rows[1] == ["فاکتور فروش", "۱۲", "تأیید شده"]
    bold = next(p for p in paragraphs if p.text.startswith("نکته"))
    assert bold.spans[0].bold and "نکته\u200cی مهم" in bold.spans[0].text


def test_legacy_visual_order_pdf_reads_logically(legacy_pdf):
    doc = pp.open_repaired(legacy_pdf)
    content = pp.extract_page(doc, 0, tables=False)
    doc.close()
    texts = [p.text for p in content.paragraphs]
    assert texts[0] == "صورتحساب شماره ۱۲۳۴ — شرکت دانشبنیان"
    assert texts[1] == "مبلغ کل ۲٬۵۰۰٬۰۰۰ ریال می\u200cباشد."
    assert texts[2] == "Invoice total: 2,500,000 IRR"


def test_pdf_to_text_tool(sample_pdf, tmp_path):
    result = _run(tmp_path, "pdf_to_text", src=str(sample_pdf), per_page=False, digits="en",
                  out=str(tmp_path / "out.txt"))
    text = Path(result.outputs[0]).read_text(encoding="utf-8")
    assert "123,456" not in text and "123٬456" in text  # digits converted, separator kept
    assert "کتاب\u200cها" in text
    assert "فاکتور فروش | 12 | تأیید شده" in text


def test_pdf_find_text_is_persian_aware(sample_pdf, tmp_path):
    found = _run(tmp_path, "pdf_find_text", src=str(sample_pdf), query="كتاب ها", ignore_case=True)
    assert "1" in found.message or "۱" in found.message
    missing = _run(tmp_path, "pdf_find_text", src=str(sample_pdf), query="واژه‌ای که نیست", ignore_case=True)
    assert "پیدا نشد" in missing.message


def test_pdf_to_markdown_and_html(sample_pdf, tmp_path):
    md = _run(tmp_path, "pdf_to_markdown", src=str(sample_pdf), out=str(tmp_path / "s.md"))
    markdown = Path(md.outputs[0]).read_text(encoding="utf-8")
    assert "## گزارش فصلی وین\u200cجنیوس" in markdown
    assert "| نام | تعداد | وضعیت |" in markdown
    assert "**نکته\u200cی مهم:**" in markdown
    html = _run(tmp_path, "pdf_to_html", src=str(sample_pdf), page_break=True, out=str(tmp_path / "s.html"))
    page = Path(html.outputs[0]).read_text(encoding="utf-8")
    assert '<html lang="fa" dir="rtl">' in page
    assert '<table dir="rtl">' in page and '<p dir="ltr"' in page and '<h2 dir="rtl"' in page


# ----------------------------------------------------------------- pdf → docx
def test_pdf_to_docx_writes_real_rtl_word(sample_pdf, tmp_path):
    docx = pytest.importorskip("docx")
    result = _run(tmp_path, "pdf_to_docx", src=str(sample_pdf), out=str(tmp_path / "s.docx"),
                  page_break=True, images=True, tables=True, font="Vazirmatn")
    document = docx.Document(result.outputs[0])
    paragraphs = [p for p in document.paragraphs if p.text.strip()]
    assert paragraphs[0].style.name == "Heading 1" and paragraphs[0].text == "گزارش فصلی وین\u200cجنیوس"
    body = next(p for p in paragraphs if p.text.startswith("این سند"))
    assert body.text.endswith("appear too.")  # one paragraph, not one per visual line
    assert sum(1 for p in paragraphs if p.text.startswith("An English")) == 1
    assert len(document.tables) == 1
    rows = [[cell.text for cell in row.cells] for row in document.tables[0].rows]
    assert rows[0] == ["نام", "تعداد", "وضعیت"]
    xml = zipfile.ZipFile(result.outputs[0]).read("word/document.xml").decode("utf-8")
    assert "<w:bidi/>" in xml and "<w:rtl/>" in xml and "<w:bidiVisual/>" in xml
    assert 'w:cs="Vazirmatn"' in xml and 'w:ascii="Vazirmatn"' in xml
    styles = zipfile.ZipFile(result.outputs[0]).read("word/styles.xml").decode("utf-8")
    assert 'w:cs="Vazirmatn"' in styles


# ------------------------------------------------------------- writing Persian
def _dark_pixels(page, dpi: int = 40) -> int:
    pix = page.get_pixmap(dpi=dpi)
    samples = pix.samples
    step = pix.n
    return sum(1 for i in range(0, len(samples), step) if samples[i] < 200)


def test_watermark_renders_persian_glyphs(sample_pdf, tmp_path, fitz):
    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(blank))
    doc.close()
    result = _run(tmp_path, "pdf_watermark", src=str(blank), text="محرمانه — پیش‌نویس", size=60,
                  opacity=0.9, out=str(tmp_path / "wm.pdf"))
    out = fitz.open(result.outputs[0])
    page = out[0]
    assert _dark_pixels(page) > 400, "watermark must paint real glyphs, not tofu boxes or nothing"
    assert "محرمانه" in pp.normalize_persian(page.get_text("text"))
    out.close()


def test_page_numbers_use_persian_digits_for_persian_documents(sample_pdf, tmp_path, fitz):
    result = _run(tmp_path, "pdf_page_numbers", src=str(sample_pdf), start=3, position="bottom-center",
                  style="n_of_total", digits="auto", out=str(tmp_path / "num.pdf"))
    doc = fitz.open(result.outputs[0])
    # MuPDF's plain dump breaks lines at bidi-run boundaries; the engine re-joins them
    assert "۳ / ۳" in pp.extract_page(doc, 0).text
    assert "۳" in doc[0].get_text("text")
    doc.close()
    latin = _run(tmp_path, "pdf_page_numbers", src=str(sample_pdf), start=1, position="top-center",
                 style="page_n", digits="en", out=str(tmp_path / "num_en.pdf"))
    doc = fitz.open(latin.outputs[0])
    assert "Page 1" in doc[0].get_text("text")
    doc.close()


def test_text_to_pdf_markdown(tmp_path, fitz):
    markdown = ("# گزارش هفتگی\n\nاین گزارش **وضعیت پروژه** را خلاصه می‌کند.\n\n"
                "- تعمیر استخراج متن فارسی\n- واترمارک با قلم وزیرمتن\n\n"
                "| ماژول | وضعیت |\n| --- | --- |\n| PDF | انجام شد |\n\n## English\n\nMixed documents work.\n")
    result = _run(tmp_path, "text_to_pdf", source="text", text=markdown, format="auto", title="",
                  size=12, page="a4", digits="keep", numbers=True, out=str(tmp_path / "md.pdf"))
    doc = fitz.open(result.outputs[0])
    text = doc[0].get_text("text")
    assert "گزارش هفتگی" in text and "Mixed documents work" in text
    assert not CYRILLIC.search(text) and not FORMS.search(text)
    content = pp.extract_page(doc, 0)
    assert content.paragraphs[0].heading == 1
    tables = [item for item in content.items if isinstance(item, pp.Table)]
    assert tables and tables[0].rows[0] == ["ماژول", "وضعیت"]
    doc.close()


def test_docx_to_pdf_builtin_fallback(tmp_path, fitz, monkeypatch):
    docx = pytest.importorskip("docx")
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)  # pretend LibreOffice is absent
    document = docx.Document()
    document.add_heading("نامه‌ی رسمی", level=1)
    paragraph = document.add_paragraph("با سلام، این نامه برای آزمایش تبدیل داخلی Word به PDF نوشته شده است.")
    pp.docx_set_rtl(paragraph, True)
    document.add_paragraph("English closing line.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "ردیف", "شرح"
    table.cell(1, 0).text, table.cell(1, 1).text = "۱", "خدمات"
    src = tmp_path / "letter.docx"
    document.save(str(src))
    result = _run(tmp_path, "docx_to_pdf", src=str(src), out=str(tmp_path / "letter.pdf"))
    doc = fitz.open(result.outputs[0])
    text = doc[0].get_text("text")
    doc.close()
    assert "نامه" in text and "English closing line" in text and "خدمات" in text
    assert not CYRILLIC.search(text) and not FORMS.search(text)
    assert "داخلی" in result.message or "Built-in" in result.message


def test_ocr_text_pdf_writer_shapes_persian(tmp_path, fitz):
    from app.tools.ocr_tools import _write_text_pdf

    dest = tmp_path / "ocr.pdf"
    _write_text_pdf(_ctx(tmp_path), dest, "اسکن", ["سطر اول فارسی\nو سطر دوم", "Second page in English"])
    doc = fitz.open(str(dest))
    assert doc.page_count == 2
    first, second = doc[0].get_text("text"), doc[1].get_text("text")
    doc.close()
    assert "سطر اول فارسی" in first and "Second page in English" in second
    assert not CYRILLIC.search(first) and not FORMS.search(first)


# --------------------------------------------------------- persian text tools
def test_persian_fix_tool_applies_general_zwnj_rules(tmp_path):
    result = _run(tmp_path, "persian_fix", source="text",
                  text="كتاب هاي من مي شود 123 و خانه ها و بزرگ تر و نمي توانم", numbers="fa", write_file=False)
    assert result.details == "کتاب\u200cهای من می\u200cشود ۱۲۳ و خانه\u200cها و بزرگ\u200cتر و نمی\u200cتوانم"


def test_text_cleanup_pairs_persian_quotes(tmp_path):
    result = _run(tmp_path, "text_cleanup", text='او گفت: "سلام   دنیا"\nsize 12" monitor\n', dedupe=0)
    assert result.details == 'او گفت: «سلام دنیا»\nsize 12" monitor'


def test_date_math_moves_by_jalali_months_and_years(tmp_path):
    month = _run(tmp_path, "date_math", date="1405/06/31", calendar="jalali", add=1, unit="months", other="")
    assert "1405/07/30" in month.message
    year = _run(tmp_path, "date_math", date="1403/12/30", calendar="auto", add=1, unit="years", other="")
    assert "1404/12/29" in year.message
    gregorian = _run(tmp_path, "date_math", date="2024-01-31", calendar="gregorian", add=1, unit="months", other="")
    assert "2024-02-29" in gregorian.message


def test_indexer_reads_persian_pdfs(sample_pdf):
    from app.core.indexer import _pdf_reader

    text = _pdf_reader(sample_pdf)
    assert "کتاب\u200cها" in text and not CYRILLIC.search(text)


def test_direction_html_annotates_blocks_and_mirrors_rtl_tables():
    html = pp.direction_html("<p>سلام دنیا</p><p>Hello</p><table><tr><td>نام</td><td>تعداد</td></tr></table>")
    assert 'dir="rtl"' in html.split("</p>")[0] and "text-align:right" in html.split("</p>")[0]
    assert 'dir="ltr"' in html.split("</p>")[1]
    # the Story engine ignores dir="rtl" on tables, so RTL rows are mirrored for it
    assert html.index("تعداد") < html.index("نام")


def test_stamp_helpers_accept_bytes_round_trip(fitz):
    doc = fitz.open()
    page = doc.new_page()
    pp.stamp_line(page, "صفحه ۱", position="bottom-right", size_pt=10)
    pp.stamp_text(page, "DRAFT", size_pt=40, opacity=0.3)
    data = pp.pdf_bytes(doc)
    doc.close()
    again = fitz.open("pdf", io.BytesIO(data).read())
    text = again[0].get_text("text")
    again.close()
    assert "صفحه ۱" in text and "DRAFT" in text
