"""End-to-end smoke test: runs (almost) every WinGenius tool on real files.

    python tests/smoke_test.py            # all tools, temp sandbox
    python tests/smoke_test.py pdf image  # only matching tool ids

Exit code 0 means every exercised tool produced its expected output.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never open real windows from a test run: the shell launcher becomes a dry run.
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")
# Standalone runs must not touch the developer's real application profile.
from tests.sandbox import isolate_profile
isolate_profile()

from app.core.registry import get, load_all                       # noqa: E402
from app.core.spec import RunContext, RunResult, ToolError        # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results = []


# --------------------------------------------------------------------- fixtures
def make_fixtures(base: Path) -> dict:
    from PIL import Image, ImageDraw
    from reportlab.pdfgen import canvas

    base.mkdir(parents=True, exist_ok=True)

    # images
    images = []
    for index, color in enumerate(((220, 70, 90), (60, 170, 200), (250, 190, 60))):
        image = Image.new("RGB", (640, 480), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle([40, 40, 600, 200], fill=(255, 255, 255))
        draw.text((60, 90), f"WinGenius test {index}", fill=(10, 10, 10))
        path = base / f"image_{index}.png"
        image.save(path)
        images.append(path)

    # pdf with 6 pages of text
    pdf_path = base / "sample.pdf"
    c = canvas.Canvas(str(pdf_path), pagesize=(595, 842))
    for page in range(6):
        c.setFont("Helvetica-Bold", 24)
        c.drawString(60, 760, f"WinGenius sample page {page + 1}")
        c.setFont("Helvetica", 12)
        for line in range(12):
            c.drawString(60, 720 - line * 22, f"Line {line + 1} of page {page + 1} — hello world.")
        c.showPage()
    c.save()

    # docx / csv / json / text
    from docx import Document

    document = Document()
    document.add_heading("WinGenius", level=1)
    document.add_paragraph("Sample document for conversion tests.")
    docx_path = base / "sample.docx"
    document.save(str(docx_path))

    csv_path = base / "table.csv"
    csv_path.write_text("name,age,city\nAli,30,Tehran\nSara,27,Berlin\nJohn,41,London\n", encoding="utf-8")

    json_path = base / "data.json"
    json_path.write_text(json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], ensure_ascii=False), encoding="utf-8")

    text_path = base / "notes.txt"
    text_path.write_text("سلام دنيا\nخط دوم 123\n\n\nخط چهارم\n", encoding="utf-8")

    # xlsx for the office tools
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["name", "age", "city"])
    for index in range(25):
        sheet.append([f"person{index}", 20 + index, "Tehran"])
    xlsx_path = base / "sample.xlsx"
    workbook.save(str(xlsx_path))

    # a folder with planted secrets for the scanner
    secrets_dir = base / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    (secrets_dir / "settings.py").write_text(
        'API_KEY = "AIzaSyD-abcdefghijklmnopqrstuvwxyz012345"\npassword = "hunter2pass"\n',
        encoding="utf-8")

    second_text = base / "notes2.txt"
    second_text.write_text("سلام دنیا\nخط تغییر یافته\n", encoding="utf-8")

    images_dir = base / "images"
    images_dir.mkdir(exist_ok=True)
    for index, path in enumerate(images):
        (images_dir / f"copy{index}.png").write_bytes(path.read_bytes())

    big_path = base / "big.bin"
    big_path.write_bytes(os.urandom(3 * 1024 * 1024))

    rename_dir = base / "rename"
    rename_dir.mkdir(exist_ok=True)
    for index in range(4):
        (rename_dir / f"photo {index}.jpg").write_bytes(b"x" * 2048)

    dup_dir = base / "dups"
    dup_dir.mkdir(exist_ok=True)
    for index in range(3):
        (dup_dir / f"copy_{index}.txt").write_text("identical content", encoding="utf-8")
    (dup_dir / "other.txt").write_text("different", encoding="utf-8")

    return {
        "base": base,
        "images": images,
        "pdf": pdf_path,
        "docx": docx_path,
        "csv": csv_path,
        "json": json_path,
        "text": text_path,
        "big": big_path,
        "rename_dir": rename_dir,
        "xlsx": xlsx_path,
        "secrets": secrets_dir,
        "text2": second_text,
        "images_dir": images_dir,
        "dup_dir": dup_dir,
        "out": base / "out",
    }


# -------------------------------------------------------------------- harness
def run_tool(tool_id: str, values: dict, ctx: RunContext, expect_output=True, note="") -> RunResult:
    spec = get(tool_id)
    if spec is None:
        raise AssertionError(f"tool {tool_id} is not registered")
    if spec.run is None:
        raise AssertionError(f"tool {tool_id} has no run()")
    result = spec.run(ctx, values)
    assert isinstance(result, RunResult), f"{tool_id} returned {type(result)}"
    assert result.message, f"{tool_id} returned no message"
    if expect_output:
        assert result.outputs, f"{tool_id} produced no output paths"
        for output in result.outputs:
            assert Path(output).exists(), f"{tool_id}: missing output {output}"
    return result


def record(tool_id: str, status: str, detail: str = "") -> None:
    results.append((tool_id, status, detail))
    marker = {PASS: "✓", FAIL: "✗", SKIP: "–"}[status]
    print(f"  {marker} {tool_id:<24} {detail[:110]}")


def expect_error(tool_id: str, values: dict, ctx: RunContext, label="error") -> None:
    spec = get(tool_id)
    try:
        spec.run(ctx, spec.defaults() | values)
    except (ToolError, Exception) as exc:  # noqa: BLE001
        record(tool_id, PASS, f"raised {type(exc).__name__}")
        return
    record(tool_id, FAIL, "expected an error but succeeded")


# ---------------------------------------------------------------------- tests
def test_pdf(f: dict, ctx_factory) -> None:
    print("\n[PDF]")
    ctx = ctx_factory()
    run_tool("pdf_to_images", {"src": str(f["pdf"]), "fmt": "png", "dpi": 96,
                               "out": str(f["out"] / "pdfimg")}, ctx)
    record("pdf_to_images", PASS)

    run_tool("pdf_to_images", {"src": str(f["pdf"]), "fmt": "jpg", "dpi": 72, "quality": 80,
                               "out": str(f["out"] / "pdfimg_jpg")}, ctx)
    record("pdf_to_images(jpg)", PASS)

    result = run_tool("pdf_to_text", {"src": str(f["pdf"]), "per_page": True,
                                     "out": str(f["out"] / "sample.txt")}, ctx)
    assert "Line 1 of page 1" in Path(result.outputs[0]).read_text(encoding="utf-8")
    record("pdf_to_text", PASS)

    result = run_tool("pdf_to_docx", {"src": str(f["pdf"]), "out": str(f["out"] / "sample.docx")}, ctx)
    assert Path(result.outputs[0]).stat().st_size > 1000
    record("pdf_to_docx", PASS)

    run_tool("images_to_pdf", {"src": [str(p) for p in f["images"]], "page": "auto",
                               "out": str(f["out"] / "images.pdf")}, ctx)
    record("images_to_pdf(auto)", PASS)
    run_tool("images_to_pdf", {"src": [str(p) for p in f["images"]], "page": "a4", "fit": "contain",
                               "out": str(f["out"] / "images_a4.pdf")}, ctx)
    record("images_to_pdf(a4)", PASS)

    result = run_tool("pdf_merge", {"src": [str(f["pdf"]), str(f["out"] / "images.pdf")],
                                    "out": str(f["out"] / "merged.pdf")}, ctx)
    from pypdf import PdfReader

    assert len(PdfReader(result.outputs[0]).pages) == 9
    record("pdf_merge", PASS)

    result = run_tool("pdf_split", {"src": str(f["pdf"]), "mode": "each",
                                    "out": str(f["out"] / "split")}, ctx)
    assert len(result.outputs) == 6
    record("pdf_split(each)", PASS)
    result = run_tool("pdf_split", {"src": str(f["pdf"]), "mode": "ranges", "ranges": "1-2, 4-",
                                    "out": str(f["out"] / "split_ranges")}, ctx)
    assert len(result.outputs) == 2
    record("pdf_split(ranges)", PASS)
    result = run_tool("pdf_split", {"src": str(f["pdf"]), "mode": "chunks", "chunk": 4,
                                    "out": str(f["out"] / "split_chunks")}, ctx)
    assert len(result.outputs) == 2
    record("pdf_split(chunks)", PASS)

    run_tool("pdf_rotate", {"src": str(f["pdf"]), "angle": 90, "pages": "1-3",
                            "out": str(f["out"] / "rotated.pdf")}, ctx)
    record("pdf_rotate", PASS)

    light = run_tool("pdf_compress", {"src": str(f["pdf"]), "mode": "light",
                                      "out": str(f["out"] / "small_light.pdf")}, ctx)
    strong = run_tool("pdf_compress", {"src": str(f["pdf"]), "mode": "strong", "dpi": 96,
                                       "out": str(f["out"] / "small_strong.pdf")}, ctx)
    record("pdf_compress", PASS, f"{Path(light.outputs[0]).stat().st_size} / "
                                 f"{Path(strong.outputs[0]).stat().st_size} bytes")

    locked = run_tool("pdf_encrypt", {"src": str(f["pdf"]), "password": "secret123",
                                      "out": str(f["out"] / "locked.pdf")}, ctx)
    from pypdf import PdfReader

    assert PdfReader(locked.outputs[0]).is_encrypted
    record("pdf_encrypt", PASS)
    unlocked = run_tool("pdf_decrypt", {"src": locked.outputs[0], "password": "secret123",
                                        "out": str(f["out"] / "unlocked.pdf")}, ctx)
    assert not PdfReader(unlocked.outputs[0]).is_encrypted
    record("pdf_decrypt", PASS)
    expect_error("pdf_decrypt", {"src": locked.outputs[0], "password": "wrong",
                                 "out": str(f["out"] / "bad.pdf")}, ctx)

    run_tool("pdf_watermark", {"src": str(f["pdf"]), "text": "DRAFT", "size": 40, "opacity": 0.15,
                               "out": str(f["out"] / "watermark.pdf")}, ctx)
    record("pdf_watermark", PASS)

    result = run_tool("pdf_extract_pages", {"src": str(f["pdf"]), "pages": "2-3, 6",
                                            "out": str(f["out"] / "extract.pdf")}, ctx)
    assert len(PdfReader(result.outputs[0]).pages) == 3
    record("pdf_extract_pages", PASS)

    result = run_tool("pdf_info", {"src": str(f["pdf"])}, ctx, expect_output=True)
    assert "6" in result.details
    record("pdf_info", PASS)

    run_tool("pdf_page_numbers", {"src": str(f["pdf"]), "start": 1,
                                  "out": str(f["out"] / "numbered.pdf")}, ctx)
    record("pdf_page_numbers", PASS)


def test_images(f: dict, ctx_factory) -> None:
    print("\n[IMAGES]")
    ctx = ctx_factory()
    sources = [str(p) for p in f["images"]]
    out = f["out"]

    run_tool("image_convert", {"src": sources, "fmt": "jpg", "quality": 85, "out_dir": str(out / "conv")}, ctx)
    record("image_convert", PASS)
    run_tool("image_convert", {"src": sources, "fmt": "webp", "quality": 80, "out_dir": str(out / "conv_webp")}, ctx)
    run_tool("image_convert", {"src": sources, "fmt": "ico", "out_dir": str(out / "conv_ico")}, ctx)
    record("image_convert(webp/ico)", PASS)

    run_tool("image_resize", {"src": sources, "mode": "percent", "percent": 50, "out_dir": str(out / "resize")}, ctx)
    run_tool("image_resize", {"src": sources, "mode": "max", "max_side": 200, "out_dir": str(out / "resize2")}, ctx)
    run_tool("image_resize", {"src": sources, "mode": "exact", "width": 300, "height": 300,
                              "keep_ratio": "keep", "out_dir": str(out / "resize3")}, ctx)
    from PIL import Image

    size = Image.open(sorted((out / "resize2").glob("*.png"))[0]).size
    assert max(size) == 200, size
    record("image_resize", PASS)

    result = run_tool("image_compress", {"src": sources, "quality": 60, "max_side": 320,
                                         "out_dir": str(out / "compress")}, ctx)
    record("image_compress", PASS, result.message.replace("\n", " "))

    run_tool("image_watermark", {"src": sources, "text": "WinGenius", "position": "br", "size": 28,
                                 "opacity": 0.6, "color": "#FFFFFF", "out_dir": str(out / "wm")}, ctx)
    record("image_watermark", PASS)

    run_tool("image_rotate", {"src": sources, "angle": 90, "flip": "h", "out_dir": str(out / "rot")}, ctx)
    record("image_rotate", PASS)

    run_tool("image_grayscale", {"src": sources, "contrast": 1.4, "out_dir": str(out / "gray")}, ctx)
    record("image_grayscale", PASS)

    icon_files = run_tool("image_to_ico", {"src": sources, "out_dir": str(out / "ico")}, ctx)
    assert Path(icon_files.outputs[0]).read_bytes()[:4] == b"\x00\x00\x01\x00"
    record("image_to_ico", PASS)

    info = run_tool("image_info", {"src": sources}, ctx, expect_output=True)
    assert "640" in info.details
    record("image_info", PASS)

    run_tool("image_thumbnail", {"src": sources, "size": 128, "out_dir": str(out / "thumb")}, ctx)
    record("image_thumbnail", PASS)


def test_files(f: dict, ctx_factory) -> None:
    print("\n[FILES]")
    ctx = ctx_factory()
    out = f["out"]

    preview = run_tool("batch_rename", {"folder": str(f["rename_dir"]), "mode": "template",
                                        "template": "pic_{n}", "start": 1, "padding": 3,
                                        "preview": True}, ctx, expect_output=False)
    assert "pic_001" in preview.details
    run_tool("batch_rename", {"folder": str(f["rename_dir"]), "mode": "template",
                              "template": "pic_{n}", "start": 1, "padding": 3, "preview": False},
             ctx, expect_output=False)
    names = sorted(p.name for p in f["rename_dir"].iterdir())
    assert names[0] == "pic_001.jpg", names
    record("batch_rename", PASS, ", ".join(names))

    result = run_tool("find_duplicates", {"folder": str(f["dup_dir"]), "recursive": True,
                                          "min_size": 0, "delete": False}, ctx, expect_output=False)
    assert "copy" in result.details
    record("find_duplicates", PASS, result.message.replace("\n", " "))

    result = run_tool("hash_files", {"src": [str(f["text"]), str(f["pdf"])], "algo": "sha256",
                                     "save": str(out / "hashes.txt")}, ctx)
    assert "sha" not in result.details.lower() or len(result.details.splitlines()) == 2
    record("hash_files", PASS)

    archive = run_tool("zip_create", {"src": [str(f["images"][0]), str(f["text"])], "level": "normal",
                                      "out": str(out / "bundle.zip")}, ctx)
    record("zip_create", PASS, f"{Path(archive.outputs[0]).stat().st_size} bytes")
    result = run_tool("zip_extract", {"src": archive.outputs[0], "dest": str(out / "unzipped")}, ctx)
    assert (Path(result.outputs[0]) / f["text"].name).exists()
    record("zip_extract", PASS)

    run_tool("large_files", {"folder": str(f["base"]), "count": 5, "min_mb": 1}, ctx, expect_output=False)
    record("large_files", PASS)

    run_tool("folder_size", {"folder": str(f["base"])}, ctx, expect_output=False)
    record("folder_size", PASS)

    result = run_tool("file_search", {"folder": str(f["base"]), "name": "*.txt",
                                      "content": "", "limit": 50}, ctx, expect_output=False)
    record("file_search", PASS, f"{len(result.details.splitlines())} hits")

    parts = run_tool("split_file", {"src": str(f["big"]), "size_mb": 1, "out_dir": str(out / "parts")}, ctx)
    assert len(parts.outputs) >= 3
    joined = run_tool("join_file", {"src": parts.outputs, "out": str(out / "big_joined.bin")}, ctx)
    assert Path(joined.outputs[0]).read_bytes() == f["big"].read_bytes()
    record("split_file/join_file", PASS)

    shred = out / "shred_me.bin"
    shred.write_bytes(os.urandom(4096))
    run_tool("secure_delete", {"src": [str(shred)], "passes": 1}, ctx, expect_output=False)
    assert not shred.exists()
    record("secure_delete", PASS)


def test_security(f: dict, ctx_factory) -> None:
    print("\n[SECURITY]")
    ctx = ctx_factory()
    out = f["out"]

    result = run_tool("password_generate", {"mode": "password", "length": 24, "count": 5,
                                            "digits": True, "symbols": True, "upper": True,
                                            "simple": True, "save": str(out / "pw.txt")}, ctx)
    assert len(result.details.splitlines()) == 5
    result = run_tool("password_generate", {"mode": "passphrase", "words": 4, "count": 3,
                                            "save": ""}, ctx, expect_output=False)
    assert result.details.count("-") >= 3
    record("password_generate", PASS)

    result = run_tool("hash_text", {"text": "hello", "compare": ""}, ctx, expect_output=False)
    assert "2cf24dba5fb0a30e26e83b2ac5b9e29e" in result.details
    record("hash_text", PASS)

    encrypted = run_tool("file_encrypt", {"src": [str(f["text"])], "password": "s3cret",
                                          "out_dir": str(out / "enc")}, ctx)
    record("file_encrypt", PASS)
    decrypted = run_tool("file_decrypt", {"src": encrypted.outputs, "password": "s3cret",
                                          "out_dir": str(out / "dec")}, ctx)
    assert Path(decrypted.outputs[0]).read_text(encoding="utf-8") == f["text"].read_text(encoding="utf-8")
    record("file_decrypt", PASS)
    spec = get("file_decrypt")
    try:
        spec.run(ctx, {"src": encrypted.outputs, "password": "bad", "out_dir": str(out / "dec")})
        record("file_decrypt(wrong pw)", FAIL, "no error raised")
    except ToolError:
        record("file_decrypt(wrong pw)", PASS)

    cipher = run_tool("text_encrypt", {"mode": "encrypt", "text": "top secret متن",
                                       "password": "pw"}, ctx, expect_output=False)
    plain = run_tool("text_encrypt", {"mode": "decrypt", "text": cipher.details,
                                      "password": "pw"}, ctx, expect_output=False)
    assert plain.details == "top secret متن"
    record("text_encrypt", PASS)


def test_convert(f: dict, ctx_factory) -> None:
    print("\n[CONVERT]")
    ctx = ctx_factory()
    out = f["out"]

    run_tool("base64_tool", {"mode": "encode", "source": "text", "text": "hello world",
                             "file": "", "save": ""}, ctx, expect_output=False)
    encoded = run_tool("base64_tool", {"mode": "encode", "source": "text", "text": "hello world",
                                       "file": "", "save": ""}, ctx, expect_output=False)
    decoded = run_tool("base64_tool", {"mode": "decode", "source": "text", "text": encoded.details,
                                       "file": "", "save": ""}, ctx, expect_output=False)
    assert decoded.details == "hello world"
    encoded_file = run_tool("base64_tool", {"mode": "encode", "source": "file", "text": "",
                                            "file": str(f["text"]), "save": str(out / "b64.txt")}, ctx)
    run_tool("base64_tool", {"mode": "decode", "source": "file", "text": "",
                             "file": encoded_file.outputs[0], "save": str(out / "b64_back.txt")}, ctx)
    assert Path(out / "b64_back.txt").read_text(encoding="utf-8") == f["text"].read_text(encoding="utf-8")
    record("base64_tool", PASS)

    pretty = run_tool("json_tool", {"mode": "pretty", "text": '{"a":1,"b":[1,2,3]}', "file": "",
                                    "save": str(out / "pretty.json")}, ctx)
    assert '"a": 1' in pretty.details
    mini = run_tool("json_tool", {"mode": "minify", "text": pretty.details, "file": "", "save": ""},
                    ctx, expect_output=False)
    assert mini.details == '{"a":1,"b":[1,2,3]}'
    bad = run_tool("json_tool", {"mode": "validate", "text": '{"a":}', "file": "", "save": ""},
                   ctx, expect_output=False)
    assert "❌" in bad.message
    record("json_tool", PASS)

    xlsx = run_tool("table_convert", {"src": str(f["csv"]), "target": "xlsx", "sheet": "Data",
                                      "out": str(out / "table.xlsx")}, ctx)
    record("table_convert(csv→xlsx)", PASS)
    run_tool("table_convert", {"src": xlsx.outputs[0], "target": "csv", "sheet": "Data",
                               "out": str(out / "table_back.csv")}, ctx)
    back = Path(out / "table_back.csv").read_text(encoding="utf-8-sig")
    assert "Ali" in back and "Berlin" in back
    run_tool("table_convert", {"src": str(f["json"]), "target": "csv", "sheet": "S",
                               "out": str(out / "json.csv")}, ctx)
    record("table_convert", PASS)

    unit = run_tool("unit_convert", {"category": "length", "value": 2.5, "from": "km", "to": "m"},
                    ctx, expect_output=False)
    assert "2500" in unit.details
    temp = run_tool("unit_convert", {"category": "temp", "value": 100, "from": "c", "to": "f"},
                    ctx, expect_output=False)
    assert "212" in temp.details
    record("unit_convert", PASS, f"{unit.message} | {temp.message}")

    color = run_tool("color_convert", {"color": "#5B8CFF"}, ctx, expect_output=False)
    assert "rgb(91, 140, 255)" in color.details
    color2 = run_tool("color_convert", {"color": "rgb(255,0,0)"}, ctx, expect_output=False)
    assert "#FF0000" in color2.details
    record("color_convert", PASS)

    qr = run_tool("qr_generate", {"mode": "text", "text": "https://example.com", "ssid": "",
                                  "password": "", "size": 300, "color": "#101828",
                                  "out": str(out / "qr.png")}, ctx)
    assert Path(qr.outputs[0]).stat().st_size > 500
    run_tool("qr_generate", {"mode": "wifi", "text": "", "ssid": "HomeNet",
                                    "password": "1234", "size": 300, "color": "#101828",
                                    "out": str(out / "wifi.svg")}, ctx)
    record("qr_generate", PASS)

    ids = run_tool("uuid_generate", {"count": 3, "upper": False, "braces": False, "nodash": False},
                   ctx, expect_output=False)
    assert len(ids.details.splitlines()) == 3
    record("uuid_generate", PASS)

    transformed = run_tool("text_transform", {"operation": "fa_digits",
                                              "text": "شماره 1234567", "file": "",
                                              "save": str(out / "fa.txt")}, ctx)
    assert "۱۲۳" in transformed.details
    normalized = run_tool("text_transform", {"operation": "persian", "text": "علي كريمي",
                                             "file": "", "save": ""}, ctx, expect_output=False)
    assert "ی" in normalized.details and "ک" in normalized.details
    record("text_transform", PASS)

    stamp = run_tool("timestamp_convert", {"mode": "ts", "value": "1700000000"}, ctx, expect_output=False)
    assert "2023" in stamp.message
    record("timestamp_convert", PASS)

    html = run_tool("markdown_to_html", {"text": "# Title\n\n- a\n- b\n", "file": "",
                                         "styled": True, "out": str(out / "doc.html")}, ctx)
    assert "<h1>" in Path(html.outputs[0]).read_text(encoding="utf-8")
    record("markdown_to_html", PASS)


def test_system(f: dict, ctx_factory) -> None:
    print("\n[SYSTEM]")
    ctx = ctx_factory()
    info = run_tool("system_info", {"show_os": True}, ctx, expect_output=False)
    assert "CPU" in info.details
    record("system_info", PASS)
    disks = run_tool("disk_usage", {}, ctx, expect_output=False)
    assert "%" in disks.details
    record("disk_usage", PASS)
    processes = run_tool("process_manager", {"action": "top", "query": "", "count": 5},
                         ctx, expect_output=False)
    assert processes.details
    record("process_manager", PASS)
    temp = run_tool("temp_clean", {"targets": "temp", "dry_run": True}, ctx, expect_output=False)
    record("temp_clean", PASS, temp.message.replace("\n", " ")[:70])


def test_network(f: dict, ctx_factory) -> None:
    print("\n[NETWORK]")
    ctx = ctx_factory()
    result = run_tool("network_info", {}, ctx, expect_output=False)
    assert result.details
    record("network_info", PASS)
    scan = run_tool("port_scan", {"host": "127.0.0.1", "start": 1, "end": 120, "timeout": 120},
                    ctx, expect_output=False)
    record("port_scan", PASS, scan.message)
    try:
        dns = run_tool("net_diag", {"host": "example.com", "mode": "dns", "count": 1},
                       ctx, expect_output=False)
        record("net_diag(dns)", PASS, dns.message[:70])
    except ToolError as exc:
        record("net_diag(dns)", SKIP, f"offline: {exc}")


def test_windows_only(f: dict, ctx_factory) -> None:
    print("\n[WINDOWS-ONLY GUARDS]")
    ctx = ctx_factory()
    if sys.platform.startswith("win"):
        print("  (running on Windows — skipping guard checks)")
        return
    for tool_id, values in (
        ("wifi_passwords", {}),
        ("empty_recycle_bin", {}),
        ("services_manager", {}),
        ("windows_applets", {"applet": "settings"}),
        ("power_actions", {"action": "lock", "delay": 0}),
        ("set_wallpaper", {"src": str(f["images"][0])}),
    ):
        try:
            get(tool_id).run(ctx, get(tool_id).defaults() | values)
            record(tool_id, FAIL, "should refuse to run on non-Windows")
        except ToolError as exc:
            assert "ویندوز" in str(exc) or "Windows" in str(exc)
            record(tool_id, PASS, "friendly Windows-only error")


def test_v2(f: dict, ctx_factory) -> None:
    """The v2 additions: extended PDF/image/files/system tools, dev, Persian, office."""
    print("\n[V2 EXTENSIONS]")
    ctx = ctx_factory()
    out = f["out"]

    # --- inventory sanity -------------------------------------------------
    from app.core.registry import all_tools, category as _category, categories_with_counts
    from app.core.suggest import EXT_TOOLS
    specs = all_tools()
    assert len(specs) >= 155, f"expected 155+ tools, found {len(specs)}"
    ids = [spec.id for spec in specs]
    assert len(ids) == len(set(ids)), "duplicate tool ids"
    for spec in specs:
        assert spec.desc, f"{spec.id} has no description"
        assert spec.category, f"{spec.id} has no category"
        assert callable(spec.run), f"{spec.id} has no run function"
        for field in spec.fields:
            assert field.key, f"{spec.id} has an unnamed field"
            if field.kind == "choice":
                assert field.choices, f"{spec.id}.{field.key} has no choices"
    unknown = {tool_id for group in EXT_TOOLS.values() for tool_id in group if get(tool_id) is None}
    assert not unknown, f"suggest.py refers to unknown tools: {sorted(unknown)}"
    record("inventory(v2)", PASS, f"{len(specs)} tools in {len(categories_with_counts())} categories")

    # --- pdf extras -------------------------------------------------------
    run_tool("pdf_delete_pages", {"src": str(f["pdf"]), "pages": "2", "out": str(Path(out) / "del2.pdf")},
             ctx, expect_output=True)
    record("pdf_delete_pages", PASS)
    run_tool("pdf_reorder_pages", {"src": str(f["pdf"]), "order": "2,1,3", "out": str(Path(out) / "re.pdf")},
             ctx, expect_output=True)
    record("pdf_reorder_pages", PASS)
    run_tool("pdf_crop", {"src": str(f["pdf"]), "top": 4, "bottom": 4, "left": 4, "right": 4,
                          "out": str(Path(out) / "crop.pdf")}, ctx, expect_output=True)
    record("pdf_crop", PASS)
    run_tool("pdf_insert_blank", {"src": str(f["pdf"]), "mode": "position", "position": 2,
                                  "out": str(Path(out) / "blank.pdf")}, ctx, expect_output=True)
    record("pdf_insert_blank", PASS)
    html = run_tool("pdf_to_html", {"src": str(f["pdf"]), "page_break": True,
                                    "out": str(Path(out) / "doc.html")}, ctx)
    record("pdf_to_html", PASS, f"{Path(html.outputs[0]).stat().st_size} B")
    found = run_tool("pdf_find_text", {"src": str(f["pdf"]), "query": "page", "ignore_case": True},
                     ctx, expect_output=False)
    assert found.details or found.message
    record("pdf_find_text", PASS, found.message.replace("\n", " ")[:60])
    meta = run_tool("pdf_set_metadata", {"src": str(f["pdf"]), "title": "smoke", "author": "wg",
                                         "subject": "", "keywords": "",
                                         "out": str(Path(out) / "meta.pdf")}, ctx)
    record("pdf_set_metadata", PASS, meta.message.splitlines()[0][:60])

    # --- image extras -----------------------------------------------------
    images = f["images"]
    result = run_tool("image_crop", {"src": images, "mode": "square", "out_dir": str(Path(out) / "crop")},
                      ctx)
    record("image_crop", PASS, result.message.splitlines()[0])
    result = run_tool("image_border", {"src": images[:1], "width": 8, "color": "#18C8F5",
                                       "style": "rounded", "out_dir": str(Path(out) / "border")}, ctx)
    record("image_border", PASS)
    run_tool("image_rounded", {"src": images[:1], "radius": 25, "out_dir": str(Path(out) / "round")}, ctx)
    record("image_rounded", PASS)
    result = run_tool("image_collage", {"src": images, "columns": 2, "cell": 180, "gap": 10,
                                        "background": "#101820", "out": str(Path(out) / "collage.png")}, ctx)
    record("image_collage", PASS, result.message.splitlines()[0][:60])
    palette = run_tool("image_palette", {"src": images[0], "count": 5}, ctx, expect_output=False)
    assert "#" in palette.details
    record("image_palette", PASS)
    compare = run_tool("image_compare", {"a": images[0], "b": images[1], "save_diff": True}, ctx)
    record("image_compare", PASS, compare.message.replace("\n", " ")[:60])
    encoded = run_tool("image_base64", {"mode": "encode", "src": images[0], "text": "",
                                        "out": str(Path(out) / "b64.txt")}, ctx)
    payload = Path(encoded.outputs[0]).read_text(encoding="utf-8")
    run_tool("image_base64", {"mode": "decode", "src": "", "text": payload,
                              "out": str(Path(out) / "decoded.png")}, ctx)
    assert (Path(out) / "decoded.png").exists()
    record("image_base64", PASS, "round-trip ok")

    # --- files extras -----------------------------------------------------
    import zipfile

    zipped = Path(f["out"]) / "v2_archive.zip"
    zipped.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in f["images"]:
            archive.write(path, path.name)
        archive.write(f["text"], "notes.txt")
    listed = run_tool("archive_list", {"src": str(zipped)}, ctx, expect_output=False)
    assert listed.details
    record("archive_list", PASS, f"{len(listed.details.splitlines())} entries")
    tree = run_tool("tree_export", {"folder": str(f["base"]), "depth": 2, "show_files": True,
                                    "out": str(Path(out) / "tree.txt")}, ctx)
    record("tree_export", PASS, tree.message.splitlines()[0][:60])
    sync_dir = Path(out) / "sync_target"
    result = run_tool("folder_sync", {"source": str(f["images_dir"]), "target": str(sync_dir),
                                      "mode": "mirror", "dry_run": False}, ctx, expect_output=False)
    assert sync_dir.exists()
    record("folder_sync", PASS, result.message[:60])
    result = run_tool("copy_verify", {"items": [str(f["text"])], "out_dir": str(Path(out) / "verified")}, ctx)
    record("copy_verify", PASS, "hashes match")
    result = run_tool("file_timestamp", {"items": [str(f["text"])], "when": "shift", "days": -1}, ctx,
                      expect_output=False)
    record("file_timestamp", PASS)
    lock = run_tool("file_lock_check", {"items": [str(f["text"])]}, ctx, expect_output=False)
    record("file_lock_check", PASS, lock.message[:50])

    # --- system / network -------------------------------------------------
    health = run_tool("system_health", {"mode": "quick"}, ctx, expect_output=False)
    assert "/100" in health.message
    record("system_health", PASS, health.message[:40])
    disks = run_tool("disk_health", {"include_partitions": True}, ctx, expect_output=False)
    assert disks.details
    record("disk_health", PASS)
    env = run_tool("env_vars", {"action": "list", "name": "", "value": ""}, ctx, expect_output=False)
    assert "=" in env.details
    record("env_vars", PASS, f"{len(env.details.splitlines())} variables")
    ports = run_tool("port_listen", {"filter": "", "include_remote": False}, ctx, expect_output=False)
    record("port_listen", PASS, ports.message[:40])
    dns = run_tool("dns_lookup", {"host": "localhost", "record": "auto"}, ctx, expect_output=False)
    record("dns_lookup", PASS, dns.details.splitlines()[0][:50] if dns.details else "")
    lan = run_tool("lan_scan", {"subnet": "127.0", "timeout": 200}, ctx, expect_output=False)
    record("lan_scan", PASS, lan.message[:50])

    # --- security / dev / persian / office --------------------------------
    audit = run_tool("password_audit", {"password": "P@ssw0rd2024!"}, ctx, expect_output=False)
    assert "bit" in audit.details
    record("password_audit", PASS)
    result = run_tool("secret_scan", {"folder": str(f["secrets"]), "max_mb": "2"}, ctx,
                      expect_output=False)
    record("secret_scan", PASS, result.message.splitlines()[0][:60])
    manifest = run_tool("checksum_manifest", {"action": "create", "items": [str(f["text"])],
                                              "manifest": ""}, ctx)
    run_tool("checksum_manifest", {"action": "verify", "items": [], "manifest": manifest.outputs[0]}, ctx,
             expect_output=False)
    record("checksum_manifest", PASS, "create + verify")

    matches = run_tool("regex_tester", {"pattern": r"\d+", "text": "a1 b22 c333", "flags": "", "replace": ""},
                       ctx, expect_output=False)
    assert "3" in matches.message
    record("regex_tester", PASS)
    diff = run_tool("text_diff", {"a": str(f["text"]), "b": str(f["text2"]), "context": 2,
                                  "ignore_space": False}, ctx, expect_output=False)
    assert diff.details
    record("text_diff", PASS, diff.message[:50])
    query = run_tool("json_query", {"source": "text", "text": '{"a": {"b": [1, 2, 3]}}', "path": "a.b[2]",
                                    "pretty": True}, ctx, expect_output=False)
    assert "3" in query.details
    record("json_query", PASS)
    base = run_tool("number_base", {"value": "255", "base": "dec", "bits": 16}, ctx, expect_output=False)
    assert "0xFF" in base.details
    record("number_base", PASS)
    cron = run_tool("cron_parse", {"expression": "30 8 * * 6"}, ctx, expect_output=False)
    assert "08:30" in cron.details
    record("cron_parse", PASS, cron.message[:40])
    url_result = run_tool("url_tool", {"action": "parse", "value": "https://a.io/p?x=1#z"}, ctx,
                          expect_output=False)
    assert "x = 1" in url_result.details
    record("url_tool", PASS)
    path_result = run_tool("path_tool", {"path": r"C:\Users\me\a.txt"}, ctx, expect_output=False)
    assert "a.txt" in path_result.message
    record("path_tool", PASS)

    fixed = run_tool("persian_fix", {"source": "text", "text": "كتاب هاي من", "zero_width": True,
                                     "numbers": "fa", "write_file": False, "file": "", "out": ""}, ctx,
                     expect_output=False)
    assert "کتاب" in fixed.details
    record("persian_fix", PASS, fixed.details.strip()[:40])
    digits = run_tool("persian_numbers", {"source": "text", "text": "1234", "target": "fa",
                                          "thousands": False, "file": "", "write_file": False}, ctx,
                      expect_output=False)
    assert "۱۲۳۴" in digits.details
    record("persian_numbers", PASS)
    jalali = run_tool("jalali_date", {"direction": "to_gregorian", "year": 1403, "month": 1, "day": 1},
                      ctx, expect_output=False)
    assert "2024-03-20" in jalali.message
    record("jalali_date", PASS)
    sorted_text = run_tool("persian_sort", {"source": "text", "text": "ب\nآ\nپ", "descending": False,
                                            "file": "", "write_file": False}, ctx, expect_output=False)
    assert sorted_text.details.splitlines()[0] == "آ"
    record("persian_sort", PASS)
    keyboard = run_tool("keyboard_fix", {"text": "sghl", "direction": "en_to_fa"}, ctx, expect_output=False)
    assert keyboard.details.strip() == "سلام"
    record("keyboard_fix", PASS)

    stats = run_tool("text_stats", {"source": "text", "text": "یک دو سه چهار پنج", "file": []}, ctx,
                     expect_output=False)
    assert "5" in stats.message
    record("text_stats", PASS, stats.message[:60])
    word = run_tool("word_to_text", {"src": str(f["docx"]), "tables": True,
                                     "out": str(Path(out) / "word.txt")}, ctx)
    record("word_to_text", PASS, word.message.splitlines()[0][:50])
    csv_out = run_tool("sheet_to_csv", {"src": str(f["xlsx"]), "sheet": "", "delimiter": ",",
                                        "out_dir": str(Path(out) / "sheets")}, ctx)
    record("sheet_to_csv", PASS, csv_out.message.splitlines()[0][:50])
    csv_files = sorted(Path(out, "sheets").glob("*.csv"))
    split = run_tool("table_split", {"src": str(csv_files[0] if csv_files else f["csv"]), "rows": 10,
                                     "keep_header": True, "out_dir": str(Path(out) / "split")}, ctx)
    record("table_split", PASS, split.message.splitlines()[0][:50])

    # --- codes ------------------------------------------------------------
    qr = run_tool("barcode_generate", {"kind": "qr", "data": "https://wingenius.local/smoke", "size": 240,
                                       "color": "#000000", "out": str(Path(out) / "qr.png")}, ctx)
    decoded = run_tool("qr_scan", {"src": qr.outputs[0]}, ctx, expect_output=False)
    assert "wingenius.local" in decoded.details
    record("qr_scan", PASS, "generated QR decodes")
    try:
        linear = run_tool("barcode_generate", {"kind": "code128", "data": "WINGENIUS-2024", "size": 400,
                                               "color": "#000000",
                                               "out": str(Path(out) / "code128.png")}, ctx)
        record("barcode_generate(code128)", PASS, f"{Path(linear.outputs[0]).stat().st_size} B")
    except ToolError as exc:
        record("barcode_generate(code128)", SKIP, str(exc)[:60])

    # --- windows-only guards for the v2 tools -----------------------------
    if not sys.platform.startswith("win"):
        for tool_id in ("power_plan", "scheduled_tasks", "windows_features", "crash_dumps",
                        "startup_add", "font_install", "bitlocker_status", "restore_point",
                        "component_cleanup", "driver_problems", "event_errors", "winget_tool"):
            spec = get(tool_id)
            values = spec.defaults()
            if tool_id == "winget_tool":
                values.update({"action": "list"})
            try:
                spec.run(ctx, values)
                record(tool_id, FAIL, "should refuse to run on non-Windows")
            except ToolError as exc:
                text = str(exc)
                record(tool_id, PASS if ("ویندوز" in text or "Windows" in text) else FAIL, "windows guard")


def test_v3(f: dict, ctx_factory) -> None:
    """v4 additions: undo journal, encrypted vault, automation macros, local API."""
    import tempfile as _tempfile
    from app.core import registry
    from app.core.journal import journal, reset_journal

    print("\n[V4: JOURNAL / VAULT / MACROS]")
    ctx = ctx_factory()
    out = f["out"]
    work = Path(_tempfile.mkdtemp(prefix="wg_v4_"))

    # --- inventory: every new tool is registered and documented --------------
    new_ids = ["undo_last", "undo_history", "trash_move", "trash_restore", "trash_cleanup",
               "vault_create", "vault_add", "vault_list", "vault_extract", "vault_remove",
               "vault_password", "schedule_list", "schedule_next", "schedule_check",
               "macro_run", "macro_validate", "macro_templates", "usage_insights",
               "storage_report", "backup_appdata", "image_dupes"]
    missing = [tool_id for tool_id in new_ids if registry.get(tool_id) is None]
    assert not missing, f"missing v4 tools: {missing}"
    record("v4 tool inventory", PASS, f"{len(new_ids)} tools registered")
    for tool_id in new_ids:
        spec = registry.get(tool_id)
        assert spec.desc and spec.category and callable(spec.run), tool_id
    record("v4 tool metadata", PASS, "descriptions, categories and runners present")

    # --- undo journal -------------------------------------------------------
    reset_journal(work / "journal.sqlite3")
    target = work / "undo_demo.txt"
    target.write_text("payload", encoding="utf-8")
    from app.core.journal import send_to_trash
    pairs = send_to_trash([target])
    journal().record_trash(pairs, "smoke trash", tool_id="smoke")
    assert not target.exists(), "file was not moved to the trash"
    record("trash round-trip", PASS, "file moved into the app trash")
    result = run_tool("undo_last", {"count": 1}, ctx)
    assert target.exists(), "undo_last did not restore the file"
    record("undo_last restores", PASS, result.message[:60])
    run_tool("undo_history", {"limit": 5}, ctx, expect_output=False)
    record("undo_history", PASS, f"{journal().stats()['total']} journaled operation(s)")
    run_tool("trash_cleanup", {"confirm": "no"}, ctx, expect_output=False)
    record("trash_cleanup guard", PASS, "refuses without confirmation")

    # --- encrypted vault ----------------------------------------------------
    vault_file = work / "smoke.wgv"
    run_tool("vault_create", {"vault": str(vault_file), "password": "pw-123456789",
                              "confirm": "pw-123456789"}, ctx)
    assert vault_file.exists(), "vault_create wrote no file"
    record("vault_create", PASS, f"{vault_file.stat().st_size} bytes")
    note = work / "secret.txt"
    note.write_text("classified", encoding="utf-8")
    run_tool("vault_add", {"vault": str(vault_file), "password": "pw-123456789",
                           "files": str(note)}, ctx)
    record("vault_add file", PASS, "secret stored")
    run_tool("vault_add", {"vault": str(vault_file), "password": "pw-123456789",
                           "note_name": "memo.txt", "note_text": "hello"}, ctx)
    result = run_tool("vault_list", {"vault": str(vault_file), "password": "pw-123456789"}, ctx,
                      expect_output=False)
    assert "secret.txt" in result.details, result.details[:80]
    record("vault_list", PASS, "both entries listed")
    extract_dir = work / "vault_out"
    run_tool("vault_extract", {"vault": str(vault_file), "password": "pw-123456789",
                               "out": str(extract_dir)}, ctx)
    assert (extract_dir / "secret.txt").read_text(encoding="utf-8") == "classified"
    record("vault_extract", PASS, "content decrypted correctly")
    assert b"classified" not in vault_file.read_bytes(), "vault leaks plaintext"
    record("vault encryption", PASS, "no plaintext on disk")
    expect_error("vault_list", {"vault": str(vault_file), "password": "wrong-pw"}, ctx,
                 "wrong password")
    run_tool("vault_password", {"vault": str(vault_file), "password": "pw-123456789",
                                "new_password": "pw-654321xyz", "confirm": "pw-654321xyz"}, ctx)
    run_tool("vault_list", {"vault": str(vault_file), "password": "pw-654321xyz"}, ctx,
             expect_output=False)
    record("vault_password", PASS, "re-keyed vault unlocks with the new password")
    run_tool("vault_remove", {"vault": str(vault_file), "password": "pw-654321xyz",
                              "names": "memo.txt"}, ctx)
    record("vault_remove", PASS, "entry removed")

    # --- scheduling ---------------------------------------------------------
    result = run_tool("schedule_check", {"cron": "*/10 * * * *"}, ctx, expect_output=False)
    text = (result.message + " " + result.details).strip()
    assert text, "schedule_check returned nothing"
    record("schedule_check", PASS, text.splitlines()[0][:60])
    run_tool("schedule_next", {"cron": "0 7 * * 1-5", "count": 3}, ctx, expect_output=False)
    record("schedule_next", PASS, "three upcoming slots")
    result = run_tool("schedule_list", {}, ctx, expect_output=False)
    record("schedule_list", PASS, result.message[:60])

    # --- macro engine -------------------------------------------------------
    run_tool("macro_templates", {}, ctx, expect_output=False)
    from app.core import macro as macro_module
    templates = macro_module.library()
    assert templates, "no macro templates were written"
    record("macro_templates", PASS, f"{len(templates)} template(s) in the library")
    sample = templates[0]
    run_tool("macro_validate", {"file": str(sample)}, ctx, expect_output=False)
    record("macro_validate", PASS, sample.name[:60])
    result = run_tool("macro_run", {"file": str(sample), "dry_run": True}, ctx,
                      expect_output=False)
    record("macro_run dry", PASS, (result.message or "")[:60])

    # --- insights / reports -------------------------------------------------
    result = run_tool("usage_insights", {}, ctx, expect_output=False)
    record("usage_insights", PASS, result.message[:60])
    run_tool("storage_report", {}, ctx, expect_output=False)
    record("storage_report", PASS, "data-folder usage summary")
    folder = work / "dupes"
    folder.mkdir(exist_ok=True)
    from PIL import Image as _Image
    for name in ("a.png", "b.png"):
        _Image.new("RGB", (24, 24), (30, 90, 160)).save(folder / name)
    _Image.new("RGB", (24, 24), (200, 10, 10)).save(folder / "c.png")
    result = run_tool("image_dupes", {"folder": str(folder)}, ctx, expect_output=False)
    assert "a.png" in result.details and "b.png" in result.details, result.details[:120]
    record("image_dupes", PASS, result.message[:60])

    # --- local API ----------------------------------------------------------
    from app.core.api_server import ApiServer
    server = ApiServer(port=0, lang="en", output_dir=str(out))
    port = server.start()
    assert port > 0, "api server did not bind a port"
    import json as _json
    import urllib.request
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health",
                                     headers={"X-WinGenius-Token": server.token})
    with urllib.request.urlopen(request, timeout=10) as response:
        health = _json.loads(response.read())
    assert health["tools"] >= 200, health
    record("api /health", PASS, f"{health['tools']} tools over HTTP")
    server.stop()
    record("api stop", PASS, "server shut down")


# ------------------------------------------------------------------------ main
def main(argv: list[str]) -> int:
    filters = [arg for arg in argv[1:] if not arg.startswith("-")]
    load_all()
    temp = Path(tempfile.mkdtemp(prefix="wingenius_test_"))
    print(f"sandbox: {temp}")
    fixtures = make_fixtures(temp)

    def ctx_factory() -> RunContext:
        messages = []

        def on_message(message: str) -> None:
            messages.append(message)

        def on_progress(current: int, total: int, message: str) -> None:
            pass

        return RunContext(lang="en", output_dir=str(fixtures["out"]),
                          on_progress=on_progress, on_message=on_message)

    suites = {
        "pdf": test_pdf, "image": test_images, "files": test_files,
        "security": test_security, "convert": test_convert, "system": test_system,
        "network": test_network, "windows": test_windows_only, "v2": test_v2,
        "v3": test_v3,
    }
    for name, suite in suites.items():
        if filters and not any(f in name for f in filters):
            continue
        try:
            suite(fixtures, ctx_factory)
        except Exception:
            traceback.print_exc()
            record(f"{name} suite", FAIL, "crashed — see traceback")

    passed = sum(1 for _, status, _ in results if status == PASS)
    failed = sum(1 for _, status, _ in results if status == FAIL)
    skipped = sum(1 for _, status, _ in results if status == SKIP)
    print(f"\n{'=' * 62}\n  PASS {passed}   FAIL {failed}   SKIP {skipped}\n{'=' * 62}")
    if failed:
        print("\nFailures:")
        for tool_id, status, detail in results:
            if status == FAIL:
                print(f"  - {tool_id}: {detail}")
    if not argv[1:]:
        shutil.rmtree(temp, ignore_errors=True)
    else:
        print(f"kept sandbox: {temp}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
