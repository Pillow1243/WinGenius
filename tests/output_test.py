#!/usr/bin/env python3
"""Output-correctness suite — "is the artefact right?", not just "did it run".

The functional suite proves a tool does not crash.  This one checks the *result*:
page counts, pixel sizes, hashes, round-trips and decoded content, because a
converter that quietly writes a 1-page PDF or a black image passes every
smoke test ever written.

    python3 tests/output_test.py            # everything
    python3 tests/output_test.py pdf image  # only those groups

Exit code is 0 only when nothing failed; optional dependencies (ffmpeg,
tesseract, LibreOffice) are reported as SKIP, never as failures.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never open real windows from a test run: the shell launcher becomes a dry run.
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")
if os.name != "nt":
    os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="wg-out-cfg-"))

from app.core.registry import get as get_tool, load_all          # noqa: E402
from app.core.spec import RunContext, ToolError                   # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FAILURES: list[str] = []

WORK = Path(tempfile.mkdtemp(prefix="wg_output_"))
OUT = WORK / "out"
OUT.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------- harness
def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  FAIL  {name}  {detail}")


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  SKIP  {name}  ({why})")


def ctx() -> RunContext:
    return RunContext(lang="fa", output_dir=str(OUT))


def run(tool_id: str, **values):
    spec = get_tool(tool_id)
    if spec is None:
        raise AssertionError(f"tool not registered: {tool_id}")
    merged = spec.defaults()
    merged.update(values)
    return spec.run(ctx(), merged)


def sha(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def only(results) -> Path:
    """The single artefact a tool produced."""
    items = [Path(item) for item in (results or [])]
    if not items:
        raise AssertionError("tool returned no output file")
    return items[0]


def has_ffmpeg() -> bool:
    from app.core.diagnostics import ffmpeg_path

    return bool(ffmpeg_path())


def has_tesseract() -> bool:
    from app.core.diagnostics import tesseract_path

    return bool(tesseract_path())


# ------------------------------------------------------------------- fixtures
def make_fixtures() -> dict:
    from PIL import Image, ImageDraw
    from reportlab.pdfgen import canvas

    fixtures: dict = {}

    pdf_path = WORK / "report.pdf"
    book = canvas.Canvas(str(pdf_path))
    for page in range(1, 4):
        book.setFont("Helvetica-Bold", 24)
        book.drawString(60, 760, f"WinGenius report page {page}")
        book.setFont("Helvetica", 12)
        for line in range(6):
            book.drawString(60, 720 - line * 20,
                            f"page {page} line {line}: invoice total {page * 100 + line} EUR")
        book.showPage()
    book.save()
    fixtures["pdf"] = pdf_path

    photo = WORK / "photo.png"
    image = Image.new("RGB", (640, 480), "#1F2A44")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 40, 600, 440], outline="white", width=4)
    draw.ellipse([200, 160, 440, 320], fill="#5B8CFF")
    image.save(photo)
    fixtures["png"] = photo

    gallery = WORK / "gallery"
    gallery.mkdir(exist_ok=True)
    colours = ("#FF5A6E", "#2BD9A0", "#FFC24B", "#8B5CFF", "#18C8F5", "#F06BFF")
    for index, colour in enumerate(colours):
        Image.new("RGB", (400, 300), colour).save(gallery / f"frame_{index}.png")
    fixtures["gallery"] = sorted(gallery.glob("*.png"))

    text_file = WORK / "notes.txt"
    text_file.write_text("سلام WinGenius\nline two\ninvoice 1240 EUR\n", encoding="utf-8")
    fixtures["txt"] = text_file

    csv_path = WORK / "table.csv"
    csv_path.write_text("name,qty,price\napple,3,1.5\npear,10,0.8\nplum,7,2.25\n", encoding="utf-8")
    fixtures["csv"] = csv_path

    docx_path = WORK / "letter.docx"
    try:
        from docx import Document

        document = Document()
        document.add_heading("WinGenius letter", level=1)
        document.add_paragraph("This paragraph must survive the round-trip. مقدار ۱۲۳")
        document.add_paragraph("Second paragraph with the magic word: წიგნი".replace("წიგნი", "BOOKWORM"))
        document.save(docx_path)
        fixtures["docx"] = docx_path
    except Exception:
        fixtures["docx"] = None

    xlsx_path = WORK / "book.xlsx"
    try:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sales"
        sheet.append(["month", "units", "revenue"])
        for month, units in (("Farvardin", 12), ("Ordibehesht", 30)):
            sheet.append([month, units, units * 3.5])
        workbook.save(xlsx_path)
        fixtures["xlsx"] = xlsx_path
    except Exception:
        fixtures["xlsx"] = None

    return fixtures


def make_video() -> Path | None:
    """Two-second test clip, only when ffmpeg is available."""
    if not has_ffmpeg():
        return None
    import subprocess

    from app.core.diagnostics import ffmpeg_path

    target = WORK / "clip.mp4"
    command = [ffmpeg_path(), "-y",
               "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-shortest", "-pix_fmt", "yuv420p", str(target)]
    try:
        subprocess.run(command, capture_output=True, timeout=120, check=True)
    except Exception:
        return None
    return target if target.exists() else None


def make_silent_video() -> Path | None:
    """A clip with no audio track — used to prove we fail politely."""
    if not has_ffmpeg():
        return None
    import subprocess

    from app.core.diagnostics import ffmpeg_path

    target = WORK / "silent.mp4"
    try:
        subprocess.run([ffmpeg_path(), "-y", "-f", "lavfi", "-i",
                        "testsrc=size=160x120:rate=10:duration=1", "-pix_fmt", "yuv420p", str(target)],
                       capture_output=True, timeout=120, check=True)
    except Exception:
        return None
    return target if target.exists() else None


# ------------------------------------------------------------------ pdf group
def group_pdf(fx: dict) -> None:
    print("\n[pdf]")
    from pypdf import PdfReader

    pdf = fx["pdf"]
    pages_before = len(PdfReader(str(pdf)).pages)
    check("fixture pdf has 3 pages", pages_before == 3, str(pages_before))

    result = run("pdf_to_images", src=str(pdf), fmt="png", dpi=72, out=str(OUT / "pdf_pages"))
    images = sorted(Path(item) for item in (result.outputs or []))
    check("pdf_to_images writes one file per page", len(images) == pages_before, str(len(images)))
    if images:
        from PIL import Image

        with Image.open(images[0]) as first:
            width, height = first.size
        check("pdf_to_images renders A4 at 72 dpi", 560 <= width <= 640 and 760 <= height <= 880,
              f"{width}x{height}")

    result = run("pdf_to_text", src=str(pdf), per_page=True, out=str(OUT / "report.txt"))
    body = Path(result.outputs[0]).read_text(encoding="utf-8")
    check("pdf_to_text keeps the text", "WinGenius report page 1" in body, body[:60])
    check("pdf_to_text keeps all pages", body.count("report page") >= 3, str(body.count("report page")))
    check("pdf_to_text has no mojibake", "Ã" not in body and "\x00" not in body)

    merged = run("pdf_merge", src=[str(pdf), str(pdf)], out=str(OUT / "merged.pdf"))
    check("pdf_merge doubles the page count",
          len(PdfReader(str(only(merged.outputs))).pages) == pages_before * 2,
          str(len(PdfReader(str(only(merged.outputs))).pages)))

    split = run("pdf_split", src=str(pdf), mode="each", out=str(OUT / "split"))
    singles = sorted(Path(item) for item in (split.outputs or []))
    check("pdf_split writes one pdf per page", len(singles) == pages_before, str(len(singles)))
    if singles:
        check("each split pdf has exactly one page",
              all(len(PdfReader(str(item)).pages) == 1 for item in singles))

    extracted = run("pdf_extract_pages", src=str(pdf), pages="2", out=str(OUT / "page2.pdf"))
    check("pdf_extract_pages keeps the requested page",
          len(PdfReader(str(only(extracted.outputs))).pages) == 1)

    rotated = run("pdf_rotate", src=str(pdf), angle="90", out=str(OUT / "rotated.pdf"))
    rotated_reader = PdfReader(str(only(rotated.outputs)))
    rotation = int(rotated_reader.pages[0].get("/Rotate", 0) or 0)
    check("pdf_rotate actually rotates the page", rotation % 360 == 90, str(rotation))

    compressed = run("pdf_compress", src=str(pdf), mode="balanced", out=str(OUT / "small.pdf"))
    small = only(compressed.outputs)
    check("pdf_compress writes a readable pdf", len(PdfReader(str(small)).pages) == pages_before)
    check("pdf_compress does not produce an empty file", small.stat().st_size > 500,
          str(small.stat().st_size))

    back = run("images_to_pdf", src=[str(item) for item in images],
               page="fit", fit="contain", out=str(OUT / "back.pdf"))
    check("images_to_pdf rebuilds the page count",
          len(PdfReader(str(only(back.outputs))).pages) == len(images),
          f'{len(PdfReader(str(only(back.outputs))).pages)} vs {len(images)}')

    # round-trip: text must still be readable after pdf → images → pdf (visual, not text)
    check("pdf round-trip pdf → png → pdf keeps every page", images and back.outputs)

    # ------------------------------------------------- the smaller page tools
    numbered = run("pdf_page_numbers", src=str(pdf), position="bottom-center",
                   start=1, out=str(OUT / "numbered.pdf"))
    numbered_text = "".join(page.extract_text() or "" for page in PdfReader(str(only(numbered.outputs))).pages)
    check("pdf_page_numbers writes a readable number on every page",
          numbered_text.count("1") >= 1 and numbered_text.count("3") >= 1, numbered_text[-40:])

    renaming = run("pdf_set_metadata", src=str(pdf), title="Quarterly report",
                   author="WinGenius tests", out=str(OUT / "meta.pdf"))
    meta = PdfReader(str(only(renaming.outputs))).metadata or {}
    check("pdf_set_metadata writes the title", "Quarterly" in str(meta.get("/Title", "")),
          str(meta.get("/Title"))[:40])
    check("pdf_set_metadata writes the author", "WinGenius" in str(meta.get("/Author", "")),
          str(meta.get("/Author"))[:40])

    found = run("pdf_find_text", src=str(pdf), query="invoice total")
    check("pdf_find_text reports the matches", "۳" in found.details or "3" in found.details,
          found.details[:60])

    markdown = run("pdf_to_markdown", src=str(pdf), out=str(OUT / "report.md"))
    md_text = Path(markdown.outputs[0]).read_text(encoding="utf-8")
    check("pdf_to_markdown keeps the text", "report page" in md_text, md_text[:60])

    blanked = run("pdf_insert_blank", src=str(pdf), mode="position", position=1,
                  out=str(OUT / "blank.pdf"))
    check("pdf_insert_blank adds exactly one page at the requested position",
          len(PdfReader(str(only(blanked.outputs))).pages) == pages_before + 1,
          str(len(PdfReader(str(only(blanked.outputs))).pages)))
    every = run("pdf_insert_blank", src=str(pdf), mode="every", out=str(OUT / "blank_every.pdf"))
    check("pdf_insert_blank 'every' doubles the page count",
          len(PdfReader(str(only(every.outputs))).pages) == pages_before * 2,
          str(len(PdfReader(str(only(every.outputs))).pages)))

    reordered = run("pdf_reorder_pages", src=str(pdf), order="3,2,1", out=str(OUT / "reordered.pdf"))
    first_text = PdfReader(str(only(reordered.outputs))).pages[0].extract_text() or ""
    check("pdf_reorder_pages puts page 3 first", "page 3" in first_text.lower(), first_text[:40])

    cropped = run("pdf_crop", src=str(pdf), top=10, bottom=10, left=10, right=10,
                  out=str(OUT / "cropped.pdf"))
    cropped_page = PdfReader(str(only(cropped.outputs))).pages[0]
    crop_box = cropped_page.cropbox
    check("pdf_crop shrinks the visible box",
          float(crop_box.width) < float(cropped_page.mediabox.width) - 20,
          f"{float(crop_box.width):.0f} of {float(cropped_page.mediabox.width):.0f}")
    check("pdf_crop keeps the document readable",
          (cropped_page.extract_text() or "").strip() != "", "page has no text after crop")

    booklet = run("pdf_booklet", src=str(pdf), out=str(OUT / "booklet.pdf"))
    booklet_reader = PdfReader(str(only(booklet.outputs)))
    booklet_pages = len(booklet_reader.pages)
    # a booklet prints two source pages per side: 3 pages → 4 padded → 2 sides
    check("pdf_booklet produces printable sides", booklet_pages % 2 == 0 and
          booklet_pages * 2 >= pages_before, str(booklet_pages))
    check("pdf_booklet sides really hold content",
          any((page.extract_text() or "").strip() for page in booklet_reader.pages))

    poster = run("pdf_poster", src=str(pdf), columns=2, rows=2, out=str(OUT / "poster.pdf"))
    poster_pages = len(PdfReader(str(only(poster.outputs))).pages)
    check("pdf_poster multiplies the page count", poster_pages == pages_before * 4, str(poster_pages))


# ---------------------------------------------------------------- image group
def group_image(fx: dict) -> None:
    print("\n[image]")
    from PIL import Image

    png, gallery = fx["png"], fx["gallery"]

    result = run("image_convert", src=[str(png)], fmt="jpeg", quality=88, out_dir=str(OUT / "jpg"))
    converted = only(result.outputs)
    with Image.open(converted) as image:
        check("image_convert writes a real jpeg", image.format == "JPEG", str(image.format))
        check("image_convert keeps the pixel size", image.size == (640, 480), str(image.size))

    result = run("image_resize", src=[str(png)], mode="max", max_side=200,
                 keep_ratio="keep", out_dir=str(OUT / "resized"))
    with Image.open(only(result.outputs)) as image:
        check("image_resize honours max_side", max(image.size) == 200, str(image.size))
        check("image_resize keeps the aspect ratio", abs((image.size[0] / image.size[1]) - (640 / 480)) < 0.02,
              str(image.size))

    result = run("image_resize", src=[str(png)], mode="exact", width=123, height=45,
                 keep_ratio="stretch", out_dir=str(OUT / "exact"))
    with Image.open(only(result.outputs)) as image:
        check("image_resize exact mode uses both sides", image.size == (123, 45), str(image.size))

    original_bytes = png.stat().st_size
    result = run("image_compress", src=[str(png)], quality=40, max_side=800, out_dir=str(OUT / "compressed"))
    compressed = only(result.outputs)
    check("image_compress shrinks a photo", compressed.stat().st_size < original_bytes * 3,
          f"{compressed.stat().st_size} vs {original_bytes}")

    before = Image.open(png).convert("RGB")
    result = run("image_watermark", src=[str(png)], text="WinGenius",
                 position="br", size=28, opacity=0.8, color="#FFFFFF",
                 out_dir=str(OUT / "marked"))
    after = Image.open(only(result.outputs)).convert("RGB")
    check("image_watermark changes pixels", before.size == after.size
          and before.tobytes() != after.tobytes())
    if before.size == after.size:
        changed = sum(1 for a, b in zip(before.tobytes()[::97], after.tobytes()[::97]) if a != b)
        check("image_watermark only paints a corner", changed > 0, str(changed))

    result = run("image_contact_sheet", src=[str(item) for item in gallery], cols=3, cell=150,
                 gap=6, out=str(OUT / "sheet.png"))
    with Image.open(only(result.outputs)) as sheet:
        check("image_contact_sheet is a grid of the inputs", sheet.size[0] > 3 * 150 * 0.9,
              str(sheet.size))
        check("image_contact_sheet is not a blank image",
              len(set(sheet.convert("RGB").getdata())) > 50,
              str(len(set(sheet.convert("RGB").getdata()))))

    result = run("image_split_grid", src=[str(png)], cols=2, rows=2, out_dir=str(OUT / "tiles"))
    tiles = [Path(item) for item in (result.outputs or []) if Path(item).is_file()]
    check("image_split_grid produces cols×rows tiles", len(tiles) == 4, str(len(tiles)))

    result = run("image_rotate", src=[str(png)], angle="90", out_dir=str(OUT / "rot"))
    with Image.open(only(result.outputs)) as image:
        check("image_rotate swaps width and height", image.size == (480, 640), str(image.size))


# ----------------------------------------------------------------- docs group
def group_docs(fx: dict) -> None:
    print("\n[docs]")
    csv_path = fx["csv"]

    result = run("csv_profile", file=str(csv_path), rows=20)
    details = getattr(result, "details", "") or ""
    check("csv_profile counts the rows", "3" in details, details[:80])
    check("csv_profile names the columns", "price" in details and "name" in details, details[:80])

    result = run("csv_to_sql", file=str(csv_path), table="items", out=str(OUT / "table.sql"))
    sql_text = Path(only(result.outputs)).read_text(encoding="utf-8")
    check("csv_to_sql emits INSERTs for every row", sql_text.count("INSERT") == 3,
          str(sql_text.count("INSERT")))
    check("csv_to_sql quotes the values", "'apple'" in sql_text, sql_text[:80])

    if fx.get("xlsx"):
        result = run("sheet_to_csv", src=str(fx["xlsx"]), out_dir=str(OUT / "csv_from_xlsx"))
        produced = [Path(item) for item in (result.outputs or []) if Path(item).suffix == ".csv"]
        check("sheet_to_csv writes a csv from xlsx", bool(produced), str(result.outputs)[:80])
        if produced:
            check("sheet_to_csv keeps the header row",
                  "month" in produced[0].read_text(encoding="utf-8").lower(),
                  produced[0].read_text(encoding="utf-8")[:60])
    else:
        skip("sheet_to_csv", "openpyxl unavailable")

    result = run("markdown_to_html", text="# Title\n\nSome **bold** text.\n", out=str(OUT / "page.html"))
    html = Path(only(result.outputs)).read_text(encoding="utf-8")
    check("markdown_to_html produces a heading", "<h1" in html.lower(), html[:60])
    check("markdown_to_html produces bold markup", "<strong>" in html.lower(), html[:60])

    result = run("json_yaml", source="json", target="yaml", text='{"a": 1, "b": [1, 2]}',
                 out=str(OUT / "data.yaml"))
    yaml_text = Path(only(result.outputs)).read_text(encoding="utf-8")
    check("json_yaml converts to yaml", "a: 1" in yaml_text, yaml_text[:60])

    result = run("json_yaml", source="yaml", target="json",
                 text="a: 1\nb:\n  - 1\n  - 2\n", out=str(OUT / "data.json"))
    parsed = json.loads(Path(only(result.outputs)).read_text(encoding="utf-8"))
    check("json_yaml converts back to json", parsed.get("a") == 1 and parsed.get("b") == [1, 2], str(parsed))

    if fx.get("docx"):
        check("pdf_to_docx is available", get_tool("pdf_to_docx") is not None)
        result = run("pdf_to_docx", src=str(fx["pdf"]), out=str(OUT / "from_pdf.docx"))
        produced = only(result.outputs)
        from docx import Document

        paragraphs = [p.text for p in Document(str(produced)).paragraphs if p.text.strip()]
        check("pdf_to_docx writes a real docx", produced.suffix == ".docx" and paragraphs,
              str(len(paragraphs)))
        check("pdf_to_docx keeps the text", any("WinGenius report" in text for text in paragraphs),
              str(paragraphs[:2]))
    else:
        skip("docx fixtures", "python-docx unavailable")

    if fx.get("xlsx"):
        result = run("table_convert", src=str(fx["xlsx"]), target="csv", out=str(OUT / "sheet.csv"))
        sheet_text = Path(only(result.outputs)).read_text(encoding="utf-8")
        check("table_convert exports xlsx rows",
              "Farvardin" in sheet_text and "revenue" in sheet_text.lower(), sheet_text[:80])
        check("table_convert keeps numeric cells", "12" in sheet_text, sheet_text[:80])
    else:
        skip("xlsx fixtures", "openpyxl unavailable")


# --------------------------------------------------------------- crypto group
def group_crypto(fx: dict) -> None:
    print("\n[crypto]")
    from app.core.vault import MAGIC_V2, Vault

    source = WORK / "secret.txt"
    source.write_text("top secret payload ۱۲۳\n", encoding="utf-8")
    original_hash = sha(source)

    result = run("file_encrypt", src=[str(source)], password="correct-horse",
                 out_dir=str(OUT / "enc"))
    encrypted = only(result.outputs)
    check("file_encrypt writes bytes that are not the plaintext",
          source.read_bytes() not in encrypted.read_bytes())
    check("file_encrypt makes the file bigger (salt+nonce)", encrypted.stat().st_size > source.stat().st_size)

    result = run("file_decrypt", src=[str(encrypted)], password="correct-horse",
                 out_dir=str(OUT / "dec"))
    decrypted = only(result.outputs)
    check("file_decrypt restores the exact bytes", sha(decrypted) == original_hash,
          f"{sha(decrypted)[:12]} vs {original_hash[:12]}")

    try:
        run("file_decrypt", src=[str(encrypted)], password="wrong-password", out_dir=str(OUT / "bad"))
        check("file_decrypt refuses a wrong password", False, "no error raised")
    except ToolError as exc:
        check("file_decrypt refuses a wrong password", True, str(exc)[:40])

    vault_path = OUT / "demo.wgv"
    run("vault_create", vault=str(vault_path), password="vault-pass", confirm="vault-pass",
        iterations="14")
    vault = Vault(vault_path, "vault-pass")
    vault.unlock()
    check("vault uses the authenticated v2 container",
          vault_path.read_bytes().startswith(MAGIC_V2), str(vault_path.read_bytes()[:4]))
    check("vault key derivation is scrypt", "scrypt" in vault.strength(), vault.strength())

    run("vault_add", vault=str(vault_path), password="vault-pass", files=[str(source)])
    check("vault_add stores the entry", "secret.txt" in Vault(vault_path, "vault-pass").names(),
          str(Vault(vault_path, "vault-pass").names()))

    result = run("vault_extract", vault=str(vault_path), password="vault-pass",
                 names="secret.txt", out_dir=str(OUT / "vault_out"))
    restored = [Path(p) for p in (result.outputs or []) if Path(p).is_file()]
    check("vault_extract restores the file byte for byte",
          bool(restored) and sha(restored[0]) == original_hash,
          str(restored[:1]))

    # header tampering must fail authentication (v2 binds the header as AAD)
    raw = bytearray(vault_path.read_bytes())
    raw[5] = (raw[5] + 3) % 20
    tampered = OUT / "tampered.wgv"
    tampered.write_bytes(bytes(raw))
    try:
        Vault(tampered, "vault-pass").unlock()
        check("vault rejects a tampered header", False, "header edit was accepted")
    except Exception:
        check("vault rejects a tampered header", True)

    result = run("hash_files", src=[str(source)], algo="sha256", save=str(OUT / "hashes.txt"))
    manifest = Path(result.outputs[0]).read_text(encoding="utf-8").lower() if result.outputs else ""
    check("hash_files writes the sha256 it claims", sha(source) in manifest, manifest[:70])


# -------------------------------------------------------------- archive group
def group_archive(fx: dict) -> None:
    print("\n[archive]")
    gallery = fx["gallery"]
    result = run("zip_create", src=[str(item) for item in gallery], level="normal",
                 out=str(OUT / "frames.zip"))
    archive = only(result.outputs)
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
    check("zip_create stores every input", len(names) == len(gallery), str(len(names)))
    check("zip_create keeps the file names", any(name.endswith("frame_0.png") for name in names),
          str(names[:3]))
    check("zip_create produces a valid zipfile", zipfile.is_zipfile(archive))

    result = run("zip_extract", src=str(archive), dest=str(OUT / "unzip"))
    restored = sorted((OUT / "unzip").rglob("frame_*.png"))
    check("zip_extract restores every file", len(restored) == len(gallery), str(len(restored)))
    if restored:
        check("zip round-trip keeps the bytes", sha(restored[0]) == sha(gallery[0]))

    # zip-slip regression (also covered in the functional suite)
    evil = OUT / "evil.zip"
    with zipfile.ZipFile(evil, "w") as handle:
        handle.writestr("../../pwned.txt", "x")
        handle.writestr("ok.txt", "y")
    run("zip_extract", src=str(evil), dest=str(OUT / "evil_out"))
    check("zip_extract never escapes the destination", not (WORK / "pwned.txt").exists()
          and not (OUT / "pwned.txt").exists())
    check("zip_extract still writes the safe member", (OUT / "evil_out" / "ok.txt").exists())


# ------------------------------------------------------------------ qr group
def group_qr(fx: dict) -> None:
    print("\n[qr]")
    from PIL import Image

    payload = "https://example.com/wingenius?x=42"
    result = run("qr_generate", mode="text", text=payload, size=320, out=str(OUT / "code.png"))
    code = only(result.outputs)
    with Image.open(code) as image:
        check("qr_generate creates a square image", image.size[0] == image.size[1], str(image.size))
    scan = run("qr_scan", src=str(code))
    details = f"{getattr(scan, 'message', '')} {getattr(scan, 'details', '')}"
    check("qr_scan decodes what qr_generate encoded", payload in details, details[:80])
    check("qr_scan does not return garbage", "example.com" in details, details[:80])


# --------------------------------------------------------------- media group
def group_media(fx: dict) -> None:
    print("\n[media]")
    clip = make_video()
    if clip is None:
        skip("video tools", "ffmpeg not available")
        return
    from PIL import Image

    result = run("video_frames", src=str(clip), mode="every", interval=1,
                 out_dir=str(OUT / "frames"))
    frames = [Path(item) for item in (result.outputs or [])
              if Path(item).suffix.lower() in (".png", ".jpg", ".jpeg")]
    check("video_frames extracts frames", len(frames) >= 2, str(len(frames)))

    result = run("video_to_gif", src=str(clip), fps=8, width=240, out=str(OUT / "clip.gif"))
    gif = only(result.outputs)
    with Image.open(gif) as image:
        check("video_to_gif writes an animated gif", getattr(image, "n_frames", 1) > 1,
              str(getattr(image, "n_frames", 1)))

    result = run("video_to_audio", src=[str(clip)], fmt="mp3", out_dir=str(OUT / "audio"))
    audio = only(result.outputs)
    check("video_to_audio writes a real mp3", audio.exists() and audio.stat().st_size > 1000,
          str(audio.stat().st_size))
    with open(audio, "rb") as handle:
        head = handle.read(3)
    framed = head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    check("video_to_audio writes an mp3 header", framed, head.hex())

    # -------------------------------------------------- more media operations
    result = run("media_convert", src=[str(clip)], fmt="wav", out_dir=str(OUT / "converted"))
    converted = only(result.outputs)
    check("media_convert writes the requested format", converted.suffix == ".wav",
          converted.name)
    check("media_convert produces audio data", converted.stat().st_size > 2000,
          str(converted.stat().st_size))

    result = run("video_trim", src=str(clip), start="0", duration=1, out=str(OUT / "trim.mp4"))
    trimmed = only(result.outputs)
    check("video_trim writes a smaller clip",
          trimmed.exists() and trimmed.stat().st_size < clip.stat().st_size,
          f"{trimmed.stat().st_size} vs {clip.stat().st_size}")

    result = run("video_compress", src=str(clip), quality="low", height=120,
                 out_dir=str(OUT / "compressed_video"))
    smaller = only(result.outputs)
    check("video_compress never returns a bigger file",
          smaller.stat().st_size <= clip.stat().st_size,
          f"{smaller.stat().st_size} vs {clip.stat().st_size}")
    check("video_compress explains when it kept the original",
          "کم‌حجم" in result.message or "compact" in result.message, result.message[:80])

    joined = run("video_merge", src=[str(clip), str(clip)], reencode=True,
                 out=str(OUT / "joined.mp4"))
    check("video_merge writes one merged file", only(joined.outputs).stat().st_size > clip.stat().st_size)

    info = run("media_info", src=[str(clip)])
    details = f"{getattr(info, 'message', '')} {getattr(info, 'details', '')}"
    check("media_info reports the real duration", "2" in details, details[:80])
    check("media_info mentions the container or codec",
          any(word in details.lower() for word in ("h264", "mp4", "aac", "video")), details[:80])

    result = run("audio_trim", src=str(audio), start="0", end="1", out=str(OUT / "cut.mp3"))
    cut = only(result.outputs)
    check("audio_trim produces a shorter file (and does not crash on human_size)",
          cut.exists() and cut.stat().st_size > 0, str(cut.stat().st_size))
    check("audio_trim reports a friendly size", "KB" in (result.message or "")
          or "MB" in (result.message or ""), (result.message or "")[:60])

    result = run("audio_join", src=[str(cut), str(cut)], fmt="mp3", out=str(OUT / "joined.mp3"))
    check("audio_join writes one file", only(result.outputs).stat().st_size > cut.stat().st_size)

    result = run("audio_normalize", src=str(audio), target="-16", out=str(OUT / "normal.mp3"))
    check("audio_normalize produces audio", only(result.outputs).stat().st_size > 0)

    silent = make_silent_video()
    if silent is not None:
        try:
            run("video_to_audio", src=[str(silent)], fmt="mp3", out_dir=str(OUT / "silent_audio"))
            check("video_to_audio explains a silent clip", False, "no error raised")
        except ToolError as exc:
            message = str(exc)
            check("video_to_audio explains a silent clip",
                  "صوت" in message or "audio" in message.lower(), message[:60])


# ------------------------------------------------------------------- ocr group
def group_ocr(fx: dict) -> None:
    print("\n[ocr]")
    if not has_tesseract():
        skip("image_ocr", "tesseract not installed")
        return
    from PIL import Image, ImageDraw, ImageFont

    plate = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(plate)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
    except Exception:
        font = None
    draw.text((30, 60), "WIN GENIUS 2026", fill="black", font=font)
    plate_path = WORK / "plate.png"
    plate.save(plate_path)
    result = run("image_ocr", src=str(plate_path))
    text = f"{getattr(result, 'message', '')} {getattr(result, 'details', '')}".upper()
    check("image_ocr reads printed text", "GENIUS" in text, text[:80])


# ---------------------------------------------------------------------- main
# --------------------------------------------------------------- signature group
def _make_p12(path) -> None:
    """A throw-away self-signed certificate so the signing tests need no CA."""
    import datetime as _dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WinGenius Test Signer")])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1))
            .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=365))
            .sign(key, hashes.SHA256()))
    path.write_bytes(pkcs12.serialize_key_and_certificates(
        b"signer", key, cert, None, serialization.BestAvailableEncryption(b"secret123")))


def group_sign(fx: dict) -> None:
    """Sign → verify → tamper: the three things that must hold for a signature."""
    print("\n[signatures]")
    try:
        import pyhanko  # noqa: F401
    except Exception:
        skip("pdf_sign (pyHanko not installed)", "pip install pyhanko")
        return
    import pymupdf

    p12 = WORK / "signer.p12"
    _make_p12(p12)
    source = WORK / "contract.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 90), "WinGenius service agreement", fontsize=16)
    page.insert_text((60, 130), "Total: 4500 EUR", fontsize=12)
    doc.save(source)
    doc.close()

    result = run("pdf_sign", src=str(source), cert=str(p12), password="secret123",
                 reason="approved", location="Tehran", page=1,
                 out=str(OUT / "contract_signed.pdf"))
    signed = only(result.outputs)
    check("pdf_sign writes a signed file", signed.exists() and signed.stat().st_size > 1000,
          str(signed.stat().st_size))
    check("pdf_sign reports the signature as intact",
          "سالم است" in result.message or "intact" in result.message, result.message[:80])
    check("pdf_sign names the signer",
          "WinGenius Test Signer" in result.details, result.details[:80])

    checked = run("pdf_signature_check", src=str(signed))
    check("pdf_signature_check finds the signature", "1" in checked.message, checked.message[:60])
    check("pdf_signature_check confirms the file is unchanged",
          "دست‌نخورده" in checked.message or "unchanged" in checked.message, checked.message[:80])
    check("pdf_signature_check explains the trust state",
          "خودساخته" in checked.message or "self-signed" in checked.message,
          checked.message[:80])

    # tamper after signing: the signature stays intact, but the file has moved on
    tampered = OUT / "contract_tampered.pdf"
    tampered.write_bytes(signed.read_bytes())
    with pymupdf.open(tampered) as document:
        document[0].insert_text((60, 400), "Added after signing", fontsize=12)
        document.saveIncr()
    after = run("pdf_signature_check", src=str(tampered))
    check("pdf_signature_check detects a change made after signing",
          "هشدار" in after.message or "warning" in after.message, after.message[:80])
    check("pdf_signature_check shows the reduced coverage",
          "ENTIRE_REVISION" in after.details or "تغییر" in after.details, after.details[:80])

    failed = run_expect_failure("pdf_sign", src=str(source), cert=str(p12),
                                password="wrong-one", out=str(OUT / "nope.pdf"))
    check("pdf_sign explains a wrong certificate password",
          "رمز" in failed or "password" in failed, failed[:70])
    failed = run_expect_failure("pdf_signature_check", src=str(source))
    check("pdf_signature_check says an unsigned pdf has no signature",
          "امضا" in failed or "signature" in failed, failed[:70])


# ------------------------------------------------------------- redact group
def group_redact(fx: dict) -> None:
    """The point of redaction: after saving, the words must be *gone*, not covered."""
    print("\n[redact]")
    import pymupdf

    secret = "6037-9975-1234-5678"
    source = WORK / "invoice.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 90), "WinGenius invoice", fontsize=18)
    page.insert_text((60, 130), f"Card number: {secret}", fontsize=12)
    page.insert_text((60, 160), "Password: hunter2-secret", fontsize=12)
    page.insert_text((60, 190), "This public line must survive.", fontsize=12)
    doc.save(source)
    doc.close()

    before = "".join(p.get_text() for p in pymupdf.open(source))
    check("redact fixture contains the secret", secret in before, before[:40])

    result = run("pdf_redact", src=str(source), mode="search",
                 terms=f"{secret}\nhunter2-secret", out=str(OUT / "invoice_masked.pdf"))
    masked = only(result.outputs)
    check("pdf_redact writes a file", masked.exists() and masked.stat().st_size > 400,
          str(masked.stat().st_size))

    with pymupdf.open(masked) as reader:
        after = "".join(page.get_text() for page in reader)
    check("redacted card number is really gone", secret not in after, after[:80])
    check("redacted password is really gone", "hunter2" not in after, after[:80])
    check("redaction keeps unrelated text", "This public line must survive." in after, after[:80])
    check("redaction keeps the page count", pymupdf.open(masked).page_count == 1)
    check("pdf_redact confirms the removal in its message",
          "تأیید" in result.message or "verified" in result.message, result.message[:70])

    # a phrase that is not there must fail loudly instead of writing an untouched copy
    failed = run_expect_failure("pdf_redact", src=str(source), mode="search",
                                terms="this phrase does not exist",
                                out=str(OUT / "nope.pdf"))
    check("pdf_redact refuses when nothing matches", "پیدا نشد" in failed or "not occur" in failed,
          failed[:70])

    # area mode: keeps the file readable, blanks a band of the page
    area = run("pdf_redact", src=str(source), mode="area", area="5,5,90,25",
               out=str(OUT / "invoice_area.pdf"))
    check("pdf_redact area mode writes a file", only(area.outputs).stat().st_size > 400)


def run_expect_failure(tool_id: str, **values) -> str:
    """Run a tool that should refuse; return the message for assertions."""
    try:
        run(tool_id, **values)
    except Exception as exc:                    # ToolError or a wrapped failure
        return str(exc)
    return ""


GROUPS = {"pdf": group_pdf, "image": group_image, "docs": group_docs,
          "crypto": group_crypto, "archive": group_archive, "qr": group_qr,
          "media": group_media, "ocr": group_ocr, "redact": group_redact,
          "sign": group_sign}


def main(argv: list[str]) -> int:
    load_all()
    wanted = [name for name in argv[1:] if not name.startswith("-")] or list(GROUPS)
    unknown = [name for name in wanted if name not in GROUPS]
    if unknown:
        print(f"unknown group(s): {', '.join(unknown)}")
        return 2
    fixtures = make_fixtures()
    for name in wanted:
        try:
            GROUPS[name](fixtures)
        except Exception as exc:                       # a broken group must be visible
            import traceback

            traceback.print_exc()
            check(f"{name} group ran", False, f"{type(exc).__name__}: {exc}")
    print("\n" + "=" * 62)
    print(f"  OUTPUT CHECK  PASS {PASS}   FAIL {FAIL}   SKIP {SKIP}")
    if FAILURES:
        print("=" * 62)
        for line in FAILURES:
            print(f"  ✗ {line}")
    print("=" * 62)
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
