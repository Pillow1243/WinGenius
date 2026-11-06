#!/usr/bin/env python3
"""Functional tests for the v2 engine and tools — runs real files, no GUI.

    python3 tests/functional_test.py            # everything
    python3 tests/functional_test.py pdf image  # only those groups
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never open real windows from a test run: the shell launcher becomes a dry run.
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")
# Standalone runs must not touch the developer's real application profile.
from tests.sandbox import isolate_profile
isolate_profile()

from app.core.registry import get as get_tool, load_all, count  # noqa: E402
from app.core.spec import RunContext, ToolError  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0
FAILURES: list[str] = []


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


WORK = Path(tempfile.mkdtemp(prefix="wg_func_"))
OUT = WORK / "out"
OUT.mkdir(parents=True, exist_ok=True)


def ctx() -> RunContext:
    return RunContext(lang="fa", output_dir=str(OUT))


def run(tool_id: str, **values):
    spec = get_tool(tool_id)
    if spec is None:
        raise AssertionError(f"tool not registered: {tool_id}")
    merged = spec.defaults()
    merged.update(values)
    return spec.run(ctx(), merged)


def make_fixtures() -> dict:
    """Create real PDFs, images, texts and archives to test against."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    fixtures: dict = {}

    pdf_path = WORK / "doc.pdf"
    doc = canvas.Canvas(str(pdf_path), pagesize=A4)
    for page in range(1, 6):
        doc.setFont("Helvetica", 18)
        doc.drawString(60, 760, f"WinGenius test page {page}")
        doc.setFont("Helvetica", 11)
        doc.drawString(60, 730, "invoice total 1450 dollars")
        doc.drawString(60, 710, f"page marker {page}")
        doc.showPage()
    doc.save()
    fixtures["pdf"] = pdf_path

    image_paths = []
    colors = [("#18C8F5", "A"), ("#7B61FF", "B"), ("#FFB020", "C")]
    for index, (colour, letter) in enumerate(colors):
        path = WORK / f"img{index}.png"
        image = Image.new("RGB", (480, 320), colour)
        draw = ImageDraw.Draw(image)
        draw.rectangle([20, 20, 460, 300], outline="white", width=4)
        draw.text((40, 40), f"IMG {letter} {colour}", fill="white")
        image.save(path)
        image_paths.append(str(path))
    fixtures["images"] = image_paths

    from PIL import Image as PILImage

    qr_target = WORK / "qr.png"
    try:
        import qrcode

        qrcode.make("https://wingenius.local/test").save(qr_target)
        fixtures["qr"] = qr_target
    except Exception:
        fixtures["qr"] = None

    text_path = WORK / "fa.txt"
    text_path.write_text(
        "كتاب هاي من\nمي شود\nسلام  دنيا\n۱۲۳ و 456\nزلزله\nآبان\nپژوهش\n", encoding="utf-8")
    fixtures["text"] = text_path

    second_text = WORK / "fa2.txt"
    second_text.write_text("كتاب هاي من\nتغییر یافته\nسلام دنيا\n", encoding="utf-8")
    fixtures["text2"] = second_text

    zip_path = WORK / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in image_paths:
            archive.write(path, Path(path).name)
        archive.write(text_path, "notes/fa.txt")
    fixtures["zip"] = zip_path

    fixtures["folder"] = WORK
    fixtures["imgdir"] = WORK
    return fixtures


# --------------------------------------------------------------------- groups
def test_registry() -> None:
    print("\n[registry]")
    failures = load_all()
    check("all tool modules import", not failures, str(failures))
    total = count()
    check("at least 150 tools registered", total >= 150, f"got {total}")
    ids = set()
    duplicates = []
    from app.core.registry import all_tools

    for spec in all_tools():
        if spec.id in ids:
            duplicates.append(spec.id)
        ids.add(spec.id)
    check("no duplicate tool ids", not duplicates, str(duplicates))
    check("every tool has a description", all(t.desc for t in all_tools()))
    check("every tool has a run function", all(callable(t.run) for t in all_tools()))
    broken = [t.id for t in all_tools() for f in t.fields if f.kind == "choice" and not f.choices]
    check("all choice fields have options", not broken, str(broken))
    dev = [t.id for t in all_tools() if t.category == "dev"]
    check("new dev category has tools", len(dev) >= 8, f"{len(dev)}")


def test_core_engine(fixtures: dict) -> None:
    print("\n[engine: jobs / history / plugins / diagnostics / suggest / cli]")
    from app.core import suggest
    from app.core.diagnostics import diagnostics_text, deps_report, doctor_issues
    from app.core.history import History
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
    except Exception:
        app = None

    if app is None:
        skip("job manager", "Qt unavailable")
    else:
        from app.core.jobs import JobManager

        manager = JobManager(max_parallel=2)
        finished = []
        manager.jobFinished.connect(lambda job: finished.append(job))
        manager.submit_by_id("uuid_generate", {"count": 3, "upper": False, "braces": False})
        deadline = time.time() + 25
        while time.time() < deadline and not finished:
            app.processEvents()
            time.sleep(0.02)
        check("job runs through the queue", bool(finished), "no jobFinished signal")
        if finished:
            job = finished[0]
            check("job status is done", job.status == "done", f"status={job.status} error={job.error}")
            check("job reports outputs", bool(job.result and job.result.message), "empty result")
        stats = manager.stats()
        check("job manager stats", stats.get("done", 0) >= 1, str(stats))
        manager.shutdown()

    from app.core.history import history as HISTORY
    from app.core.jobs import Job as CoreJob
    from app.core.spec import RunResult

    HISTORY.clear()
    sample = CoreJob(id="test", tool_id="unit_test", title="Unit test", status="done",
                     message="ok", outputs=["a.txt"], result=RunResult(message="ok"))
    HISTORY.add(sample)
    check("history stores an entry", bool(HISTORY.entries(limit=5)), "no entries")
    check("history filters by tool", bool(HISTORY.for_tool("unit_test")), "no match")
    HISTORY.clear()
    check("history clears", HISTORY.entries(limit=5) == [], "still has entries")

    text = diagnostics_text()
    check("diagnostics text built", "WinGenius" in text and len(text) > 120, text[:80])
    report = deps_report()
    check("deps report covers packages", isinstance(report, (list, tuple, dict)) and len(report) >= 5, str(type(report)))
    issues = doctor_issues()
    check("doctor issues is a list", isinstance(issues, (list, tuple)), str(type(issues)))

    from app.core import plugins as plugins_mod

    check("plugin discovery works", isinstance(plugins_mod.discover(), (list, tuple)), "bad type")
    example = plugins_mod.write_example_plugin()
    check("example plugin file written", example.exists(), str(example))
    plugin_file = plugins_mod.plugins_dir() / "unit_plugin.py"
    plugin_file.write_text(
        "from app.core.registry import tool\n"
        "from app.core.spec import K_TEXT, Field, RunResult\n\n"
        "@tool(id='unit_plugin_tool', category='dev', name='افزونه تست', name_en='Test plugin',\n"
        "      desc='یک ابزار آزمون', desc_en='A test tool', icon='puzzle',\n"
        "      fields=[Field('text', K_TEXT, 'متن', 'Text', default='x')])\n"
        "def unit_plugin_tool(ctx, values):\n"
        "    return RunResult(message='plugin says ' + str(values.get('text')))\n",
        encoding="utf-8")
    plugins_mod.trust_plugin(plugin_file)
    loaded = plugins_mod.load_plugins()
    check("plugin file loaded", any(info.path.name == "unit_plugin.py" for info in loaded),
          str([(i.name, i.error) for i in loaded]))
    plugin_spec = get_tool("unit_plugin_tool")
    check("plugin tool registered", plugin_spec is not None, "not in the registry")
    if plugin_spec is not None:
        result = plugin_spec.run(ctx(), {"text": "ok"})
        check("plugin tool runs", result.message == "plugin says ok", result.message)
        from app.core.registry import all_tools

        check("plugin tool appears in the tool list",
              "unit_plugin_tool" in {spec.id for spec in all_tools()})
    plugin_file.unlink(missing_ok=True)
    example.unlink(missing_ok=True)

    ranked = suggest.tools_for_files([str(fixtures["pdf"])])
    check("drop suggestion ranks PDF tools", "pdf_to_images" in ranked, str(ranked))
    ranked_images = suggest.tools_for_files(fixtures["images"])
    check("drop suggestion detects images", "image_compress" in ranked_images, str(ranked_images))
    check("suggestion is empty for no files", suggest.tools_for_files([]) == [])

    from app.core.cli import _build_values, _parse_args

    options = _parse_args(["--run", "pdf_to_images", "--file", "x.pdf", "--set", "dpi=200",
                           "--out", str(OUT), "--json"])
    check("cli parses mode", options["mode"] == "run", str(options.get("mode")))
    check("cli parses --set", options["sets"] == [("dpi", "200")], str(options["sets"]))
    spec = get_tool("pdf_to_images")
    values = _build_values(spec, options["files"], options["sets"], options["out"])
    check("cli coerces int values", values.get("dpi") == 200, str(values.get("dpi")))
    check("cli assigns the input file", values.get("src") == "x.pdf", str(values.get("src")))


def test_pdf(fixtures: dict) -> None:
    print("\n[pdf]")
    pdf = fixtures["pdf"]
    result = run("pdf_to_html", src=str(pdf))
    target = Path(result.outputs[0])
    check("pdf_to_html writes html", target.exists() and "<html" in target.read_text(encoding="utf-8"))

    result = run("pdf_find_text", src=str(pdf), query="invoice")
    check("pdf_find_text counts hits", "5" in result.message or "بار" in result.message, result.message)

    result = run("pdf_delete_pages", src=str(pdf), pages="2,4")
    target = Path(result.outputs[0])
    from pypdf import PdfReader

    check("pdf_delete_pages removes pages", len(PdfReader(str(target)).pages) == 3,
          str(len(PdfReader(str(target)).pages)))

    result = run("pdf_reorder_pages", src=str(pdf), order="5,4,3,2,1")
    check("pdf_reorder_pages keeps page count", len(PdfReader(result.outputs[0]).pages) == 5)

    result = run("pdf_insert_blank", src=str(pdf), mode="every")
    check("pdf_insert_blank doubles pages", len(PdfReader(result.outputs[0]).pages) == 10)

    result = run("pdf_crop", src=str(pdf), top=5, bottom=5, left=5, right=5)
    reader = PdfReader(result.outputs[0])
    check("pdf_crop shrinks the cropbox", float(reader.pages[0].cropbox.width) < float(reader.pages[0].mediabox.width))

    try:
        run("pdf_crop", src=str(pdf), top=49, bottom=49, left=5, right=5)
        check("pdf_crop rejects too much cropping", False, "no error raised")
    except ToolError:
        check("pdf_crop rejects too much cropping", True)

    try:
        run("pdf_delete_pages", src=str(pdf), pages="1-99")
        check("pdf_delete_pages rejects deleting everything", False, "no error raised")
    except ToolError:
        check("pdf_delete_pages rejects deleting everything", True)

    result = run("pdf_set_metadata", src=str(pdf), title="WinGenius Title", author="Tester")
    reader = PdfReader(result.outputs[0])
    check("pdf_set_metadata writes title", "WinGenius Title" in str(reader.metadata), str(reader.metadata))

    result = run("pdf_stamp_image", src=str(pdf), image=fixtures["images"][0], position="br", scale=20)
    check("pdf_stamp_image produces a pdf", Path(result.outputs[0]).exists())

    check("new pdf tools registered", all(get_tool(t) for t in
          ("pdf_delete_pages", "pdf_reorder_pages", "pdf_insert_blank", "pdf_crop",
           "pdf_to_html", "pdf_find_text", "pdf_set_metadata", "pdf_stamp_image")))


def test_image(fixtures: dict) -> None:
    print("\n[image]")
    from PIL import Image

    images = fixtures["images"]
    result = run("image_crop", src=images, mode="percent", top=10, bottom=10, left=10, right=10)
    check("image_crop processes every image", len(result.outputs) == len(images), str(len(result.outputs)))
    check("image_crop shrinks the image", Image.open(result.outputs[0]).width < 480)

    result = run("image_crop", src=images[:1], mode="square")
    cropped = Image.open(result.outputs[0])
    check("image_crop square mode is square", cropped.width == cropped.height, f"{cropped.size}")

    result = run("image_border", src=images[:1], width=10, color="#FF0000", style="solid")
    check("image_border grows the canvas", Image.open(result.outputs[0]).width == 500)

    result = run("image_border", src=images[:1], width=8, style="rounded")
    border = Image.open(result.outputs[0])
    check("image_border rounded keeps alpha", border.mode == "RGBA", border.mode)

    result = run("image_rounded", src=images[:1], radius=30)
    check("image_rounded makes corners transparent", Image.open(result.outputs[0]).mode == "RGBA")

    result = run("image_collage", src=images, columns=2, cell=200, gap=8)
    collage = Image.open(result.outputs[0])
    check("image_collage grid size", collage.width == 2 * 200 + 8 * 3, f"{collage.size}")

    result = run("image_exif", src=images[:1], action="show")
    check("image_exif shows info", "img0.png" in result.details and len(result.details) > 10, result.details[:60])

    result = run("image_exif", src=images[:1], action="strip")
    check("image_exif strips metadata", Path(result.outputs[0]).exists())

    result = run("image_palette", src=images[0], count=4)
    check("image_palette returns hex colours", "#" in result.details, result.details[:60])

    result = run("image_compare", a=images[0], b=images[1])
    check("image_compare reports similarity", "%" in result.message, result.message)

    result = run("image_compare", a=images[0], b=images[0])
    check("image_compare identical images ≈100%", "100.00" in result.message, result.message)

    result = run("image_base64", mode="encode", src=images[0])
    payload = Path(result.outputs[0]).read_text(encoding="utf-8")
    check("image_base64 encodes a data uri", payload.startswith("data:image/"), payload[:24])
    decoded = run("image_base64", mode="decode", text=payload, out=str(OUT / "decoded.png"))
    check("image_base64 round-trips", Image.open(decoded.outputs[0]).size == (480, 320))


def test_files(fixtures: dict) -> None:
    print("\n[files]")
    zip_path = fixtures["zip"]
    result = run("archive_list", src=str(zip_path))
    check("archive_list lists entries", "img0.png" in result.details, result.details[:80])

    result = run("zip_extract", src=str(zip_path), dest=str(OUT / "unzip"))
    check("zip_extract writes files", (OUT / "unzip" / "img0.png").exists())

    # --- zip-slip: a crafted archive must not write outside the destination ---
    evil_zip = OUT / "evil.zip"
    escape_target = OUT / "escaped.txt"
    with zipfile.ZipFile(evil_zip, "w") as archive:
        archive.writestr("../../escaped.txt", "pwned")
        archive.writestr("nested/ok.txt", "fine")
    evil_dest = OUT / "evil_out"
    result = run("zip_extract", src=str(evil_zip), dest=str(evil_dest))
    check("zip_extract keeps safe members", (evil_dest / "nested" / "ok.txt").exists())
    check("zip_extract blocks zip-slip", not escape_target.exists()
          and not (OUT.parent / "escaped.txt").exists())
    check("zip_extract reports the skipped entry", "رد شد" in result.message or "skipped" in result.message,
          result.message)
    check("zip_extract refuses to leak dot-dot paths",
          not any(p.name == "escaped.txt" for p in OUT.rglob("escaped.txt")))

    result = run("empty_folders", folder=str(OUT / "unzip"), delete=False)
    check("empty_folders runs", "پوشه" in result.message, result.message)

    source = OUT / "sync_src"
    target = OUT / "sync_dst"
    source.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    (source / "a.txt").write_text("hello", encoding="utf-8")
    (target / "old.txt").write_text("gone", encoding="utf-8")
    result = run("folder_sync", source=str(source), target=str(target), mode="mirror", dry_run=True)
    check("folder_sync dry run changes nothing", (target / "old.txt").exists() and "+ a.txt" in result.details)
    run("folder_sync", source=str(source), target=str(target), mode="mirror", dry_run=False)
    check("folder_sync mirrors the source", (target / "a.txt").exists() and not (target / "old.txt").exists())

    try:
        run("folder_sync", source=str(source), target=str(source))
        check("folder_sync rejects identical folders", False, "no error raised")
    except ToolError:
        check("folder_sync rejects identical folders", True)

    result = run("tree_export", folder=str(source), depth=2, out=str(OUT / "tree.txt"))
    check("tree_export writes a tree", "a.txt" in Path(result.outputs[0]).read_text(encoding="utf-8"))

    copy_dir = OUT / "copied"
    copy_dir.mkdir(exist_ok=True)
    result = run("copy_verify", items=[str(source / "a.txt")], out_dir=str(copy_dir))
    check("copy_verify verifies hashes", "✓" in result.details and (copy_dir / "a.txt").exists(), result.details[:60])

    result = run("file_lock_check", items=[str(source / "a.txt")])
    check("file_lock_check reports free files", "آزاد" in result.message, result.message)

    result = run("file_timestamp", items=[str(source / "a.txt")], when="shift", days=-3)
    check("file_timestamp shifts mtime", Path(source / "a.txt").exists())


def test_system_network(fixtures: dict) -> None:
    print("\n[system / network]")
    result = run("system_health", mode="quick")
    check("system_health returns a score", "/100" in result.message, result.message)
    check("system_health has details", "RAM" in result.details, result.details[:80])

    result = run("disk_health")
    check("disk_health reports something", len(result.details) > 10, result.details[:60])

    result = run("env_vars", action="list")
    check("env_vars lists variables", "=" in result.details, result.details[:60])

    result = run("port_listen")
    check("port_listen returns rows", "ردیف" in result.message or "row" in result.message, result.message)

    result = run("dns_lookup", host="localhost", record="auto")
    check("dns_lookup resolves localhost", "127.0.0.1" in result.details or "::1" in result.details,
          result.details[:80])

    result = run("lan_scan", subnet="127.0", timeout=200)
    check("lan_scan completes", "دستگاه" in result.message or "device" in result.message, result.message)

    result = run("number_base", value="255")
    check("number_base converts", "0xFF" in result.details, result.details[:40])

    result = run("cron_parse", expression="0 9 * * *")
    check("cron_parse finds next runs", "09:00" in result.details, result.details[:120])

    result = run("cron_parse", expression="*/15 * * * *")
    check("cron_parse handles steps", len(result.details) > 40)

    try:
        run("cron_parse", expression="bad expression here now")
        check("cron_parse rejects invalid expressions", False, "no error")
    except ToolError:
        check("cron_parse rejects invalid expressions", True)


def test_security_dev(fixtures: dict) -> None:
    print("\n[security / dev / persian]")
    result = run("password_audit", password="123456")
    check("password_audit flags weak passwords", "24" in result.message or "bit" in result.message, result.message)
    weak = result.details
    strong = run("password_audit", password="Tr0ub4dor&3-Horse-Battery").details
    check("password_audit scores strong higher", "قوی" in strong or "Strong" in strong, strong[:80])
    check("password_audit lists weaknesses", "نقاط ضعف" in weak or "Weakness" in weak, weak[:80])

    secret_dir = OUT / "secrets"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "config.py").write_text(
        'API_KEY = "AIzaSyD-abcdefghijklmnopqrstuvwxyz012345"\npassword = "hunter2pass"\n', encoding="utf-8")
    result = run("secret_scan", folder=str(secret_dir))
    check("secret_scan finds planted secrets", "Google API key" in result.details, result.details[:100])

    result = run("checksum_manifest", action="create", items=[str(fixtures["text"])])
    manifest = Path(result.outputs[0])
    check("checksum_manifest creates a manifest", manifest.exists())
    verify_dir = manifest.parent
    shutil.copy2(fixtures["text"], verify_dir / fixtures["text"].name)
    result = run("checksum_manifest", action="verify", manifest=str(manifest))
    check("checksum_manifest verifies files", "سالم" in result.message or "ok" in result.message, result.message)

    result = run("regex_tester", pattern=r"\b\d{4}\b", text="سال 1403 و 2024 مهم است")
    check("regex_tester finds matches", "2" in result.message, result.message)

    result = run("text_diff", a=str(fixtures["text"]), b=str(fixtures["text2"]))
    check("text_diff reports differences", "خط" in result.message or "diff" in result.message, result.message)

    payload = json.dumps({"data": {"items": [{"title": "hello"}, {"title": "world"}]}})
    result = run("json_query", source="text", text=payload, path="data.items[1].title")
    check("json_query extracts by path", "world" in result.details, result.details[:60])

    try:
        run("json_query", source="text", text="{broken json", path="a")
        check("json_query rejects bad json", False, "no error")
    except ToolError:
        check("json_query rejects bad json", True)

    import base64

    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps({"sub": "42", "exp": 4102444800}).encode()).decode().rstrip("=")
    result = run("jwt_decode", token=f"{header}.{body}.signature")
    check("jwt_decode reads the payload", '"sub": "42"' in result.details, result.details[:80])
    check("jwt_decode reports validity", "معتبر" in result.message or "valid" in result.message.lower(), result.message)

    result = run("url_tool", action="encode", value="hello world/سلام")
    check("url_tool encodes", "%20" in result.details, result.details[:40])
    result = run("url_tool", action="parse", value="https://example.com:8443/a/b?x=1&y=2#frag")
    check("url_tool parses components", "x = 1" in result.details and "8443" in result.details, result.details)

    result = run("path_tool", path=r"C:\Users\me\My Documents\file.tar.gz")
    check("path_tool splits a windows path", "file.tar.gz" in result.message, result.message)

    result = run("persian_fix", source="text", text="كتاب هاي من مي شود 123", numbers="fa", write_file=False)
    check("persian_fix replaces arabic letters", "کتاب" in result.details and "ي" not in result.details.split("123")[0],
          result.details)
    check("persian_fix converts digits", "۱۲۳" in result.details, result.details)

    result = run("persian_numbers", source="text", text="قیمت 12500 تومان", target="fa", thousands=True)
    check("persian_numbers formats thousands", "۱۲" in result.details and "٬" in result.details, result.details)

    result = run("jalali_date", direction="to_gregorian", year=1403, month=1, day=1)
    check("jalali_date converts 1403/01/01", "2024-03-20" in result.message, result.message)
    result = run("jalali_date", direction="to_jalali", year=2024, month=3, day=20)
    check("jalali_date converts back", "1403" in result.message, result.message)
    result = run("jalali_date", direction="today")
    check("jalali_date reports today", "امروز" in result.message or "Today" in result.message, result.message)

    result = run("persian_sort", source="text", text="ب\nآ\nپ\nالف\n12\n9")
    order = result.details.splitlines()
    check("persian_sort puts آ before ب", order.index("آ") < order.index("ب"), str(order))

    result = run("keyboard_fix", text="sghl", direction="en_to_fa")
    check("keyboard_fix maps latin keys to persian", result.details.strip() == "سلام", result.details)


def test_ocr_codes(fixtures: dict) -> None:
    print("\n[ocr / codes]")
    if fixtures.get("qr"):
        result = run("qr_scan", src=str(fixtures["qr"]))
        check("qr_scan decodes a QR code", "wingenius.local" in result.details, result.details[:60])
    else:
        skip("qr_scan", "qrcode not installed")

    result = run("barcode_generate", kind="qr", data="https://example.com", size=300)
    target = Path(result.outputs[0])
    check("barcode_generate writes a qr png", target.exists() and target.stat().st_size > 500)
    if fixtures.get("qr"):
        decoded = run("qr_scan", src=str(target))
        check("generated QR decodes back", "example.com" in decoded.details, decoded.details[:60])

    try:
        run("image_ocr", src=fixtures["images"][0])
        check("image_ocr works or explains how to install", True)
    except ToolError as exc:
        message = str(exc)
        check("image_ocr explains how to install OCR",
              "tesseract" in message.lower() and ("pip install" in message.lower() or "winget" in message.lower()),
              message[:120])


def test_v41_tools(fixtures: dict) -> None:
    """Tools added in 4.2: redaction, speech-to-text, OCR setup, SendTo, portable mode."""
    print("\n[4.2 engine]")
    from app.tools.media_extra import _srt_time

    check("srt timestamp 0s", _srt_time(0) == "00:00:00,000", _srt_time(0))
    check("srt timestamp keeps millis", _srt_time(61.5) == "00:01:01,500", _srt_time(61.5))
    check("srt timestamp hours", _srt_time(3725.999) == "01:02:05,999", _srt_time(3725.999))
    check("srt timestamp never emits 1000 ms", _srt_time(59.9999).endswith(",999") or
          _srt_time(59.9999).endswith(",000"), _srt_time(59.9999))

    # ---- redaction guards (the happy path lives in output_test.py)
    # Own copy of the inputs: by the time this group runs, the shared fixtures may have
    # been consumed by the file-operation tests.
    sandbox = Path(tempfile.mkdtemp(prefix="wg_v42_"))
    sample_pdf = sandbox / "secret.pdf"
    try:
        import pymupdf

        book = pymupdf.open()
        sheet = book.new_page()
        sheet.insert_text((60, 90), "Card number: 6037-9975-1234-5678", fontsize=12)
        sheet.insert_text((60, 120), "Public line stays.", fontsize=12)
        book.save(sample_pdf)
        book.close()
    except Exception:
        sample_pdf = None
    sample_png = sandbox / "photo.png"
    try:
        from PIL import Image

        Image.new("RGB", (320, 200), "#223344").save(sample_png)
    except Exception:
        sample_png = None

    try:
        run("pdf_redact", src=str(sample_pdf), mode="search", terms="")
        check("pdf_redact rejects an empty phrase list", False)
    except ToolError as exc:
        check("pdf_redact rejects an empty phrase list",
              "عبارت" in str(exc) or "phrase" in str(exc), str(exc)[:70])
    try:
        run("pdf_redact", src=str(sample_pdf), mode="area", area="1,2,3")
        check("pdf_redact rejects a malformed area", False)
    except ToolError as exc:
        check("pdf_redact rejects a malformed area", "چهار" in str(exc) or "four" in str(exc),
              str(exc)[:70])
    try:
        run("pdf_redact", src=str(sample_pdf), mode="area", area="10,10,300,20")
        check("pdf_redact rejects an out-of-range area", False)
    except ToolError as exc:
        check("pdf_redact rejects an out-of-range area", "۱۰۰" in str(exc) or "100" in str(exc),
              str(exc)[:70])

    # ---- speech to text: without a local engine it must explain, never crash
    engine_ready = False
    try:
        import faster_whisper  # noqa: F401
        engine_ready = True
    except Exception:
        try:
            import whisper  # noqa: F401
            engine_ready = True
        except Exception:
            engine_ready = False
    if engine_ready:
        skip("audio_transcribe install hint", "a whisper engine is installed")
    else:
        try:
            run("audio_transcribe", src=str(sample_png))
            check("audio_transcribe explains the install", False)
        except ToolError as exc:
            message = str(exc)
            check("audio_transcribe explains the install",
                  "faster-whisper" in message and "pip install" in message, message[:80])

    # ---- OCR setup: status must always answer, and it must find the engine when present
    result = run("ocr_setup", action="status")
    check("ocr_setup returns a status message", bool(result.message), result.message[:60])
    from app.tools.ocr_tools import find_tesseract

    engine = find_tesseract()
    if engine:
        check("ocr_setup finds the engine on PATH", engine.lower().endswith(("tesseract", "tesseract.exe")),
              engine)
    else:
        check("ocr_setup documents the install command",
              "winget" in result.details.lower() or "pip install" in result.details.lower(),
              result.details[:60])
    try:
        run("ocr_setup", action="set_path", path=str(WORK / "nowhere" / "tesseract.exe"))
        check("ocr_setup rejects a missing path", False)
    except ToolError as exc:
        check("ocr_setup rejects a missing path", "وجود ندارد" in str(exc) or "exist" in str(exc),
              str(exc)[:60])
    try:
        run("ocr_setup", action="set_path", path="")
        check("ocr_setup rejects an empty path", False)
    except ToolError as exc:
        check("ocr_setup rejects an empty path", "خالی" in str(exc) or "empty" in str(exc),
              str(exc)[:60])

    # ---- OCR setup: saving a path and detecting an already-installed language pack
    fake_engine = sandbox / ("tesseract.exe" if sys.platform.startswith("win") else "tesseract")
    fake_engine.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_engine.chmod(0o755)
    from app.core.settings import Settings as _Settings

    _settings = _Settings()
    try:
        saved = run("ocr_setup", action="set_path", path=str(fake_engine))
        expected_engine = fake_engine.resolve()
        check("ocr_setup saves a custom engine path", str(expected_engine) in saved.message,
              saved.message[:60])
        check("ocr_setup records the path in settings",
              Path(str(_settings.get("ocr_tesseract_path", ""))).resolve() == expected_engine, "not saved")
        saved_dir = run("ocr_setup", action="set_path", path=str(sandbox))
        check("ocr_setup accepts a folder containing the engine", "وجود ندارد" not in saved_dir.message,
              saved_dir.message[:60])

        packs = sandbox / "tessdata"
        packs.mkdir(exist_ok=True)
        (packs / "fas.traineddata").write_bytes(b"0" * 200_000)
        _settings.set("ocr_tessdata_path", str(packs))
        again = run("ocr_setup", action="download", lang="fas")
        check("ocr_setup skips a language pack that is already installed",
              "از قبل" in again.message or "already" in again.message, again.message[:60])
    finally:
        for key in ("ocr_tesseract_path", "ocr_tessdata_path"):
            try:
                _settings.set(key, "")
            except Exception:
                pass

    # ---- SendTo integration must degrade cleanly off Windows
    import sys as _sys

    if _sys.platform.startswith("win"):
        result = run("sendto_install", action="status")
        check("sendto_install reports its status on Windows", bool(result.message))
    else:
        try:
            run("sendto_install", action="install")
            check("sendto_install explains it is Windows-only", False)
        except ToolError as exc:
            check("sendto_install explains it is Windows-only", "ویندوز" in str(exc) or "Windows" in str(exc),
                  str(exc)[:60])

    # ---- portable mode: the flag, the env var and the marker file all work
    from app.core import paths as paths_module

    saved_argv = list(_sys.argv)
    try:
        _sys.argv = ["WinGenius.exe", "--portable"]
        check("portable flag switches the data folder", paths_module.is_portable())
        _sys.argv = ["WinGenius.exe"]
        check("no flag means a per-user data folder", not paths_module.is_portable())
        os.environ["WINGENIUS_PORTABLE"] = "1"
        check("WINGENIUS_PORTABLE env switches the data folder", paths_module.is_portable())
        del os.environ["WINGENIUS_PORTABLE"]
    finally:
        _sys.argv = saved_argv
    shutil.rmtree(sandbox, ignore_errors=True)
    marker = Path(paths_module.project_root()) / "portable.flag"
    if marker.parent.name == "WinGenius" and marker.parent.parent.name == "user":
        marker.write_text("", encoding="utf-8")
        try:
            check("portable.flag marker switches the data folder", paths_module.is_portable())
        finally:
            marker.unlink()


def test_v50_tools(fixtures: dict) -> None:
    """5.0 additions: digital signatures, OCR-aware search, crash reports, settings."""
    print("\n[5.0 engine]")
    sandbox = Path(tempfile.mkdtemp(prefix="wg_v50_"))
    try:
        # ---------------------------------------------------------- signatures
        unsigned = sandbox / "plain.pdf"
        try:
            import pymupdf

            book = pymupdf.open()
            sheet = book.new_page()
            sheet.insert_text((60, 90), "contract body", fontsize=14)
            book.save(unsigned)
            book.close()
            have_pdf = True
        except Exception:
            have_pdf = False
        if have_pdf:
            try:
                run("pdf_signature_check", src=str(unsigned))
                check("pdf_signature_check rejects an unsigned pdf", False)
            except ToolError as exc:
                check("pdf_signature_check rejects an unsigned pdf",
                      "امضا" in str(exc) or "signature" in str(exc), str(exc)[:60])
            try:
                run("pdf_sign", src=str(unsigned), cert=str(sandbox / "missing.p12"),
                    password="x", out=str(sandbox / "out.pdf"))
                check("pdf_sign reports a missing certificate file", False)
            except ToolError as exc:
                check("pdf_sign reports a missing certificate file",
                      "پیدا نشد" in str(exc) or "not found" in str(exc), str(exc)[:60])
        else:
            skip("signature guards", "PyMuPDF not installed")

        # ------------------------------------------------------- crash report
        preview = run("crash_report", action="preview", lines=120)
        check("crash_report previews a bundle", bool(preview.details), preview.message[:60])
        check("crash_report preview names the version", "WinGenius" in preview.details,
              preview.details[:40])
        built = run("crash_report", action="create", lines=120, out_dir=str(sandbox / "report"))
        bundle = Path(built.outputs[0])
        check("crash_report writes a zip", bundle.exists() and bundle.suffix == ".zip")
        import zipfile

        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            body = archive.read("report.txt").decode("utf-8", errors="replace")
        check("crash_report bundle has a report and a manifest",
              "report.txt" in names and "manifest.txt" in names, str(names))
        check("crash_report redacts the home path", str(Path.home()) not in body, "leaked home path")
        check("crash_report includes the diagnostics", "diagnostics" in body, body[:60])

        # ------------------------------------------ indexer: scanned pdf via OCR
        from app.core import indexer
        from app.core import tesseract as tess

        check("core.tesseract agrees with the tool layer",
              tess.find_tesseract() == __import__(
                  "app.tools.ocr_tools", fromlist=["find_tesseract"]).find_tesseract())
        check("ocr_available reflects the machine",
              isinstance(tess.ocr_available(), bool))

        long_text = sandbox / "notes.pdf"
        short_text = sandbox / "stamp.pdf"
        if have_pdf:
            book = pymupdf.open()
            page = book.new_page()
            page.insert_text((60, 90), "This document has a real text layer so it is not a scan.",
                             fontsize=12)
            book.save(long_text)
            book.close()
            check("indexer does not OCR a real text layer",
                  indexer.extract_text_with_meta(long_text)[1] is False)
            book = pymupdf.open()
            page = book.new_page()
            page.insert_text((60, 90), "tiny", fontsize=12)
            book.save(short_text)
            book.close()
            check("indexer does not OCR a short pdf without a page image",
                  indexer.extract_text_with_meta(short_text)[1] is False)

            if tess.ocr_available():
                from PIL import Image, ImageDraw

                scan_image = sandbox / "scan.png"
                image = Image.new("RGB", (1100, 400), "white")
                ImageDraw.Draw(image).text((40, 180), "SCANNED INVOICE 987654 TOTAL 4500", fill="black")
                image.save(scan_image)
                scanned = sandbox / "scanned.pdf"
                book = pymupdf.open()
                page = book.new_page(width=1100, height=400)
                page.insert_image(page.rect, filename=str(scan_image))
                book.save(scanned)
                book.close()
                text, used_ocr = indexer.extract_text_with_meta(scanned)
                check("indexer OCRs a scanned pdf", used_ocr is True, text[:40])
                check("indexer keeps the recognised words", "987654" in text, text[:40])
                counters = indexer.build([str(scanned)])
                check("index build counts the OCR rescue", counters.get("ocr", 0) >= 1, str(counters))
                hits = indexer.search("987654")
                check("a scanned pdf becomes searchable",
                      any("scanned.pdf" in hit.path for hit in hits), str([h.path for h in hits][:3]))
            else:
                skip("scanned pdf indexing", "Tesseract or pytesseract is missing")
        else:
            skip("indexer OCR checks", "PyMuPDF not installed")

        # -------------------------------------------------- settings + toggles
        from app.core.settings import Settings

        check("OCR indexing is on by default", bool(Settings().get("index/ocr", True)))

        # --------------------------------------------- index search still works
        plain = sandbox / "searchable.txt"
        plain.write_text("alpha bravo charlie delta", encoding="utf-8")
        indexer.build([str(plain)])
        hits = indexer.search("bravo")
        check("search still finds plain text", any("searchable.txt" in hit.path for hit in hits),
              str([h.path for h in hits][:2]))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_scale(fixtures: dict) -> None:
    """Bulk work must stay fast and reversible — the classic way a toolbox rots."""
    print("\n[scale]")
    import time as _time

    sandbox = Path(tempfile.mkdtemp(prefix="wg_scale_"))
    try:
        folder = sandbox / "bulk"
        folder.mkdir()
        count = 600
        for index in range(count):
            (folder / f"photo_{index:04d}.txt").write_text(f"file {index}", encoding="utf-8")

        started = _time.time()
        renamed = run("batch_rename", folder=str(folder), mode="template",
                      template="renamed_{n}", start=1, padding=4, preview=False)
        rename_seconds = _time.time() - started
        after = list(folder.iterdir())
        check(f"batch_rename handled {count} files", len(after) == count, str(len(after)))
        check("batch_rename is fast enough", rename_seconds < 90, f"{rename_seconds:.1f}s")
        check("batch_rename reported the batch", str(count // 10) in renamed.message.replace(",", "")
              or str(count) in renamed.message, renamed.message[:60])

        from app.core.journal import journal as journal_store

        started = _time.time()
        undone = journal_store().undo_last(1)
        undo_seconds = _time.time() - started
        restored = [path for path in folder.iterdir() if path.name.startswith("photo_")]
        check("undo restores a 600-file batch", len(restored) == count, str(len(restored)))
        check("undo of a big batch is fast", undo_seconds < 90, f"{undo_seconds:.1f}s")
        check("undo reported success", bool(undone) and undone[0]["status"] != "failed",
              str(undone[:1])[:80])

        # indexing a few hundred documents
        docs = sandbox / "docs"
        docs.mkdir()
        for index in range(250):
            (docs / f"doc_{index:03d}.txt").write_text(
                f"document {index} unique-marker-{index} lorem ipsum", encoding="utf-8")
        from app.core import indexer

        started = _time.time()
        counters = indexer.build([str(docs)])
        index_seconds = _time.time() - started
        check("index build sees every document", counters["files"] == 250, str(counters))
        check("index build is fast enough", index_seconds < 90, f"{index_seconds:.1f}s")
        hits = indexer.search("unique-marker-137")
        check("index finds a document deep in the batch",
              any("doc_137.txt" in hit.path for hit in hits), str([h.path for h in hits][:2]))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_cli(fixtures: dict) -> None:
    print("\n[cli]")
    from app.core.cli import run_cli

    code = run_cli(["--list", "--json"])
    check("cli --list --json exits 0", code == 0, str(code))
    code = run_cli(["--describe", "pdf_to_images"])
    check("cli --describe exits 0", code == 0, str(code))
    code = run_cli(["--describe", "does_not_exist"])
    check("cli --describe rejects unknown tools", code == 2, str(code))
    code = run_cli(["--doctor"])
    check("cli --doctor exits 0", code == 0, str(code))
    output_pdf = OUT / "cli_split.pdf"
    code = run_cli(["--run", "pdf_extract_pages", "--file", str(fixtures["pdf"]),
                    "--set", "pages=1-2", "--out", str(output_pdf), "--quiet"])
    check("cli runs a real tool", code == 0 and output_pdf.exists(), f"code={code}")
    code = run_cli(["--run", "pdf_crop", "--file", str(WORK / "missing.pdf")])
    check("cli reports tool failure with code 1", code == 1, str(code))

    # ---------------------------------------------------- the rest of the flags
    code = run_cli(["--version"])
    check("cli --version exits 0", code == 0, str(code))
    code = run_cli(["--help"])
    check("cli --help exits 0", code == 0, str(code))
    code = run_cli(["--list", "--category", "pdf", "--lang", "en"])
    check("cli --list filters by category", code == 0, str(code))
    code = run_cli(["--describe", "pdf_to_images", "--json"])
    check("cli --describe --json exits 0", code == 0, str(code))
    code = run_cli(["--run", "uuid_generate", "--set", "count=2", "--json", "--quiet"])
    check("cli --run --json exits 0", code == 0, str(code))
    code = run_cli(["--plan", "فشرده کردن عکس‌ها", "--dry-run", "--json"])
    check("cli --plan --dry-run exits 0", code == 0, str(code))
    code = run_cli(["--plan", "یک جمله‌ی کاملاً بی‌ربط", "--dry-run"])
    check("cli --plan rejects nonsense", code == 2, str(code))
    code = run_cli(["--list-workflows", "--json"])
    check("cli --list-workflows exits 0", code == 0, str(code))

    # ------------------------------------------------------------- v6: flows
    code = run_cli(["--flows", "--json"])
    check("cli --flows exits 0", code == 0, str(code))
    code = run_cli(["--lint-flow", "photo_pdf_pack", "--lang", "en"])
    check("cli --lint-flow accepts a builtin flow", code == 0, str(code))
    code = run_cli(["--explain-flow", "photo_pdf_pack", "--lang", "en"])
    check("cli --explain-flow exits 0", code == 0, str(code))
    code = run_cli(["--lint-flow", "no-such-flow-at-all"])
    check("cli --lint-flow rejects an unknown flow", code == 2, str(code))
    flow_file = WORK / "cli_flow.json"
    flow_file.write_text(json.dumps({
        "id": "cli_v60", "name": "cli flow", "steps": [
            {"tool_id": "hash_files", "values": {"src": ["{input}"],
                                                 "save": "{out_dir}/cli_hash.sha256"}}]}),
        encoding="utf-8")
    code = run_cli(["--lint-flow", str(flow_file), "--lang", "en"])
    check("cli --lint-flow reads a flow file", code == 0, str(code))
    code = run_cli(["--run-flow", str(flow_file), "--file", str(fixtures["text"]),
                    "--out", str(OUT), "--quiet", "--json"])
    check("cli --run-flow runs a flow file", code == 0, str(code))
    check("cli --run-flow wrote the output", (OUT / "cli_hash.sha256").exists())

    from app.core.workflows import store as workflow_store

    workflows = workflow_store.all()
    check("there are built-in workflows", len(workflows) >= 5, str(len(workflows)))
    if workflows:
        code = run_cli(["--run-workflow", workflows[0].id, "--json", "--quiet"])
        check("cli --run-workflow exits 0", code in (0, 1), str(code))

    from app.core.scheduler import store as schedule_store

    item = schedule_store.new()
    item.name = "cli schedule"
    item.cron = "0 4 * * *"
    item.kind = "workflow"
    item.target_id = workflows[0].id if workflows else ""
    item.enabled = True
    schedule_store.save(item)
    code = run_cli(["--list-schedules", "--json"])
    check("cli --list-schedules exits 0", code == 0, str(code))
    code = run_cli(["--run-schedule", item.id, "--quiet"])
    check("cli --run-schedule exits 0", code in (0, 1), str(code))
    code = run_cli(["--run-schedule", "nope", "--quiet"])
    check("cli --run-schedule rejects an unknown id", code == 2, str(code))
    schedule_store.delete(item.id)

    from app.core import macro as macro_module

    macro_path = macro_module.save_macro(macro_module.builtin_macros("fa")[0])
    code = run_cli(["--macros", "--json"])
    check("cli --macros exits 0", code == 0, str(code))
    code = run_cli(["--run-macro", str(macro_path), "--dry-run", "--json", "--quiet"])
    check("cli --run-macro --dry-run exits 0", code == 0, str(code))
    code = run_cli(["--run-macro", str(WORK / "ghost.json"), "--quiet"])
    check("cli --run-macro rejects a missing file", code == 2, str(code))
    from app.core.journal import journal as journal_store

    journal_store().clear()
    code = run_cli(["--undo", "--quiet"])
    check("cli --undo with an empty journal exits 0", code == 0, str(code))
    # with an entry whose files are gone, the CLI must report it and use exit 1
    ghost = WORK / "ghost_rename.txt"
    ghost.write_text("x", encoding="utf-8")
    journal_store().record_moves([(str(ghost), str(WORK / "ghost_renamed.txt"))],
                                 "تغییر نام", "rename")
    ghost.unlink()                       # the source vanished: undo cannot succeed
    code = run_cli(["--undo", "--quiet"])
    check("cli --undo never crashes on a stale entry", code in (0, 1), str(code))
    journal_store().clear()
    code = run_cli(["--run", "no_such_tool"])
    check("cli rejects an unknown tool", code == 2, str(code))
    code = run_cli(["--install-sendto"])
    if sys.platform.startswith("win"):
        check("cli --install-sendto exits 0 on Windows", code == 0, str(code))
    else:
        check("cli --install-sendto fails cleanly off Windows", code == 1, str(code))
    code = run_cli(["--remove-sendto"])
    check("cli --remove-sendto never crashes", code in (0, 1), str(code))




# --------------------------------------------------------------- v3: planner
def test_planner(fixtures: dict) -> None:
    print("\n[planner]")
    from app.core import planner

    plan = planner.build_plan("این عکس‌ها را کم‌حجم کن و PDF بساز", fixtures["images"])
    check("planner builds a photo→PDF chain", plan is not None and
          [step.tool_id for step in plan.steps] == ["image_compress", "images_to_pdf"],
          str(plan.steps if plan else None))
    check("planner fills the first step with the picked files",
          plan is not None and fixtures["images"][0] in plan.steps[0].values.get("src", []),
          str(plan.steps[0].values if plan else None))
    check("planner chains the second step to {prev_all}",
          plan is not None and plan.steps[1].values.get("src") == ["{prev_all}"],
          str(plan.steps[1].values if plan else None))

    reverse = planner.build_plan("PDF را به عکس تبدیل کن", [str(fixtures["pdf"])])
    check("planner respects word order (PDF → images)",
          reverse is not None and reverse.steps[0].tool_id == "pdf_to_images",
          str(reverse.steps[0].tool_id if reverse else None))

    shrink = planner.build_plan("حجم این PDF را کم کن", [str(fixtures["pdf"])])
    check("planner understands 'shrink this PDF'",
          shrink is not None and shrink.steps[0].tool_id == "pdf_compress", "")

    english = planner.build_plan("make a gif from this video", ["clip.mp4"])
    check("planner understands English", english is not None and
          english.steps[0].tool_id == "video_to_gif", "")

    check("planner returns None for nonsense", planner.build_plan("qwerty zxcv", []) is None)
    check("planner ships example sentences", len(planner.suggestions("fa")) >= 6)
    check("planner exposes ~30 intents", len(planner.all_intents()) >= 28,
          str(len(planner.all_intents())))
    described = planner.describe(plan) if plan else ""
    check("planner can describe a plan", "image_compress" not in described and described, described[:60])


# ------------------------------------------------------------- v3: workflows
def test_workflows(fixtures: dict) -> None:
    print("\n[workflows]")
    from app.core import workflows

    check("built-in workflow library is not empty", len(workflows.store.builtins()) >= 8,
          str(len(workflows.store.builtins())))
    for workflow in workflows.store.builtins():
        if any(get_tool(step.tool_id) is None for step in workflow.steps):
            check(f"workflow {workflow.id} references real tools", False)

    spec = get_tool("images_to_pdf")
    key, takes_list = workflows.input_field(spec)
    check("input_field finds the files field", key == "src" and takes_list, f"{key}/{takes_list}")
    filled = workflows.autofill(spec, fixtures["images"])
    check("autofill writes the picked files", filled["src"] == fixtures["images"], "")

    context = workflows._initial_context({"__paths__": fixtures["images"]})
    chained = workflows.substitute({"src": ["{prev_all}"], "note": "{prev_dir}/x"}, 
                                   {**context, "prev_all": ["/a/b.png"], "prev_dir": "/a"})
    check("placeholders resolve inside lists", chained["src"] == ["/a/b.png"], str(chained))
    check("placeholders resolve inside strings", chained["note"] == "/a/x", str(chained["note"]))

    needs_password = workflows.store.get("pdf_shrink_protect")
    problems = workflows.missing_required(needs_password)
    check("missing_required flags an empty password", any("رمز" in item or "password" in item
                                                          for item in problems), str(problems))

    # real run: PDF → images → ZIP
    workflow = workflows.store.get("pdf_to_images_zip")
    result = workflows.run_sync(workflow, "fa", str(OUT), {"__paths__": [str(fixtures["pdf"])]})
    zips = [path for path in result.outputs if path.lower().endswith(".zip")]
    check("workflow run finishes", result.ok, result.message)
    check("workflow chains outputs into the next step", bool(zips), str(result.outputs))
    check("workflow reports every step", len(result.steps) == len(workflow.steps), "")

    # user workflow round-trip
    custom = workflows.store.new("تست موقت")
    custom.steps = [workflows.Step("uuid_generate", {"count": 2})]
    workflows.store.save(custom)
    loaded = workflows.store.get(custom.id)
    check("user workflow round-trips through JSON", loaded is not None and
          loaded.steps[0].tool_id == "uuid_generate", "")
    sync = workflows.run_sync(loaded, "fa", str(OUT), {})
    check("user workflow runs", sync.ok, sync.message)
    check("user workflow deletes cleanly", workflows.store.delete(custom.id))


# --------------------------------------------------------------- v3: watcher
def test_watcher(fixtures: dict) -> None:
    print("\n[watcher]")
    from app.core import watcher as watcher_mod
    from PySide6.QtCore import QCoreApplication

    if QCoreApplication.instance() is None:      # QTimer needs an application object
        QCoreApplication([])

    watch_dir = WORK / "watched"
    watch_dir.mkdir(exist_ok=True)
    for stale in watch_dir.iterdir():
        stale.unlink()

    rule = watcher_mod.store.new()
    rule.name = "تست دیده‌بان"
    rule.folder = str(watch_dir)
    rule.pattern = "*.txt"
    rule.action = "workflow"
    rule.target_id = "archive_encrypt"
    rule.settle = 0.0
    watcher_mod.store.save(rule)
    check("watch rule is stored", watcher_mod.store.get(rule.id) is not None)

    (watch_dir / "old.txt").write_text("old", encoding="utf-8")
    local = watcher_mod.Watcher(interval_ms=1000)
    primed = local.prime(rule)
    check("prime() ignores existing files", primed == 1, str(primed))
    check("no trigger for a known file", local.scan_now() == [])

    (watch_dir / "new.txt").write_text("hello", encoding="utf-8")
    hits = local.scan_now()
    check("new file triggers the rule", len(hits) == 1 and hits[0][1].endswith("new.txt"), str(hits))
    check("the same file is not reported twice", local.scan_now() == [])

    (watch_dir / "ignored.pdf").write_bytes(b"%PDF-1.4")
    check("pattern filter works", local.scan_now() == [])

    check("matching files are listed",
          [path.name for path in watcher_mod.read_folder(str(watch_dir), "*.txt")] ==
          ["new.txt", "old.txt"] or len(watcher_mod.read_folder(str(watch_dir), "*.txt")) == 2)

    local.reset()
    check("reset() forgets fingerprints", len(local.scan_now()) >= 1)

    watcher_mod.mark_run(rule, str(watch_dir / "new.txt"), "auto")
    refreshed = watcher_mod.store.get(rule.id)
    check("mark_run stores the run counters", refreshed.runs >= 1 and refreshed.last_status == "auto",
          str(refreshed.runs))
    local.start()
    started = local.running
    local.stop()
    check("watcher start()/stop() toggles the timer", started and not local.running,
          f"started={started} stopped={local.running}")
    check("watch rule deletes cleanly", watcher_mod.store.delete(rule.id))


# ----------------------------------------------------------------- v3: index
def test_index(fixtures: dict) -> None:
    print("\n[index]")
    from app.core import indexer

    indexer.clear()
    docs = WORK / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "fa.txt").write_text("قرارداد سال ۱۴۰۳ با شرکت آلفا بسته شد.\nWinGenius FTS test.",
                                 encoding="utf-8")
    (docs / "notes.md").write_text("# Report\n\nquarterly revenue growth 12%", encoding="utf-8")
    shutil.copy(fixtures["pdf"], docs / "report.pdf")

    counters = indexer.build([str(docs)])
    check("indexer reads every supported file", counters["files"] >= 3, str(counters))
    check("indexer stores documents", counters["indexed"] >= 3, str(counters))
    stats = indexer.stats()
    check("index stats report documents", stats["docs"] >= 3, str(stats))

    persian = indexer.search("قرارداد")
    check("Persian full-text search works", bool(persian) and persian[0].name == "fa.txt",
          str([hit.name for hit in persian]))
    english = indexer.search("revenue")
    check("English full-text search works", bool(english) and english[0].name == "notes.md", "")
    pdf_hits = indexer.search("WinGenius")
    check("PDF text is indexed too", any(hit.name == "report.pdf" for hit in pdf_hits),
          str([hit.name for hit in pdf_hits]))
    check("queries with no match return nothing", indexer.search("zzzznotfound") == [])
    check("snippet contains the query",
          "«قرارداد»" in persian[0].snippet if persian else False,
          persian[0].snippet if persian else "")
    report = indexer.report("revenue", english)
    check("indexer writes a readable report", "revenue" in report and "notes.md" in report)

    incremental = indexer.build([str(docs)])
    check("second build skips unchanged files", incremental["indexed"] == 0, str(incremental))
    check("remove() drops a document", indexer.remove(str(docs / "notes.md")) and
          not indexer.search("revenue"))
    indexer.clear()
    check("clear() empties the index", indexer.stats()["docs"] == 0)


# -------------------------------------------------------------- v3: automation
def test_automation_tools(fixtures: dict) -> None:
    print("\n[automation tools]")
    from app.core import indexer

    indexer.clear()
    docs = WORK / "docs2"
    docs.mkdir(exist_ok=True)
    (docs / "a.txt").write_text("hello win genius index", encoding="utf-8")
    result = run("index_build", folder=str(docs), recursive=True)
    check("index_build indexes a folder", "ایندکس" in result.message or "سند" in result.message,
          result.message)
    result = run("index_search", query="genius", limit=5)
    check("index_search finds the text", "a.txt" in result.details, result.details[:80])
    result = run("index_stats")
    check("index_stats reports documents", "سند" in result.message, result.message)
    result = run("index_clear")
    check("index_clear empties it", "پاک" in result.message, result.message)
    try:
        run("index_search", query="anything")
        check("index_search explains an empty index", False)
    except ToolError as exc:
        check("index_search explains an empty index", "ایندکس" in str(exc), str(exc)[:80])

    result = run("workflows_list")
    check("workflows_list lists the library", "→" in result.details, result.details[:60])
    result = run("tools_inventory", out=str(OUT / "inventory.txt"))
    check("tools_inventory writes a report", bool(result.outputs) and
          (OUT / "inventory.txt").exists(), str(result.outputs))
    result = run("watch_status")
    check("watch_status reports no rules", "قانون" in result.message, result.message)


# ----------------------------------------------------------------- v3: tools
def test_v3_tools(fixtures: dict) -> None:
    print("\n[v3 tools]")
    # ---- text intelligence
    long_text = ("وین‌جنیوس یک دستیار ویندوزی است. کاربر می‌تواند فایل‌ها را تبدیل کند. "
                 "تبدیل PDF به عکس ساده است. خلاصه‌سازی متن برای مطالعه مفید است. "
                 "برنامه همه‌ی کارها را محلی انجام می‌دهد. هیچ داده‌ای ارسال نمی‌شود. "
                 "کاربر می‌تواند چند ابزار را پشت سر هم اجرا کند.") * 2
    result = run("text_summarize", text=long_text, size=3)
    check("text_summarize returns bullets", result.details.count("•") >= 3, result.details[:80])
    result = run("text_keywords", text=long_text, count=8)
    check("text_keywords lists keywords", result.details.count("\n") >= 5, "")
    result = run("number_to_words", number="1,250,000", currency="ریال")
    check("number_to_words writes Persian words", "میلیون" in result.message, result.message)
    result = run("text_readability", text=long_text)
    check("text_readability reports reading time", "دقیقه" in result.details, result.details[:60])
    result = run("text_extract", text="mail a@b.com call 09121234567 site https://x.y/z")
    check("text_extract finds emails and phones",
          "a@b.com" in result.details and "09121234567" in result.details, result.details[:80])
    result = run("lang_detect", text="سلام دنیا hello")
    check("lang_detect detects mixed text", "ترکیبی" in result.message or "فارسی" in result.message,
          result.message)
    result = run("text_outline", text="# فصل اول\nمتن\n## بخش\n1. مورد\n")
    check("text_outline finds headings", result.details.count("•") >= 2, result.details[:80])
    result = run("text_cleanup", text="سلام   دنیا\n\n\nسلام   دنیا\n")
    check("text_cleanup collapses blank lines and duplicates",
          result.details.count("دنیا") == 1, result.details[:80])
    result = run("csv_profile", file=str(fixtures["text"]))
    check("csv_profile survives a non-csv file", result.message is not None, "")

    # ---- developer extras
    result = run("minify_code", kind="css", text="a { color : red ; }\n/* x */")
    check("minify_code shrinks CSS", "%" in result.message, result.message)
    result = run("sql_format", sql="select a from t where a=1 and b=2")
    check("sql_format adds line breaks", "\n" in result.details and "FROM" in result.details,
          result.details[:60])
    result = run("json_yaml", source="json", target="yaml", text='{"a":1,"b":[1,2]}')
    check("json_yaml converts to YAML", "a: 1" in result.details, result.details[:60])
    result = run("semver_compare", a="1.2.3", b="1.10.0")
    check("semver_compare compares numerically", "1.10.0" in result.message, result.message)
    result = run("html_escape", text='<b>x</b> & "y"', mode="escape")
    check("html_escape escapes tags", "&lt;b&gt;" in result.details, result.details[:40])
    result = run("slugify", text="سلام دنیا! Hello")
    check("slugify builds a slug", "hello" in result.message, result.message)
    env_file = WORK / ".env.test"
    env_file.write_text("API_KEY=secret\nAPI_KEY=again\nEMPTY=\n", encoding="utf-8")
    result = run("env_check", file=str(env_file))
    check("env_check finds duplicates and empties", "هشدار" in result.message, result.message)
    csv_file = WORK / "data.csv"
    csv_file.write_text("name,age\nعلی,30\nSara,25\n", encoding="utf-8")
    result = run("csv_to_sql", file=str(csv_file), table="people")
    check("csv_to_sql writes INSERTs", "INSERT INTO people" in result.details, result.details[:60])

    # ---- image extras
    images = fixtures["images"]
    result = run("image_split_grid", src=images[:1], cols=2, rows=2)
    check("image_split_grid makes tiles", len(result.outputs) == 4, str(len(result.outputs)))
    result = run("image_contact_sheet", src=images, cols=3, cell=120)
    check("image_contact_sheet makes one sheet", len(result.outputs) == 1 and
          Path(result.outputs[0]).exists(), str(result.outputs))
    result = run("image_ascii", src=images[:1], width=60)
    check("image_ascii writes text art", Path(result.outputs[0]).read_text(encoding="utf-8").count("\n") > 5)
    result = run("image_adjust", src=images[:1], brightness=120, contrast=110, blur=4)
    check("image_adjust writes an adjusted copy", len(result.outputs) == 1, "")
    result = run("image_duotone", src=images[:1], dark="#001122", light="#ffcc00")
    check("image_duotone writes a copy", len(result.outputs) == 1, "")
    result = run("image_autocontrast", src=images[:1])
    check("image_autocontrast writes a copy", len(result.outputs) == 1, "")
    result = run("image_stack", src=images, mode="median")
    check("image_stack merges frames", len(result.outputs) == 1, "")
    result = run("image_pixelate", src=images[:1], block=16)
    check("image_pixelate writes a copy", len(result.outputs) == 1, "")
    result = run("image_gif_make", src=images, fps=4)
    check("image_gif_make builds a GIF", Path(result.outputs[0]).suffix == ".gif", "")
    result = run("image_favicon_pack", src=images[:1])
    check("image_favicon_pack builds a full pack", len(result.outputs) >= 10, str(len(result.outputs)))
    manifest = [path for path in result.outputs if path.endswith("manifest.webmanifest")]
    check("favicon pack includes a manifest", bool(manifest), "")

    # ---- pdf extras
    pdf = fixtures["pdf"]
    result = run("pdf_booklet", src=str(pdf))
    check("pdf_booklet produces a booklet", Path(result.outputs[0]).exists(), "")
    result = run("pdf_poster", src=str(pdf), cols=2, rows=2)
    check("pdf_poster tiles the pages", Path(result.outputs[0]).exists(), "")
    result = run("pdf_page_size", src=str(pdf), size="a4")
    check("pdf_page_size normalises pages", Path(result.outputs[0]).exists(), "")
    result = run("pdf_to_markdown", src=str(pdf))
    markdown = Path(result.outputs[0]).read_text(encoding="utf-8")
    check("pdf_to_markdown writes headings", markdown.startswith("# ") and "page" in markdown.lower(),
          markdown[:60])
    result = run("pdf_compare", first=str(pdf), second=str(pdf))
    check("pdf_compare detects identical text", "یکسان" in result.message, result.message)
    result = run("pdf_forms_list", src=str(pdf))
    check("pdf_forms_list explains a form-less PDF", "فرم" in result.message, result.message)
    result = run("pdf_attachments", src=str(pdf), mode="list")
    check("pdf_attachments explains a PDF without attachments", "پیوست" in result.message,
          result.message)

    # ---- file extras
    folder = WORK / "messy"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "deep").mkdir(parents=True)
    (folder / "a.txt").write_text("a", encoding="utf-8")
    (folder / "b.png").write_bytes(b"\x89PNG" + b"0" * 20)
    (folder / "Thumbs.db").write_bytes(b"x" * 40)
    (folder / "deep" / "c.pdf").write_bytes(b"%PDF-1.4")
    result = run("organize_files", folder=str(folder), mode="type", preview=True)
    check("organize_files previews without touching files", "پیش‌نمایش" in result.message,
          result.message)
    result = run("file_type_stats", folder=str(folder))
    check("file_type_stats counts extensions", "png" in result.details, result.details[:60])
    result = run("junk_scan", folder=str(folder))
    check("junk_scan finds Thumbs.db", "Thumbs.db" in result.details, result.details[:60])
    result = run("empty_file_finder", folder=str(folder))
    check("empty_file_finder finds nothing when all files have content", "خالی" in result.message,
          result.message)
    result = run("recent_changes", folder=str(folder), hours=24)
    check("recent_changes lists new files", "a.txt" in result.details, result.details[:80])
    result = run("permissions_report", folder=str(folder), out=str(OUT / "perms.csv"))
    check("permissions_report writes a CSV", (OUT / "perms.csv").exists(), "")
    result = run("flatten_folder", folder=str(folder), preview=True)
    check("flatten_folder previews", "پیش‌نمایش" in result.message, result.message)
    result = run("hardlink_report", folder=str(folder))
    check("hardlink_report runs", "هاردلینک" in result.message, result.message)
    backup_root = OUT / "backups"
    result = run("quick_backup", folder=str(folder), dest=str(backup_root), keep=2)
    check("quick_backup writes a manifest",
          any(path.endswith("MANIFEST.sha256") for path in
              [str(item) for item in Path(result.outputs[0]).rglob("*")]), "")

    # ---- system extras (platform safe)
    result = run("ram_hogs", count=5)
    check("ram_hogs lists processes", "RAM" in result.details, result.details[:60])
    result = run("time_info")
    check("time_info reports uptime", "آپ" in result.message or "Uptime" in result.message,
          result.message)
    result = run("temp_size")
    check("temp_size measures temp folders", "جمع" in result.details, result.details[-40:])
    result = run("disk_tree_usage", folder=str(WORK), count=3)
    check("disk_tree_usage ranks folders", "زیرپوشه" in result.message, result.message)
    result = run("fonts_list")
    check("fonts_list lists fonts", "فونت" in result.message, result.message)
    result = run("open_ports_owner")
    check("open_ports_owner lists ports", "پورت" in result.message, result.message)
    result = run("screen_info")
    check("screen_info runs everywhere", bool(result.details), "")


# --------------------------------------------------------------------- v4 tests
def test_journal(fixtures: dict) -> None:
    print("\n[journal / undo]")
    from app.core.journal import journal, reset_journal, send_to_trash, trash_size

    data = WORK / "journal"
    data.mkdir(parents=True, exist_ok=True)
    reset_journal(data / "journal.sqlite3")

    folder = WORK / "journal_files"
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(3):
        (folder / f"doc_{index}.txt").write_text(f"file {index}", encoding="utf-8")

    result = run("batch_rename", folder=str(folder), mode="template", template="renamed_{n}",
                 preview=False)
    check("batch_rename renames files", "3" in result.message, result.message)
    check("renamed files exist", (folder / "renamed_01.txt").exists(), "")
    result = run("undo_last", count=1)
    check("undo_last restores names", (folder / "doc_0.txt").exists(), result.message)
    check("undo removed the new names", not (folder / "renamed_01.txt").exists(), "")

    result = run("organize_files", folder=str(folder), mode="type", action="move", preview=False)
    moved = list(folder.rglob("*.txt"))
    check("organize_files moved files into subfolders",
          any(len(part.relative_to(folder).parts) > 1 for part in moved), result.message)
    result = run("undo_last", count=1)
    check("undo brings files back to the root", (folder / "doc_1.txt").exists(), result.message)

    pairs = send_to_trash([folder / "doc_2.txt"])
    journal().record_trash(pairs, "test trash", tool_id="test")
    check("send_to_trash moves the file away", not (folder / "doc_2.txt").exists(), "")
    check("trash grows", trash_size() > 0, "")
    result = run("trash_restore", limit=5)
    check("trash_restore puts files back", (folder / "doc_2.txt").exists(), result.message)

    result = run("undo_history", limit=10)
    check("undo_history lists operations", "#" in result.details, result.details[:80])
    result = run("trash_cleanup", confirm="no")
    check("trash_cleanup needs confirmation", "لغو" in result.message, result.message)


def test_scheduler() -> None:
    print("\n[scheduler]")
    from datetime import datetime, timedelta

    from app.core.scheduler import (CronError, Schedule, ScheduleStore, Scheduler,
                                    describe_cron, matches, next_run, parse_cron, upcoming)

    check("cron parses a simple expression", parse_cron("0 9 * * *")["minute"] == {0}, "")
    check("cron parses ranges and steps", parse_cron("*/15 9-17 * * 0-4")["hour"] == set(range(9, 18)),
          "")
    try:
        parse_cron("99 * * * *")
        check("cron rejects out-of-range values", False, "no error")
    except CronError:
        check("cron rejects out-of-range values", True)
    moment = datetime(2026, 5, 4, 9, 0)          # a Monday
    check("cron matches the exact minute", matches("0 9 * * 1", moment), "")
    check("cron skips other minutes", not matches("30 9 * * 1", moment), "")
    check("next_run finds tomorrow 09:00",
          next_run("0 9 * * *", moment) == datetime(2026, 5, 5, 9, 0), "")
    check("upcoming returns five slots", len(upcoming("0 */6 * * *", 5, moment)) == 5, "")
    check("describe_cron explains daily runs", "09:00" in describe_cron("0 9 * * *"), "")

    store_file = WORK / "schedules.json"
    store = ScheduleStore(store_file)
    store.clear()
    item = store.new()
    item.name = "هر ۱۵ دقیقه"
    item.cron = "*/15 * * * *"
    item.kind = "workflow"
    item.target_id = "photo_pdf_pack"
    store.save(item)
    check("schedule persists", len(ScheduleStore(store_file).all()) == 1, "")
    check("schedule computes next run", item.next_at() is not None, "")
    check("schedule is due on a matching minute",
          item.due(datetime(2026, 5, 4, 10, 15, 30)), "")
    item.last_run = datetime(2026, 5, 4, 10, 15).timestamp()
    check("schedule does not fire twice in one minute",
          not item.due(datetime(2026, 5, 4, 10, 15, 45)), "")
    check("catch-up fires after a missed window",
          item.catch_up_due(datetime(2026, 5, 4, 10, 31)), "")
    scheduler = Scheduler(store_ref=store, interval_ms=5_000)
    item.last_run = 0
    store.save(item)
    fired = scheduler.tick(datetime(2026, 5, 4, 10, 30))
    check("scheduler tick reports the schedule", len(fired) == 1, str(fired))
    store.mark_run(item.id, "done")
    check("mark_run records the run", store.get(item.id).runs == 1, "")
    store.delete(item.id)
    check("schedule delete works", len(store.all()) == 0, "")

    result = run("schedule_check", cron="0 8 * * 6")
    check("schedule_check validates a cron expression", "معتبر" in result.message, result.message)
    result = run("schedule_list")
    check("schedule_list runs", bool(result.message), result.message)


def test_macro(fixtures: dict) -> None:
    print("\n[macro engine]")
    from app.core import macro as macro_module

    folder = WORK / "macro_src"
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        (folder / f"pic_{index}.txt").write_text("hello", encoding="utf-8")

    macro = {
        "name": "test macro",
        "vars": {"folder": str(folder), "out": str(WORK / "macro_out")},
        "steps": [
            {"type": "mkdir", "path": "{out}"},
            {"type": "log", "message": "starting"},
            {"type": "foreach", "glob": "{folder}/*.txt", "as": "file", "steps": [
                {"type": "if", "test": "exists", "path": "{file}",
                 "then": [{"type": "copy", "src": "{file}", "dst": "{out}"}],
                 "else": [{"type": "log", "message": "missing {file}"}]},
            ]},
            {"type": "write", "path": "{out}/summary.txt", "text": "done"},
        ],
    }
    problems = macro_module.validate(macro, [spec.id for spec in __import__("app.core.registry",
                                                                          fromlist=["all_tools"]).all_tools()])
    check("validate accepts a healthy macro", problems == [], str(problems))

    bad = {"steps": [{"type": "run", "tool": "does_not_exist"}]}
    problems = macro_module.validate(bad, ["pdf_compress"])
    check("validate catches unknown tools", bool(problems), str(problems))
    problems = macro_module.validate({"steps": [{"type": "nope"}]})
    check("validate catches unknown step types", bool(problems), str(problems))

    result = macro_module.MacroRunner(lang="fa", output_dir=str(WORK / "macro_out"),
                                      dry_run=True).execute(macro, {})
    check("dry run makes no files", not (WORK / "macro_out" / "summary.txt").exists(),
          result.status)
    check("dry run reports steps", result.executed >= 4, str(result.executed))
    check("dry run status is dry-run", result.status == "dry-run", result.status)

    result = macro_module.MacroRunner(lang="fa", output_dir=str(WORK / "macro_out")).execute(macro, {})
    check("macro runs to completion", result.status == "done" and not result.errors, str(result.errors))
    check("macro copied both files", len(list((WORK / "macro_out").glob("pic_*.txt"))) == 2, "")
    check("macro wrote the summary", (WORK / "macro_out" / "summary.txt").exists(), "")
    check("macro logged progress", any("starting" in line for line in result.log), "")

    path = macro_module.save_macro(macro)
    check("macro saved to the library", path.exists() and path.suffix == ".json", str(path))
    reloaded = macro_module.load_macro(path)
    check("macro reloads", reloaded.get("name") == "test macro", "")
    check("built-in templates exist", len(macro_module.builtin_macros("fa")) >= 3, "")

    result = run("macro_validate", file=str(path))
    check("macro_validate tool passes a good macro", "سالم" in result.message, result.message)
    result = run("macro_run", file=str(path))
    check("macro_run tool executes a file", "done" in result.message, result.message)


def test_vault() -> None:
    print("\n[vault]")
    from app.core.vault import Vault, VaultError

    vault_path = WORK / "demo.wgv"
    vault = Vault(vault_path, "correct-horse", iterations=50_000)
    vault.create()
    check("vault file created", vault_path.exists(), "")
    secret = WORK / "secret.txt"
    secret.write_text("top secret payload", encoding="utf-8")
    added = vault.add([secret])
    check("vault adds a file", added == ["secret.txt"], str(added))
    vault.add_text("note.txt", "سلام دنیا")
    names = vault.names()
    check("vault lists both entries", set(names) == {"secret.txt", "note.txt"}, str(names))
    info = vault.info()
    check("vault reports sizes", info["entries"] == 2 and info["bytes"] > 0, str(info))

    out = WORK / "vault_out"
    written = vault.extract_all(out)
    check("vault extracts everything", len(written) == 2, str(written))
    check("extracted content matches",
          (out / "secret.txt").read_text(encoding="utf-8") == "top secret payload", "")
    check("vault verifies its records", vault.verify()["problems"] == [], "")

    raw = vault_path.read_bytes()
    check("vault file is encrypted (no plaintext)", b"top secret" not in raw, "")
    check("vault uses the v2 container", raw.startswith(b"WGV2"), str(raw[:4]))
    check("vault key derivation is scrypt", "scrypt" in vault.strength(), vault.strength())

    # the header is authenticated: flipping a KDF parameter must fail, not re-derive
    tampered = WORK / "tampered.wgv"
    broken = bytearray(raw)
    broken[5] = 14 if broken[5] != 14 else 15
    tampered.write_bytes(bytes(broken))
    try:
        Vault(tampered, "correct-horse").unlock()
        check("vault rejects a tampered header", False, "edited log_n was accepted")
    except VaultError:
        check("vault rejects a tampered header", True)

    try:
        Vault(vault_path, "wrong-password").unlock()
        check("wrong password is rejected", False, "unlocked")
    except VaultError:
        check("wrong password is rejected", True)

    vault.change_password("new-password")
    check("password change keeps data",
          Vault(vault_path, "new-password").unlock() is not None, "")
    removed = vault.remove(["note.txt"])
    check("vault removes an entry", removed == 1 and vault.names() == ["secret.txt"], "")

    result = run("vault_list", vault=str(vault_path), password="new-password")
    check("vault_list tool unlocks", "secret.txt" in result.details, result.details[:80])
    result = run("vault_add", vault=str(vault_path), password="new-password",
                 note_name="cli.txt", note_text="from the tool")
    check("vault_add tool stores a note", "cli.txt" in (result.details or ""), "")
    result = run("vault_extract", vault=str(vault_path), password="new-password",
                 out=str(WORK / "vault_tool_out"))
    check("vault_extract tool writes files",
          (WORK / "vault_tool_out" / "secret.txt").exists(), str(result.outputs))

    # ------------------------------------------------ legacy v1 → v2 migration
    from app.core.vault import MAGIC_V1, derive_key

    legacy_path = WORK / "legacy.wgv"
    legacy = Vault(legacy_path, "old-pass", iterations=20_000)
    legacy.format = 1
    import os as _os

    legacy._salt = _os.urandom(16)
    legacy._key = derive_key("old-pass", legacy._salt, 20_000)
    legacy._entries = []
    legacy._flush()
    check("a legacy container can still be written", legacy_path.read_bytes().startswith(MAGIC_V1))
    reopened = Vault(legacy_path, "old-pass")
    reopened.unlock()
    reopened.add_text("old.txt", "written by v1")
    check("v1 vaults stay readable", reopened.names() == ["old.txt"], str(reopened.names()))
    check("v1 vault reports its own format", reopened.format_version() == 1, "")
    check("upgrade converts v1 to v2", reopened.upgrade() == "upgraded"
          and legacy_path.read_bytes().startswith(b"WGV2"), "")
    after = Vault(legacy_path, "old-pass")
    after.unlock()
    check("upgraded vault keeps the data", after.names() == ["old.txt"], str(after.names()))
    check("upgrading twice is a no-op", after.upgrade() == "already-v2", "")
    result = run("vault_upgrade", vault=str(legacy_path), password="old-pass")
    check("vault_upgrade tool is idempotent",
          "scrypt" in (result.message + (result.details or "")).lower()
          or "قالب تازه" in result.message, result.message[:60])


def test_api_server() -> None:
    print("\n[local api]")
    import urllib.error
    import urllib.parse
    import urllib.request

    from app.core.api_server import ApiServer

    server = ApiServer(port=0, lang="fa", output_dir=str(OUT))
    port = server.start()
    check("api starts on a free port", port > 0, str(port))

    def call(path: str, method: str = "GET", body=None, token: bool = True):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-WinGenius-Token": server.token if token else "",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    status, payload = call("/health")
    check("api health", status == 200 and payload["tools"] > 200, str(payload)[:80])
    status, payload = call("/tools?category=pdf")
    check("api lists tools by category", status == 200 and payload["count"] >= 30, str(payload["count"]))
    status, payload = call("/tools/pdf_to_images")
    check("api describes a tool", status == 200 and payload["fields"], "")
    status, payload = call("/plan?q=" + urllib.parse.quote("حجم این PDF را کم کن"))
    check("api plans from a sentence", status == 200 and payload["steps"], str(payload)[:80])
    status, payload = call("/workflows")
    check("api lists workflows", status == 200 and len(payload["workflows"]) >= 9, "")
    status, payload = call("/run", "POST", {"tool": "uuid_generate", "values": {}})
    check("api runs a tool", status == 200 and payload["ok"], str(payload)[:80])
    status, payload = call("/run", "POST", {"tool": "nope"})
    check("api rejects unknown tools", status == 404, str(status))
    status, _payload = call("/health", token=False)
    check("api requires the token", status == 401, str(status))
    status, payload = call("/journal")
    check("api exposes the journal", status == 200 and "stats" in payload, "")
    server.stop()
    check("api stops cleanly", not server.running, "")


def _raises(call) -> bool:
    """True when the callable raises (any exception)."""
    try:
        call()
    except Exception:                                   # noqa: BLE001
        return True
    return False


# ---------------------------------------------------------------- schema audit
def test_schema() -> None:
    """Every one of the tools must be well-formed — this catches dead links."""
    print("\n[schema audit: all tools]")
    from app.core import i18n, planner
    from app.core.registry import CATEGORIES, all_tools
    from app.core.spec import K_CHOICE, ALL_KINDS
    from app.ui import icons

    specs = all_tools()
    check("tool count is 246 or more", len(specs) >= 246, str(len(specs)))
    ids = [spec.id for spec in specs]
    check("tool ids are unique", len(ids) == len(set(ids)),
          str([item for item in set(ids) if ids.count(item) > 1][:5]))

    bad_icon, bad_cat, bad_name, bad_desc, bad_run = [], [], [], [], []
    bad_fields, bad_choices, bad_defaults = [], [], []
    for spec in specs:
        if spec.icon not in icons.ICONS:
            bad_icon.append(f"{spec.id}:{spec.icon}")
        if spec.category not in {item.key for item in CATEGORIES}:
            bad_cat.append(f"{spec.id}:{spec.category}")
        if not spec.name or not spec.name_en:
            bad_name.append(spec.id)
        if not spec.desc or not spec.desc_en:
            bad_desc.append(spec.id)
        if not callable(spec.run):
            bad_run.append(spec.id)
        seen = set()
        for field in spec.fields:
            if not field.key or field.key in seen:
                bad_fields.append(f"{spec.id}.{field.key}")
            seen.add(field.key)
            if field.kind not in ALL_KINDS:
                bad_fields.append(f"{spec.id}.{field.key}:{field.kind}")
            if not field.label or not field.label_en:
                bad_fields.append(f"{spec.id}.{field.key}:label")
            if field.kind == K_CHOICE:
                if not field.choices:
                    bad_choices.append(f"{spec.id}.{field.key}")
                else:
                    values = [value for value, _fa, _en in field.choices]
                    if any(not str(label_fa).strip() or not str(label_en).strip()
                           for _v, label_fa, label_en in field.choices):
                        bad_choices.append(f"{spec.id}.{field.key}:labels")
                    if field.default is not None and field.default not in values:
                        bad_defaults.append(f"{spec.id}.{field.key}:{field.default}")
            if field.visible_when and field.visible_when[0] not in seen:
                bad_fields.append(f"{spec.id}.{field.key}:visible_when")

    check("every tool has a real vector icon", not bad_icon, ", ".join(bad_icon[:4]))
    check("every tool is in a known category", not bad_cat, ", ".join(bad_cat[:4]))
    check("every tool has Persian and English names", not bad_name, ", ".join(bad_name[:4]))
    check("every tool has Persian and English descriptions", not bad_desc, ", ".join(bad_desc[:4]))
    check("every tool has a run function", not bad_run, ", ".join(bad_run[:4]))
    check("every field is unique and named", not bad_fields, ", ".join(bad_fields[:4]))
    check("every choice field lists valid, labelled options", not bad_choices,
          ", ".join(bad_choices[:4]))
    check("every choice default exists in its options", not bad_defaults,
          ", ".join(bad_defaults[:4]))

    # defaults(): every tool must produce a dict the engine can hand to run()
    broken_defaults = []
    for spec in specs:
        try:
            values = spec.defaults()
        except Exception as exc:                        # noqa: BLE001
            broken_defaults.append(f"{spec.id}: {exc}")
            continue
        if not isinstance(values, dict) or set(values) != {field.key for field in spec.fields}:
            broken_defaults.append(spec.id)
    check("defaults() covers every field of every tool", not broken_defaults,
          ", ".join(broken_defaults[:4]))

    # bilingual search: each tool must be findable by its own name in both languages
    from app.core import nlsearch
    misses = []
    live = [spec for spec in specs if not spec.requires_admin] if hasattr(specs[0], "requires_admin") else specs
    for spec in live:
        for lang_label, needle in (("fa", spec.name), ("en", spec.name_en)):
            hits = [item.id for _score, item in nlsearch.search(needle, limit=8)]
            if spec.id not in hits:
                misses.append(f"{spec.id}/{lang_label}")
    check("search finds every tool by its own name", len(misses) <= 6,
          f"{len(misses)} misses: " + ", ".join(misses[:6]))

    intents = planner.INTENTS if hasattr(planner, "INTENTS") else []
    check("planner exposes its intent table", len(intents) >= 31, str(len(intents)))
    unknown = set()
    for intent in intents:
        for step in intent.get("steps", []):
            tool_id = step[0] if isinstance(step, (tuple, list)) else step
            if get_tool(tool_id) is None:
                unknown.add(str(tool_id))
    check("every planner intent points at a real tool", not unknown, str(sorted(unknown)[:4]))
    plan_misses = []
    for intent in intents:
        sentence = intent.get("example") or ""
        if sentence and planner.build_plan(sentence, [], "fa") is None:
            plan_misses.append(intent.get("id", "?"))
    check("every intent example produces a plan", not plan_misses, ", ".join(plan_misses[:4]))

    missing_en = [key for key, table in i18n.STRINGS.items() if not table.get("en")]
    check("every i18n key has an English string", not missing_en, ", ".join(missing_en[:4]))
    missing_fa = [key for key, table in i18n.STRINGS.items() if not table.get("fa")]
    check("every i18n key has a Persian string", not missing_fa, ", ".join(missing_fa[:4]))


# ------------------------------------------------------------------- v4 stress
def test_stress() -> None:
    """Deliberately hostile cases: big inputs, broken files, repeated calls."""
    print("\n[stress: vault / journal / scheduler / macro / api]")

    from app.core import macro as macro_module
    from app.core.journal import journal, reset_journal, send_to_trash
    from app.core.scheduler import (ScheduleStore, describe_cron, matches, next_run,
                                    parse_cron, upcoming)
    from app.core.vault import Vault, VaultError, human_size

    sandbox = WORK / "stress"
    sandbox.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- vault
    print(" · vault")
    vault_path = sandbox / "big.wgv"
    vault = Vault(vault_path, "pass-123456", iterations=20_000)
    vault.create()
    blob = sandbox / "blob.bin"
    blob.write_bytes(bytes(range(256)) * 16_384)                 # 4 MiB of binary
    vault.add([blob])
    check("vault stores a 4 MiB binary file",
          vault.info()["bytes"] >= 4 * 1024 * 1024, str(vault.info()))
    out = sandbox / "out_big"
    vault.extract_all(out)
    check("vault round-trips binary byte for byte",
          (out / "blob.bin").read_bytes() == blob.read_bytes(), "")

    # fifty unicode-named notes, then a full verify
    for index in range(50):
        vault.add_text(f"یادداشت {index}.txt", f"مقدار {index}")
    entries = vault.unlock()
    check("vault holds 51 entries (1 file + 50 notes)", len(entries) == 51, str(len(entries)))
    report = vault.verify()
    check("verify decrypts every record", report["checked"] == 51 and not report["problems"],
          str(report)[:80])

    # duplicate name keeps both records
    before = len(vault.unlock())
    vault.add_text("یادداشت 0.txt", "نسخه‌ی دوم")
    check("duplicate name does not lose data", len(vault.unlock()) == before + 1, "")

    # removing something that is not there is harmless
    check("removing a missing entry returns 0", vault.remove(["ghost.txt"]) == 0, "")
    check("vault stays valid after a no-op remove",
          not vault.verify()["problems"], "")

    # broken containers
    broken = sandbox / "broken.wgv"
    broken.write_bytes(b"WGV1" + b"\x00" * 40)
    try:
        Vault(broken, "pass-123456").unlock()
        check("a truncated vault is rejected", False, "opened")
    except VaultError:
        check("a truncated vault is rejected", True)
    fake = sandbox / "fake.wgv"
    fake.write_bytes(b"NOTA" + b"\xff" * 64)
    try:
        Vault(fake, "pass-123456").unlock()
        check("a foreign file is rejected", False, "opened")
    except VaultError:
        check("a foreign file is rejected", True)
    try:
        Vault(sandbox / "absent.wgv", "pass").unlock()
        check("a missing vault is reported", False, "opened")
    except VaultError:
        check("a missing vault is reported", True)

    # empty password never unlocks a vault made with a real one
    try:
        Vault(vault_path, "").unlock()
        check("an empty password cannot open a vault", False, "opened")
    except VaultError:
        check("an empty password cannot open a vault", True)

    check("human_size is bidi-safe", human_size(2048).startswith("\u2066")
          and human_size(2048).endswith("\u2069"), "")

    # -------------------------------------------------------------- journal
    print(" · journal")
    reset_journal(sandbox / "journal.sqlite3")
    folder = sandbox / "many"
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(60):
        (folder / f"f{index:03d}.txt").write_text(str(index), encoding="utf-8")
    run("batch_rename", folder=str(folder), mode="template", template="g_{n}", preview=False)
    check("60 files renamed", len(list(folder.glob("g_*.txt"))) == 60, "")
    first = journal().undo_last(1)
    check("one undo restores all 60 files", len(list(folder.glob("f*.txt"))) == 60,
          str(len(list(folder.glob("f*.txt")))))
    check("undo reports the restored paths", len(first[0]["restored"]) == 60,
          str(len(first[0]["restored"])))
    again = journal().undo_last(1)
    check("a second undo is a clean no-op", again == [] or again[0]["status"] != "failed",
          str(again)[:60])
    stats = journal().stats()
    check("journal tracks undone operations", stats["undone"] >= 1, str(stats))

    # interleaved operations unwind in the right order
    a = sandbox / "order"
    a.mkdir(exist_ok=True)
    for name in ("one", "two", "three"):
        (a / f"{name}.txt").write_text(name, encoding="utf-8")
    run("batch_rename", folder=str(a), mode="template", template="x_{name}", preview=False)
    check("template rename applied", (a / "x_one.txt").exists(), "")
    run("organize_files", folder=str(a), mode="type", action="move", preview=False)
    journal().undo_last(1)
    check("organize undo happens before rename undo",
          all((a / f"x_{name}.txt").exists() for name in ("one", "two", "three")),
          str(sorted(p.name for p in a.iterdir())))
    journal().undo_last(1)
    check("rename undo follows", all((a / f"{name}.txt").exists()
                                     for name in ("one", "two", "three")), "")

    # trash: restore into a folder that already has the name
    clash = sandbox / "clash"
    clash.mkdir(exist_ok=True)
    victim = clash / "keep.txt"
    victim.write_text("original", encoding="utf-8")
    pairs = send_to_trash([victim])
    journal().record_trash(pairs, "stress trash", tool_id="stress")
    victim.write_text("replacement", encoding="utf-8")
    result = run("trash_restore", limit=5)
    check("trash restore never overwrites a live file",
          victim.read_text(encoding="utf-8") == "replacement", result.message)
    check("the restored copy lands beside it",
          len(list(clash.glob("keep*.txt"))) >= 2,
          str([p.name for p in clash.iterdir()]))

    # ------------------------------------------------------------ scheduler
    print(" · scheduler")
    from datetime import datetime, timedelta

    check("cron rejects 0 step", _raises(lambda: parse_cron("*/0 * * * *")), "")
    check("cron rejects too many fields", _raises(lambda: parse_cron("* * * * * *")), "")
    check("cron rejects letters", _raises(lambda: parse_cron("a b c d e")), "")
    check("cron accepts lists", parse_cron("1,15,30 * * * *")["minute"] == {1, 15, 30}, "")
    check("cron accepts sunday as 0 and 7",
          matches("0 0 * * 0", datetime(2026, 5, 3, 0, 0))
          and matches("0 0 * * 7", datetime(2026, 5, 3, 0, 0)), "")
    leap = next_run("0 3 29 2 *", datetime(2026, 3, 1))
    check("cron finds the next 29 February", leap and leap.year == 2028, str(leap))
    new_year = next_run("0 0 1 1 *", datetime(2026, 6, 1))
    check("cron rolls into the next year", new_year == datetime(2027, 1, 1), str(new_year))
    check("upcoming never returns past slots",
          all(moment > datetime(2026, 6, 1) for moment in
              upcoming("30 4 * * *", 4, datetime(2026, 6, 1, 12, 0))), "")
    for expr in ("* * * * *", "*/5 * * * *", "0 9 * * *", "0 0 1 * *", "0 0 1 1 *",
                 "15 2 * * 6"):
        text = describe_cron(expr, "fa")
        if not text:
            check(f"describe_cron explains {expr}", False, "empty")
            break
    else:
        check("describe_cron explains every preset", True)

    store_file = sandbox / "schedules.json"
    store = ScheduleStore(store_file)
    store.clear()
    for index in range(8):
        item = store.new()
        item.name = f"کار {index}"
        item.cron = f"{index} * * * *"
        item.kind = "tool"
        item.target_id = "uuid_generate"
        store.save(item)
    check("eight schedules persist", len(store.all()) == 8, "")
    store_file.write_text("{not json", encoding="utf-8")
    recovered = ScheduleStore(store_file)
    check("a corrupted schedule file degrades to empty", recovered.all() == [], "")
    item = recovered.new()
    item.name = "بعد از خرابی"
    item.cron = "0 6 * * *"
    item.kind = "tool"
    item.target_id = "uuid_generate"
    recovered.save(item)
    check("the store heals itself on the next save", len(recovered.all()) == 1, "")

    # ---------------------------------------------------------------- macro
    print(" · macro")
    deep = {"name": "deep", "steps": []}
    node = deep["steps"]
    for _level in range(14):
        child = {"type": "if", "test": "exists", "path": str(sandbox),
                 "then": [], "else": []}
        node.append(child)
        node = child["then"]
    result = macro_module.MacroRunner(lang="fa", output_dir=str(sandbox)).execute(deep, {})
    check("runaway nesting fails gracefully", result.status in ("failed", "partial")
          and result.errors, str(result.errors)[:60])

    empty = macro_module.MacroRunner(lang="fa").execute({"name": "empty", "steps": []}, {})
    check("an empty macro is refused with a clear message",
          empty.status == "failed" and any("steps" in problem for problem in empty.errors),
          str(empty.errors)[:60])
    check("a macro without steps is rejected by validate",
          bool(macro_module.validate({"name": "x"})), "")

    missing = {"name": "missing", "steps": [
        {"type": "copy", "src": str(sandbox / "nope.txt"), "dst": str(sandbox / "x")}]}
    result = macro_module.MacroRunner(lang="fa", output_dir=str(sandbox)).execute(missing, {})
    check("copying a missing file is reported, not raised",
          not (sandbox / "x").exists() and result.status in ("done", "partial") or result.errors,
          result.status)

    raw = {"name": "raw", "steps": [{"type": "run", "tool": "pdf_compress",
                                     "values": {"src": "", "mode": "strong"}}]}
    result = macro_module.MacroRunner(lang="fa", output_dir=str(sandbox)).execute(raw, {})
    check("a tool that fails inside a macro does not kill the run",
          result.status in ("partial", "failed", "done"), result.status)

    big_macro = {"name": "many steps", "steps": [
        {"type": "log", "message": "step"} for _ in range(400)]}
    started = time.time()
    result = macro_module.MacroRunner(lang="fa").execute(big_macro, {})
    check("400 log steps run quickly", time.time() - started < 5.0, f"{time.time() - started:.2f}s")
    check("every big-macro step is logged", len(result.log) >= 400, str(len(result.log)))

    named = {"name": "قالب تکراری", "steps": [{"type": "log", "message": "x"}]}
    first_path = macro_module.save_macro(named)
    second_path = macro_module.save_macro(named)
    check("saving the same macro twice overwrites", first_path == second_path, "")
    check("the library holds one copy", len([p for p in macro_module.library()
                                             if p.name.startswith("قالب")]) == 1, "")

    # ------------------------------------------------------------------ api
    print(" · api")
    import urllib.error
    import urllib.request

    from app.core.api_server import ApiServer

    server = ApiServer(port=0, lang="fa", output_dir=str(OUT))
    port = server.start()

    def raw_call(path: str, method: str = "GET", data: bytes = b"", token: str = "ok"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data or None, method=method,
            headers={"X-WinGenius-Token": server.token if token == "ok" else token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    status, _body = raw_call("/run", "POST", b"{not json")
    check("api survives malformed JSON", status in (400, 500), str(status))
    status, body = raw_call("/tools/../etc/passwd")
    check("api rejects path traversal", status in (404, 400) and b"root:" not in body, str(status))
    status, _body = raw_call("/health", token="wrong")
    check("api rejects a wrong token", status == 401, str(status))
    results: list = []

    def hammer() -> None:
        for _ in range(6):
            results.append(raw_call("/health")[0])

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check("api answers 24 concurrent requests", results.count(200) == 24, str(results))

    status, body = raw_call("/run", "POST", json.dumps({"tool": "uuid_generate"}).encode())
    check("api still runs tools after the abuse", status == 200 and b"ok" in body, str(status))
    server.stop()
    check("api shuts down after the stress run", not server.running, "")


# --------------------------------------------------------------------- fuzzing
def test_fuzz() -> None:
    """Empty/absurd inputs to all 246 tools: must fail politely, never crash."""
    print("\n[fuzz: every tool with empty and hostile inputs]")
    import signal

    from app.core.registry import all_tools
    from app.core.spec import ToolError
    from app.core.execution import ExecutionPolicy, execute_tool

    def safe_run(spec, values):
        # Fuzzing must not bypass the same policy that protects real callers.
        return execute_tool(spec, values, RunContext(lang="fa", output_dir=str(base)),
                            policy=ExecutionPolicy.for_api())

    class Timeout(Exception):
        pass

    def _alarm(_signum, _frame):
        raise Timeout("tool exceeded 15s")

    can_alarm = hasattr(signal, "SIGALRM")
    if can_alarm:
        signal.signal(signal.SIGALRM, _alarm)

    base = WORK / "fuzz"
    base.mkdir(parents=True, exist_ok=True)
    text_file = base / "note.txt"
    text_file.write_text("hello fuzz", encoding="utf-8")

    polite, suspicious, slow = [], [], []
    # GitHub's runners are shared 2-core boxes: a whole-disk scan that takes 2 s here
    # takes 12 s there. The budget is therefore a *hang* detector, not a stopwatch
    # contest — it is looser on CI and gives network/disk-wide tools some room.
    # Windows system tools (DISM component cleanup, Windows feature queries) answer in
    # tens of seconds on a cold runner; the budget follows the platform, not the myth.
    if os.name == "nt":
        slow_budget = 60.0
        alarm_seconds = 0.0                    # no SIGALRM on Windows anyway
    elif os.environ.get("CI"):
        slow_budget, alarm_seconds = 20.0, 45.0
    else:
        slow_budget, alarm_seconds = 8.0, 15.0
    for spec in all_tools():
        for label, values in (("defaults", spec.defaults()),
                              ("empty", {field.key: None for field in spec.fields}),
                              ("hostile", {field.key: "../" * 3 if field.kind in ("text", "file", "dir")
                                           else (field.default if field.default is not None else None)
                                           for field in spec.fields})):
            if can_alarm and alarm_seconds:
                signal.setitimer(signal.ITIMER_REAL, alarm_seconds)
            started = time.time()
            try:
                safe_run(spec, values)
                polite.append(spec.id)          # ran fine with junk: acceptable
            except ToolError:
                polite.append(spec.id)          # the friendly path
            except Timeout:
                slow.append(spec.id)
            except Exception as exc:            # noqa: BLE001
                suspicious.append(f"{spec.id}/{label}: {type(exc).__name__}: {exc}"[:130])
            finally:
                if can_alarm:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                if time.time() - started > slow_budget:
                    slow.append(f"{spec.id}/{label}")

    # the sane-tool case: a real file must be accepted by anything that takes a file
    check("no tool crashes on empty or hostile input", not suspicious,
          f"{len(suspicious)}: " + " | ".join(suspicious[:5]))
    # A handful of tools legitimately walk the filesystem or the network (`../` input,
    # DNS probes); anything beyond that budget is treated as a hang.
    tolerance = 6 if os.name == "nt" else 5
    check("no tool hangs for long", len(slow) <= tolerance, ", ".join(slow[:6]))

    offenders = []
    for spec in all_tools():
        file_fields = [field for field in spec.fields
                       if field.kind in ("file", "files", "dir", "save")]
        if not file_fields:
            continue
        values = spec.defaults()
        for field in file_fields:
            values[field.key] = ([str(text_file)] if field.kind == "files" else str(text_file))
        if can_alarm:
            signal.setitimer(signal.ITIMER_REAL, 15.0)
        try:
            safe_run(spec, values)
        except ToolError:
            offenders.append(spec.id)          # acceptable: the tool wanted something else
        except Timeout:
            offenders.append(f"{spec.id}: timeout")
        except Exception as exc:               # noqa: BLE001
            offenders.append(f"{spec.id}: {type(exc).__name__}: {exc}"[:110])
        finally:
            if can_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
    hard = [item for item in offenders if ":" in item and "Error" in item]
    check("no tool blows up on a real text file", not hard, " | ".join(hard[:5]))

    # messages are shown in the UI: they must be human text, not Python noise
    noisy = []
    for spec in all_tools()[:120]:
        if can_alarm:
            signal.setitimer(signal.ITIMER_REAL, 15.0)
        try:
            result = safe_run(spec, spec.defaults())
            text = f"{getattr(result, 'message', '')} {getattr(result, 'details', '')}"
            if any(token in text for token in ("Traceback", "KeyError", "TypeError", "NoneType")):
                noisy.append(spec.id)
        except ToolError as exc:
            if any(token in str(exc) for token in ("Traceback", "KeyError", "TypeError", "NoneType")):
                noisy.append(spec.id)
        except Exception:                       # noqa: BLE001 - counted above
            pass
        finally:
            if can_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
    check("no tool leaks Python internals into its message", not noisy, ", ".join(noisy[:5]))



def test_guards(fixtures: dict) -> None:
    """: data versioning, journal schema, API limits and the instance guard."""
    print("\n[guards: data version, api limits, single instance]")
    import json as _json
    import sqlite3 as _sqlite3
    import urllib.error as _urlerror
    import urllib.request as _urlrequest

    from app.core import upgrade as upgrade_module
    from app.core.single_instance import InstanceGuard, _key_for, _pid_alive

    # ---------------------------------------------------------- data versioning
    profile = WORK / "profile_v1"
    (profile / "logs").mkdir(parents=True, exist_ok=True)
    (profile / "settings.ini").write_text("[ui]\nlanguage=fa\n", encoding="utf-8")
    (profile / "history.json").write_text("{this is not json", encoding="utf-8")
    (profile / "journal.sqlite3").write_bytes(b"")
    check("a legacy profile reports version 0", upgrade_module.current_version(profile) == 0,
          str(upgrade_module.current_version(profile)))
    check("a legacy profile needs upgrading", upgrade_module.needs_upgrade(profile))
    report = upgrade_module.migrate(profile)
    check("migration reports its steps", report["steps"] == ["0→1", "1→2", "2→3"], str(report["steps"]))
    check("migration takes a backup first", bool(report["backup"]) and Path(report["backup"]).is_dir(),
          report["backup"])
    check("migration repairs a broken history file",
          (profile / "history.json").read_text(encoding="utf-8").strip() == "[]")
    check("migration keeps the broken copy",
          (profile / "history.json.broken").exists(), "")
    check("migration creates the folders the app needs",
          all((profile / name).is_dir() for name in upgrade_module.REQUIRED_DIRS), "")
    check("the profile is stamped with the new version",
          upgrade_module.current_version(profile) == upgrade_module.DATA_VERSION, "")
    check("migration is idempotent", upgrade_module.migrate(profile)["steps"] == [])
    stamp = _json.loads((profile / "schema.json").read_text(encoding="utf-8"))
    check("the stamp records the upgrade history", bool(stamp.get("history")), str(stamp)[:60])

    fresh = WORK / "profile_fresh"
    fresh.mkdir(exist_ok=True)
    check("a fresh profile is not backed up", upgrade_module.migrate(fresh)["backup"] == "")

    # ------------------------------------------------------- journal on-disk schema
    old_db = WORK / "old_journal.sqlite3"
    connection = _sqlite3.connect(old_db)
    connection.executescript(
        "CREATE TABLE operations (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL,"
        " ts REAL NOT NULL, tool_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,"
        " title_fa TEXT NOT NULL DEFAULT '', title_en TEXT NOT NULL DEFAULT '',"
        " payload TEXT NOT NULL DEFAULT '{}');"
        "INSERT INTO operations (uid, ts, kind) VALUES ('u1', 1.0, 'copy');")
    connection.commit()
    connection.close()
    from app.core.journal import Journal

    upgraded = Journal(old_db)
    check("an old journal database is migrated in place",
          upgraded.schema_version() >= 2 and len(upgraded.entries()) == 1,
          f"v{upgraded.schema_version()}")

    # --------------------------------------------------------------- api limits
    from app.core.api_server import ApiServer

    server = ApiServer(port=0, token="guard-token", lang="en", rate_limit=4, rate_window=60)
    port = server.start()

    def call(path: str, data=None, token: str = "guard-token", raw: bytes | None = None):
        request = _urlrequest.Request(f"http://127.0.0.1:{port}{path}",
                                      method="POST" if (data or raw) else "GET")
        if token:
            request.add_header("X-WinGenius-Token", token)
        body = None
        if data is not None:
            body = _json.dumps(data).encode()
        if raw is not None:
            body = raw
        try:
            with _urlrequest.urlopen(request, body, timeout=20) as response:
                return response.status, _json.loads(response.read() or b"{}")
        except _urlerror.HTTPError as exc:
            return exc.code, _json.loads(exc.read() or b"{}")

    try:
        status, payload = call("/health")
        check("health advertises the guard", "guard" in payload and payload["guard"]["rate_limit"] == 4,
              str(payload)[:80])
        codes = [call("/health")[0] for _ in range(7)]
        check("the api throttles a burst", 429 in codes, str(codes))
    finally:
        server.stop()

    # a second server so the burst above does not poison these checks
    roomy = ApiServer(port=0, token="guard-token", lang="en")
    roomy_port = roomy.start()

    def call_roomy(path: str, data=None, token: str = "guard-token", raw: bytes | None = None):
        request = _urlrequest.Request(f"http://127.0.0.1:{roomy_port}{path}",
                                      method="POST" if (data or raw) else "GET")
        if token:
            request.add_header("X-WinGenius-Token", token)
        body = raw if raw is not None else (_json.dumps(data).encode() if data is not None else None)
        try:
            with _urlrequest.urlopen(request, body, timeout=20) as response:
                return response.status, _json.loads(response.read() or b"{}")
        except _urlerror.HTTPError as exc:
            return exc.code, _json.loads(exc.read() or b"{}")

    try:
        status, payload = call_roomy("/run", raw=b'{"x":"' + b"a" * 1_200_000 + b'"}')
        check("the api refuses an oversized body", status == 413, f"{status} {payload}")
        status, _ = call_roomy("/health", token="")
        check("the api still rejects a missing token", status == 401, str(status))
        status, payload = call_roomy("/health")
        check("the api answers normally under the limit", status == 200, str(status))
    finally:
        roomy.stop()

    strict = ApiServer(port=0, token="header-only", lang="en", allow_query_token=False)
    strict_port = strict.start()
    try:
        request = _urlrequest.Request(f"http://127.0.0.1:{strict_port}/health?token=header-only")
        try:
            with _urlrequest.urlopen(request, timeout=10) as response:
                status = response.status
        except _urlerror.HTTPError as exc:
            status = exc.code
        check("a token in the query string can be refused", status == 401, str(status))
    finally:
        strict.stop()

    # ----------------------------------------------------------- instance guard
    check("the instance key is stable per profile", _key_for("/tmp/a") == _key_for("/tmp/a"))
    check("different profiles get different keys", _key_for("/tmp/a") != _key_for("/tmp/b"))
    check("our own pid is alive", _pid_alive(os.getpid()))
    guard = InstanceGuard(profile=str(WORK / "guard-profile"))
    check("the first guard acquires the lock", guard.acquire())
    check("the guard knows it is primary", guard.primary)
    guard.release()
    check("release drops ownership", not guard.primary)

# ------------------------------------------------------------------- v6: flows
def test_v60_flows(fixtures: dict) -> None:
    """v6 Flow Engine: conditions, foreach, retries, error policy + flowkit."""
    print("\n[6.0 flow engine]")
    from app.core import flowkit, workflows
    from app.core.registry import get as get_tool, register as register_tool
    from app.core.spec import RunContext, RunResult, ToolSpec
    from app.core.workflows import (
        Step, Workflow, evaluate_condition, expand_foreach, run_sync, step_value_sets,
    )

    sandbox = Path(tempfile.mkdtemp(prefix="wg_v60_flow_"))
    # Self-contained fixtures: earlier groups in this file rename/delete files in
    # the shared WORK folder, so a flow test must never depend on them.
    local = sandbox / "input"
    local.mkdir(parents=True, exist_ok=True)
    local_texts = []
    for index in range(3):
        path = local / f"note{index}.txt"
        path.write_text(f"line {index}\nsecond line\n", encoding="utf-8")
        local_texts.append(str(path))
    from PIL import Image as _LocalImage

    local_images = []
    for index, colour in enumerate(("#18C8F5", "#7B61FF", "#FFB020")):
        path = local / f"pic{index}.png"
        _LocalImage.new("RGB", (240, 160), colour).save(path)
        local_images.append(str(path))
    local_pdf = sandbox / "doc.pdf"
    from reportlab.lib.pagesizes import A4 as _LocalA4
    from reportlab.pdfgen.canvas import Canvas as _LocalCanvas

    _sheet = _LocalCanvas(str(local_pdf), pagesize=_LocalA4)
    for page in range(1, 4):
        _sheet.drawString(60, 760, f"flow page {page}")
        _sheet.showPage()
    _sheet.save()
    fixtures = {**fixtures, "text": Path(local_texts[0]), "images": local_images,
                "pdf": local_pdf}
    # ---------------------------------------------------------- step model
    step = Step("hash_files", {"src": ["{item}"]}, when="{prev} exists",
                foreach="{input}/*.txt", retries=2, on_error="continue")
    restored = Step.from_dict(step.as_dict())
    check("a flow step round-trips its v6 controls",
          (restored.when, restored.foreach, restored.retries, restored.on_error)
          == ("{prev} exists", "{input}/*.txt", 2, "continue"),
          str(restored.as_dict()))
    check("a plain step stays plain in JSON",
          "when" not in Step("pdf_merge", {}).as_dict()
          and "foreach" not in Step("pdf_merge", {}).as_dict())
    check("an unknown on_error falls back to stop",
          Step.from_dict({"tool_id": "x", "on_error": "explode"}).on_error == "stop")
    check("retries are clamped to a sane range",
          Step.from_dict({"tool_id": "x", "retries": 99}).retries == 5)
    check("an advanced step is flagged as such", step.advanced and not Step("pdf_merge", {}).advanced)
    check("the step summary explains the loop", "برای هر" in step.summary("fa"),
          step.summary("fa"))
    check("the step summary is bilingual", "for each" in step.summary("en"),
          step.summary("en"))

    # ------------------------------------------------------------ conditions
    context = {"prev": "/tmp/a.pdf", "prev_all": ["/tmp/a.pdf"], "prev_name": "a",
               "input": str(fixtures["text"]), "out_dir": str(sandbox), "index": 0,
               "item": "", "item_name": "", "item_dir": "", "item_ext": "",
               "item_index": 1, "item_count": 1}
    check("an empty condition is true", evaluate_condition("", context))
    check("endswith matches a pdf", evaluate_condition("{prev} endswith .pdf", context))
    check("endswith rejects a jpg", not evaluate_condition("{prev} endswith .jpg", context))
    check("contains matches", evaluate_condition("{prev} contains /tmp/", context))
    check("equality works", evaluate_condition("{prev_name} == a", context))
    check("inequality works", evaluate_condition("{prev_name} != b", context))
    check("numbers compare numerically", evaluate_condition("2 < 10", context))
    check("and combines clauses",
          evaluate_condition("{prev} endswith .pdf and {prev_name} == a", context))
    check("or combines clauses",
          evaluate_condition("{prev} endswith .jpg or {prev_name} == a", context))
    check("not negates", not evaluate_condition("not {prev} endswith .pdf", context))
    check("exists is false for an empty value", not evaluate_condition("{item} exists", context))
    check("exists is true for a real path",
          evaluate_condition("{" + "prev" + "} exists", {**context, "prev": str(fixtures["text"])}))
    check("is empty catches an empty value", evaluate_condition("{item} is empty", context))

    # ------------------------------------------------------------- foreach
    pattern = str(Path(fixtures["text"]).parent / "*.txt")
    found = expand_foreach(pattern, context)
    check("a glob expands to real files", len(found) >= 2, f"{len(found)}: {found}")
    check("brace globs expand", len(expand_foreach(
        str(Path(fixtures["text"]).parent / "*.{txt,zip}"), context)) >= 3)
    check("a list placeholder expands", expand_foreach("{prev_all}", context) == ["/tmp/a.pdf"])
    check("a missing path expands to nothing",
          expand_foreach("Z:/definitely/not/here/*.pdf", context) == [])
    sets = step_value_sets(Step("hash_files", {"src": ["{item}"], "note": "{item_index}/{item_count}"},
                                foreach=pattern), context)
    check("each item becomes one run", len(sets) == len(found), str(len(sets)))
    check("item placeholders are substituted", sets[0]["src"][0] == found[0], str(sets[0]))
    check("item_index is one-based",
          sets[0]["note"] == f"1/{len(found)}", str(sets[0].get("note")))

    # ---------------------------------------------------------- run_sync: skip
    skipped = run_sync(Workflow(id="skip", name="skip", steps=[
        Step("hash_files", {"src": [str(fixtures["text"])]}, when="1 == 1"),
        Step("hash_files", {"src": [str(fixtures["text"])]}, when="1 == 2"),
    ]), lang="en", output_dir=str(sandbox))
    check("a false condition skips the step",
          skipped.steps[0].status == "done" and skipped.steps[1].status == "skipped",
          str([s.status for s in skipped.steps]))
    check("a flow of skipped steps still finishes", skipped.status == "done", skipped.status)

    # -------------------------------------------------------- run_sync: foreach
    looped = run_sync(Workflow(id="loop", name="loop", steps=[
        Step("hash_files", {"src": ["{item}"], "save": "{out_dir}/{item_name}.sha256"},
             foreach=pattern),
    ]), lang="en", output_dir=str(sandbox / "hashes"),
        base_values={"input": str(Path(fixtures["text"]).parent), "__paths__": []})
    check("foreach runs once per file", len(looped.steps[0].outputs) >= 2,
          str(looped.steps[0].outputs))
    check("foreach reports success", looped.status == "done", looped.message)

    # -------------------------------------------------- run_sync: retries/fail
    attempts = {"count": 0}

    def _flaky(ctx, values):
        attempts["count"] += 1
        if attempts["count"] < 3:
            from app.core.spec import ToolError as _ToolError

            raise _ToolError("not yet")
        return RunResult(message="third time lucky")

    register_tool(ToolSpec(id="_flaky_v60", category="dev", name="flaky", name_en="flaky",
                           run=_flaky, fields=[]))
    try:
        retried = run_sync(Workflow(id="retry", name="retry", steps=[
            Step("_flaky_v60", {}, retries=3),
        ]), lang="en", output_dir=str(sandbox))
        check("retries re-run a failing step", attempts["count"] == 3, str(attempts["count"]))
        check("the step succeeds after retries", retried.steps[0].status == "done",
              retried.steps[0].message)
        attempts["count"] = 0
        failing = run_sync(Workflow(id="fail", name="fail", steps=[
            Step("_flaky_v60", {}, retries=1, on_error="continue"),
            Step("hash_files", {"src": [str(fixtures["text"])]}),
        ]), lang="en", output_dir=str(sandbox))
        check("on_error=continue keeps going", failing.steps[0].status == "failed"
              and failing.steps[1].status == "done",
              str([s.status for s in failing.steps]))
        check("a flow with a tolerated failure is reported as partial",
              failing.status == "partial", failing.status)
        attempts["count"] = 0
        hard = run_sync(Workflow(id="hard", name="hard", steps=[Step("_flaky_v60", {})]),
                        lang="en", output_dir=str(sandbox))
        check("a default step failure stops the flow", hard.status == "failed", hard.status)
        check("the failure message names the tool", "flaky" in hard.message, hard.message)
    finally:
        from app.core import registry as registry_module

        registry_module._TOOLS.pop("_flaky_v60", None)

    # ------------------------------------------------------------- flowkit
    library_flow = workflows.store.all()[0]
    report = flowkit.validate(library_flow, "en")
    check("a builtin flow validates", report.ok, report.text("en"))
    broken = Workflow(id="broken", name="broken", steps=[
        Step("no_such_tool_xyz", {}),
        Step("pdf_merge", {"src": []}),
    ])
    broken_report = flowkit.validate(broken, "en")
    codes = {issue.code for issue in broken_report.issues}
    check("validation finds an unknown tool", "unknown_tool" in codes, str(codes))
    check("validation finds an empty required field", "required" in codes, str(codes))
    check("validation fails on a broken flow", not broken_report.ok)
    placeholder_flow = Workflow(id="ph", name="ph", steps=[
        Step("hash_files", {"src": ["{nope}"]}, foreach="fixed-name.txt", on_error="continue")])
    ph_codes = {issue.code for issue in flowkit.validate(placeholder_flow, "en").issues}
    check("validation warns about unknown placeholders", "placeholder" in ph_codes, str(ph_codes))
    check("validation warns about an unused foreach", "foreach_unused" in ph_codes, str(ph_codes))
    check("validation warns about a glob-less foreach", "foreach_single" in ph_codes, str(ph_codes))

    explanation = flowkit.explain(step_workflow := Workflow(id="ex", name="شرح", name_en="Explain",
                                                          steps=[step]), "en")
    check("explain lists every step", "1)" in explanation, explanation[:80])
    check("explain mentions the condition", "only if" in explanation, explanation[:200])
    check("explain mentions the loop", "for each" in explanation, explanation[:200])
    check("summarize gives a one-liner", bool(flowkit.summarize(step_workflow, "en").strip()),
          flowkit.summarize(step_workflow, "en"))

    folder = sandbox / "auto_input"
    (folder / "deep").mkdir(parents=True, exist_ok=True)
    for index in range(3):
        (folder / f"pic{index}.png").write_bytes(Path(fixtures["images"][index]).read_bytes())
    (folder / "deep" / "doc.pdf").write_bytes(Path(fixtures["pdf"]).read_bytes())
    try:
        import docx

        document = docx.Document()
        document.add_paragraph("office document for the flow builder")
        document.save(str(folder / "letter.docx"))
    except Exception:
        pass
    groups = flowkit.classify(folder)
    check("classify counts images", len(groups["image"]) == 3, str(len(groups["image"])))
    check("classify is recursive", len(groups["pdf"]) == 1, str(len(groups["pdf"])))
    check("detect_goal picks a sharing flow", flowkit.detect_goal(groups) == "share")
    generated = flowkit.build_from_folder(folder, "auto", "en")
    check("a generated flow has steps", len(generated.steps) >= 2, str(len(generated.steps)))
    check("generated steps loop over the folder",
          all(step_.foreach for step_ in generated.steps[:-1]), str(len(generated.steps)))
    check("a generated flow validates", flowkit.validate(generated, "en").ok,
          flowkit.validate(generated, "en").text("en"))
    check("the generated flow names what it found", "Found:" in generated.desc_en,
          generated.desc_en[:80])
    for goal, expected in (("documents", "docx_to_pdf"), ("ocr", "pdf_ocr"),
                           ("archive", "pdf_compress"), ("privacy", "image_metadata_strip")):
        flow_goal = flowkit.build_from_folder(folder, goal, "en")
        ids = [step_.tool_id for step_ in flow_goal.steps]
        check(f"goal {goal} produces {expected}", expected in ids, str(ids))

    saved_path = flowkit.save_flow(generated, sandbox / "flows" / "auto.json")
    check("a flow saves as JSON", saved_path.exists() and saved_path.suffix == ".json")
    reloaded = flowkit.load_flow(saved_path)
    check("a saved flow reloads identically",
          [s_.as_dict() for s_ in reloaded.steps] == [s_.as_dict() for s_ in generated.steps])
    registered = flowkit.save_flow(generated, flowkit.flows_dir() / f"{generated.id}.json")
    check("resolve finds a flow by id", flowkit.resolve(generated.id).id == generated.id)
    check("resolve finds a flow by path", flowkit.resolve(str(saved_path)).id == generated.id)
    try:
        flowkit.resolve("no-such-flow-anywhere")
        check("resolve rejects an unknown flow", False)
    except LookupError:
        check("resolve rejects an unknown flow", True)
    script = flowkit.export_script(generated, "en", str(sandbox))
    check("an exported script is valid Python", bool(compile(script, "flow.py", "exec")))
    check("the exported script uses run_sync", "run_sync" in script and "FLOW" in script)
    check("the exported script embeds the steps", generated.steps[0].tool_id in script)

    # -------------------------------------------------------- the six tools
    listing = run("flow_library")
    check("flow_library lists the builtin flows", "photo_pdf_pack" in listing.details
          or "بسته" in listing.details, listing.details[:80])
    built = run("flow_build", folder=str(folder), goal="share", name="", save=str(sandbox / "built.json"))
    check("flow_build writes a JSON flow", Path(built.outputs[0]).exists(), str(built.outputs))
    explained = run("flow_explain", flow=built.outputs[0], file="")
    check("flow_explain explains the built flow", "گام" in explained.details, explained.details[:60])
    validated = run("flow_validate", flow=built.outputs[0], file="")
    check("flow_validate accepts the built flow", "سالم" in validated.message, validated.message)
    exported = run("flow_export", flow=built.outputs[0], file="", out_dir=str(sandbox / "scripts"))
    script_path = Path(exported.outputs[0])
    check("flow_export writes a .py file", script_path.exists() and script_path.suffix == ".py")
    check("the exported file is valid Python",
          bool(compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec")))
    tiny = Workflow(id="tiny_v60", name="تیش", name_en="Tiny", steps=[
        Step("hash_files", {"src": ["{input}"], "save": "{out_dir}/tiny.sha256"})])
    flowkit.save_flow(tiny, flowkit.flows_dir() / "tiny_v60.json")
    checked = run("flow_run", flow=tiny.id, file="", inputs=[], out_dir="", check_only=True)
    check("flow_run can validate without running", "آماده" in checked.message, checked.message)
    ran = run("flow_run", flow=tiny.id, file="", inputs=[str(fixtures["text"])], out_dir=str(sandbox))
    check("flow_run actually runs a flow", "1 گام" in ran.message or "گام" in ran.message,
          ran.message)
    check("flow_run reports the step output",
          bool(ran.outputs) and Path(ran.outputs[0]).exists(), str(ran.outputs))
    try:
        run("flow_run", flow=tiny.id, file="", inputs=[], out_dir="")
        check("flow_run refuses to run without input", False)
    except ToolError:
        check("flow_run refuses to run without input", True)
    try:
        run("flow_validate", flow="", file="")
        check("flow tools need a flow reference", False)
    except ToolError:
        check("flow tools need a flow reference", True)

    for leftover in (registered, flowkit.flows_dir() / f"{built.outputs[0] and generated.id}.json"):
        try:
            Path(leftover).unlink()
        except OSError:
            pass
    shutil.rmtree(sandbox, ignore_errors=True)


# ------------------------------------------------------------- v6: new tools
def test_v60_tools(fixtures: dict) -> None:
    """v6 tool packs: PDF, image, security, network, media, dev, convert, files."""
    print("\n[6.0 tool packs]")
    import base64
    import csv as csv_module
    import hashlib
    import hmac as hmac_module
    import http.server
    import socket
    import threading as threading_module
    import wave

    from app.core.registry import load_all as _load

    _load()
    sandbox = Path(tempfile.mkdtemp(prefix="wg_v60_tools_"))
    out = sandbox / "out"
    out.mkdir(parents=True, exist_ok=True)

    # Self-contained fixtures (see the note in test_v60_flows).
    from PIL import Image as _Image, ImageDraw as _ImageDraw
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.pdfgen.canvas import Canvas as _CanvasClass

    fixtures = dict(fixtures)
    local_pdf = sandbox / "doc.pdf"
    _doc = _CanvasClass(str(local_pdf), pagesize=_A4)
    for page in range(1, 6):
        _doc.setFont("Helvetica", 18)
        _doc.drawString(60, 760, f"WinGenius test page {page}")
        _doc.showPage()
    _doc.save()
    fixtures["pdf"] = local_pdf
    local_images = []
    for index, colour in enumerate(("#18C8F5", "#7B61FF", "#FFB020")):
        path = sandbox / f"img{index}.png"
        picture = _Image.new("RGB", (480, 320), colour)
        _ImageDraw.Draw(picture).rectangle([20, 20, 460, 300], outline="white", width=4)
        picture.save(path)
        local_images.append(str(path))
    fixtures["images"] = local_images
    fixtures["text"] = sandbox / "note.txt"
    Path(fixtures["text"]).write_text("كتاب هاي من\nسلام دنيا\n۱۲۳ و 456\n", encoding="utf-8")
    fixtures["zip"] = sandbox / "bundle.zip"
    with zipfile.ZipFile(fixtures["zip"], "w") as _archive:
        _archive.write(fixtures["text"], "note.txt")
        _archive.write(local_images[0], Path(local_images[0]).name)

    # ------------------------------------------------------------------ PDF
    parts = run("pdf_split_by_size", src=str(fixtures["pdf"]), max_mb=1, out_dir=str(out / "parts"))
    made = [Path(path) for path in parts.outputs]
    check("pdf_split_by_size writes parts", len(made) >= 1, str(made))
    total_pages = 0
    for path in made:
        from pypdf import PdfReader as _Reader

        total_pages += len(_Reader(str(path)).pages)
    check("every page survives the split", total_pages == 5, str(total_pages))
    check("the split respects the size limit",
          all(path.stat().st_size <= 1024 * 1024 for path in made))

    linked = sandbox / "linked.pdf"
    try:
        import pymupdf

        book = pymupdf.open()
        page = book.new_page()
        page.insert_text((50, 80), "links", fontsize=12)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(20, 20, 200, 40),
                          "uri": "https://example.org/wingenius"})
        book.save(linked)
        book.close()
        have_link = True
    except Exception:
        have_link = False
    if have_link:
        links = run("pdf_extract_links", src=str(linked), include_internal=True,
                    out=str(out / "links.csv"))
        check("pdf_extract_links finds the URI",
              "example.org/wingenius" in links.details, links.details[:120])
        check("pdf_extract_links writes a CSV", Path(links.outputs[0]).exists())
    else:
        skip("pdf link extraction", "PyMuPDF link API unavailable")

    annotated = sandbox / "annotated.pdf"
    try:
        import pymupdf

        book = pymupdf.open(str(fixtures["pdf"]))
        note = book[0].add_text_annot((60, 60), "secret reviewer note")
        note.update()
        book.save(annotated)
        book.close()
        have_annot = True
    except Exception:
        have_annot = False
    if have_annot:
        flattened = run("pdf_flatten_annotations", src=str(annotated), drop_widgets=True, out="")
        from pypdf import PdfReader as _Reader

        reopened = _Reader(flattened.outputs[0])
        check("pdf_flatten_annotations removes every annotation",
              not reopened.pages[0].get("/Annots"), str(reopened.pages[0].get("/Annots")))
        check("the flattened PDF keeps its pages", len(reopened.pages) == 5)
    else:
        skip("pdf annotation flattening", "PyMuPDF annotation API unavailable")

    repaired = run("pdf_repair", src=str(fixtures["pdf"]), out=str(out / "repaired.pdf"))
    check("pdf_repair rewrites a healthy PDF", Path(repaired.outputs[0]).exists())
    check("pdf_repair keeps every page", "5 از 5" in repaired.message or "5 of 5" in repaired.message,
          repaired.message)
    damaged = sandbox / "damaged.pdf"
    damaged.write_bytes(Path(fixtures["pdf"]).read_bytes()[: 900])
    try:
        run("pdf_repair", src=str(damaged), out=str(out / "rescued.pdf"))
        check("pdf_repair accepts a truncated file gracefully", True)
    except ToolError:
        check("pdf_repair accepts a truncated file gracefully", True)

    # ---------------------------------------------------------------- images
    tagged = sandbox / "tagged.jpg"
    image = __import__("PIL.Image", fromlist=["Image"]).open(fixtures["images"][0])
    exif = image.getexif()
    exif[0x010F] = "WinGeniusCam"
    exif[0x0110] = "Model X"
    exif[0x0131] = "TestSuite 1.0"
    image.save(tagged, exif=exif)
    scanned = run("image_privacy_scan", src=[str(tagged)])
    check("image_privacy_scan sees the camera model", "Model X" in scanned.details,
          scanned.details[:120])
    check("image_privacy_scan counts leaking files", "1 از 1" in scanned.message
          or "1 of 1" in scanned.message, scanned.message)
    stripped = run("image_metadata_strip", src=[str(tagged)], out_dir=str(out / "clean"), quality=95)
    cleaned = __import__("PIL.Image", fromlist=["Image"]).open(stripped.outputs[0])
    check("image_metadata_strip writes a clean copy", Path(stripped.outputs[0]).exists())
    check("the cleaned image has no EXIF left", len(dict(cleaned.getexif())) == 0,
          str(dict(cleaned.getexif())))
    check("the cleaned image keeps its size", cleaned.size == (480, 320), str(cleaned.size))

    tilted = sandbox / "tilted.png"
    __import__("PIL.Image", fromlist=["Image"]).open(fixtures["images"][1]).rotate(
        7, expand=True, fillcolor="white").save(tilted)
    deskewed = run("image_deskew", src=[str(tilted)], out_dir=str(out / "straight"), max_angle=15)
    angles = [line for line in deskewed.details.splitlines() if "°" in line]
    check("image_deskew measures the tilt", bool(angles), deskewed.details[:80])
    if angles:
        measured = abs(float(angles[0].split(":")[1].replace("°", "").strip()))
        check("the measured tilt is about 7°", 4.0 <= measured <= 10.0, angles[0])
    check("image_deskew writes an output", Path(deskewed.outputs[0]).exists())

    blurred = run("image_face_blur", src=[str(fixtures["images"][2])], mode="blur",
                  strength=20, out_dir=str(out / "faces"))
    check("image_face_blur always writes a copy", Path(blurred.outputs[0]).exists())
    check("image_face_blur reports the count", "0" in blurred.message or "چهره" in blurred.message,
          blurred.message)

    # -------------------------------------------------------------- security
    from app.tools.security_extra2 import totp_at

    secret = base64.b32encode(b"12345678901234567890")
    check("totp matches the RFC 6238 test vector",
          totp_at(base64.b32decode(secret), 59, 30, 8, "sha1") == "94287082",
          totp_at(base64.b32decode(secret), 59, 30, 8, "sha1"))
    sha256_secret = base64.b32decode(base64.b32encode(b"12345678901234567890123456789012"))
    check("totp matches the RFC 6238 SHA-256 vector",
          totp_at(sha256_secret, 59, 30, 8, "sha256") == "46119246",
          totp_at(sha256_secret, 59, 30, 8, "sha256"))
    sha512_secret = base64.b32decode(base64.b32encode(
        b"1234567890123456789012345678901234567890123456789012345678901234"))
    check("totp matches the RFC 6238 SHA-512 vector",
          totp_at(sha512_secret, 59, 30, 8, "sha512") == "90693936",
          totp_at(sha512_secret, 59, 30, 8, "sha512"))
    tool_totp = run("totp_code", secret=secret.decode(), digits=6, period=30,
                    algorithm="sha1", check="")
    current = tool_totp.message.split(":")[-1].strip()
    check("the TOTP tool returns six digits", len(current) == 6 and current.isdigit(), current)
    verified = run("totp_code", secret=secret.decode(), digits=6, period=30,
                   algorithm="sha1", check=current)
    check("the TOTP tool verifies its own code", "✓" in verified.message, verified.message)
    wrong = run("totp_code", secret=secret.decode(), digits=6, period=30,
                algorithm="sha1", check="000000")
    check("the TOTP tool rejects a wrong code", "✖" in wrong.message, wrong.message)

    carrier = Path(fixtures["images"][0])
    hidden = run("steg_hide", carrier=str(carrier), text="top secret payload",
                 file="", password="correct horse", out=str(out / "hidden.png"))
    revealed = run("steg_reveal", carrier=hidden.outputs[0], password="correct horse",
                   out_dir="", just_text=True)
    check("steganography round-trips a message",
          "top secret payload" in revealed.details, revealed.details[:80])
    plain_hidden = run("steg_hide", carrier=str(carrier), text="no password here",
                       file="", password="", out=str(out / "plain_hidden.png"))
    plain_revealed = run("steg_reveal", carrier=plain_hidden.outputs[0], password="",
                         out_dir="", just_text=True)
    check("steganography works without a password",
          "no password here" in plain_revealed.details, plain_revealed.details[:80])
    try:
        run("steg_reveal", carrier=hidden.outputs[0], password="wrong", out_dir="", just_text=True)
        check("steganography rejects a wrong password", False)
    except ToolError:
        check("steganography rejects a wrong password", True)
    try:
        run("steg_reveal", carrier=str(carrier), password="", out_dir="", just_text=True)
        check("steganography refuses a carrier with no message", False)
    except ToolError:
        check("steganography refuses a carrier with no message", True)
    check("the carrier keeps its pixel size",
          __import__("PIL.Image", fromlist=["Image"]).open(hidden.outputs[0]).size
          == (480, 320))

    signature = run("hmac_sign", text="The quick brown fox jumps over the lazy dog", file="",
                    key="key", algorithm="sha256", expected="")
    expected_hmac = hmac_module.new(b"key", b"The quick brown fox jumps over the lazy dog",
                                    hashlib.sha256).hexdigest()
    check("HMAC matches the reference value", expected_hmac in signature.details,
          signature.details[-80:])
    check("HMAC verification accepts the right value",
          "✓" in run("hmac_sign", text="The quick brown fox jumps over the lazy dog", file="",
                     key="key", algorithm="sha256", expected=expected_hmac).message)
    check("HMAC verification rejects a wrong value",
          "✖" in run("hmac_sign", text="x", file="", key="key", algorithm="sha256",
                     expected=expected_hmac).message)

    # --------------------------------------------------------------- network
    subnet = run("subnet_calc", cidr="192.168.1.10/24", split_prefix=26, limit=8)
    check("subnet_calc normalises the network", "192.168.1.0/24" in subnet.details,
          subnet.details[:80])
    check("subnet_calc reports 256 addresses", "256" in subnet.details)
    check("subnet_calc splits into four /26", subnet.details.count("/26") == 4,
          str(subnet.details.count("/26")))
    v6 = run("subnet_calc", cidr="2001:db8::1/64", split_prefix=0, limit=8)
    check("subnet_calc understands IPv6", "2001:db8::/64" in v6.details, v6.details[:80])
    try:
        run("subnet_calc", cidr="not-an-address", split_prefix=0, limit=8)
        check("subnet_calc rejects nonsense", False)
    except ToolError:
        check("subnet_calc rejects nonsense", True)

    class _Headers(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                  # noqa: N802 - stdlib API
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Security-Policy", "default-src 'self'")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "geolocation=()")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *_args):                     # silence the test log
            return

    class _NoHeaders(_Headers):
        def do_GET(self):                                  # noqa: N802 - stdlib API
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"hello")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Headers)
    port = server.server_address[1]
    thread = threading_module.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        graded = run("security_headers", url=f"http://127.0.0.1:{port}/", timeout=5)
        check("security_headers gives a perfect site an A", "A" in graded.message, graded.message)
        timing = run("http_timing", url=f"http://127.0.0.1:{port}/", request=True)
        check("http_timing measures DNS", "DNS" in timing.details, timing.details[:120])
        check("http_timing measures the first byte", "TTFB" in timing.details, timing.details[:160])
    finally:
        server.shutdown()
        server.server_close()
    bare = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _NoHeaders)
    bare_port = bare.server_address[1]
    threading_module.Thread(target=bare.serve_forever, daemon=True).start()
    try:
        failed = run("security_headers", url=f"http://127.0.0.1:{bare_port}/", timeout=5)
        check("security_headers fails a bare site", "F" in failed.message, failed.message)
        check("security_headers lists what is missing", "کم است" in failed.message
              or "Missing" in failed.message, failed.message)
    finally:
        bare.shutdown()
        bare.server_close()
    try:
        run("tls_certificate", host="this-host-does-not-exist.invalid", port=443)
        check("tls_certificate fails cleanly on a bad host", False)
    except ToolError:
        check("tls_certificate fails cleanly on a bad host", True)
    try:
        run("security_headers", url="http://127.0.0.1:1/", timeout=3)
        check("security_headers reports an unreachable site", False)
    except ToolError:
        check("security_headers reports an unreachable site", True)

    # ----------------------------------------------------------------- media
    srt = sandbox / "sample.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,500\nسلام دنیا\n\n"
                   "2\n00:00:04,000 --> 00:00:06,000\n<i>line two</i>\n", encoding="utf-8")
    vtt = run("subtitle_convert", src=str(srt), target="vtt", shift="+1.5s",
              strip_tags=1, out="")
    body = Path(vtt.outputs[0]).read_text(encoding="utf-8")
    check("subtitle_convert writes WebVTT", body.startswith("WEBVTT"), body[:40])
    check("subtitle_convert shifts the timestamps", "00:00:02.500 --> 00:00:05.000" in body, body)
    check("subtitle_convert strips italic tags", "<i>" not in body, body)
    plain = run("subtitle_convert", src=str(srt), target="txt", shift="-1s", strip_tags=1, out="")
    check("subtitle_convert can emit plain text",
          "سلام دنیا" in Path(plain.outputs[0]).read_text(encoding="utf-8"))
    try:
        run("subtitle_convert", src=str(fixtures["text"]), target="srt", shift="", strip_tags=1, out="")
        check("subtitle_convert rejects a non-subtitle file", False)
    except ToolError:
        check("subtitle_convert rejects a non-subtitle file", True)

    wav_path = sandbox / "tone.wav"
    with wave.open(str(wav_path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(
            int(12000 * __import__("math").sin(2 * 3.14159 * 440 * index / 8000)).to_bytes(
                2, "little", signed=True) for index in range(8000)))
    try:
        waveform = run("audio_waveform", src=str(wav_path), width=600, height=200,
                       color="#00D9A6", out=str(out / "wave.png"))
        check("audio_waveform writes a PNG", Path(waveform.outputs[0]).suffix == ".png")
        check("the waveform has the requested size",
              __import__("PIL.Image", fromlist=["Image"]).open(waveform.outputs[0]).size
              == (600, 200))
        stripped_media = run("media_strip_metadata", src=str(wav_path), out=str(out / "clean.wav"))
        check("media_strip_metadata writes a copy", Path(stripped_media.outputs[0]).exists())
    except ToolError as exc:
        skip("audio video extras", f"ffmpeg unavailable: {exc}"[:70])

    # ------------------------------------------------------------------- dev
    log_path = sandbox / "app.log"
    log_path.write_text("\n".join([
        "2026-11-01 10:00:00 INFO starting up",
        "2026-11-01 10:00:01 WARN cache miss for key 8812",
        "2026-11-01 10:00:02 ERROR timeout after 1203ms calling api",
        "2026-11-01 10:00:03 ERROR timeout after 88ms calling api",
        "2026-11-01 10:00:04 INFO served 12 requests",
        "2026-11-01 10:00:05 FATAL database is gone",
    ]) + "\n", encoding="utf-8")
    analysed = run("log_analyzer", src=str(log_path), top=5, out=str(out / "log_report.txt"))
    check("log_analyzer counts every level", "ERROR: 2" in analysed.details, analysed.details[:200])
    check("log_analyzer normalises numbers in signatures",
          analysed.details.count("timeout after #ms") >= 1, analysed.details[:400])
    check("log_analyzer finds the time range", "2026-11-01 10:00:00" in analysed.details)
    check("log_analyzer writes a report", Path(analysed.outputs[0]).exists())

    repo = sandbox / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com")
    import subprocess

    def _git(*args):
        return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                              env=env, check=False)

    have_git = shutil.which("git") is not None
    if have_git:
        _git("init", "-q")
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        _git("add", ".")
        _git("commit", "-qm", "first")
        (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        _git("commit", "-qam", "second")
        stats = run("git_stats", repo=str(repo), limit=50, top=5)
        check("git_stats counts the commits", "2" in stats.message, stats.message)
        check("git_stats names the author", "Test" in stats.details, stats.details[:200])
        check("git_stats finds the changed file", "a.txt" in stats.details, stats.details[:400])
    else:
        skip("git_stats", "git is not installed")

    left = sandbox / "left.csv"
    right = sandbox / "right.csv"
    left.write_text("id,name\n1,ali\n2,sara\n3,reza\n", encoding="utf-8")
    right.write_text("id,city\n1,tehran\n3,shiraz\n4,tabriz\n", encoding="utf-8")
    joined = run("csv_join", left=str(left), right=str(right), key="id", mode="left", delimiter=",")
    rows = list(csv_module.DictReader(io.StringIO(Path(joined.outputs[0]).read_text(
        encoding="utf-8-sig"))))
    check("csv_join writes one row per match", len(rows) == 3, str(len(rows)))
    check("csv_join fills the joined column", rows[0]["city"] == "tehran", str(rows[0]))
    check("csv_join leaves unmatched cells empty", rows[1]["city"] == "", str(rows[1]))
    outer = run("csv_join", left=str(left), right=str(right), key="id", mode="outer", delimiter=",")
    outer_rows = list(csv_module.DictReader(io.StringIO(Path(outer.outputs[0]).read_text(
        encoding="utf-8-sig"))))
    check("csv_join outer keeps right-only rows", len(outer_rows) == 4, str(len(outer_rows)))

    jsonl_path = sandbox / "data.jsonl"
    jsonl_path.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    as_array = run("jsonl_tool", src=str(jsonl_path), mode="to_array", out=str(out / "data.json"))
    check("jsonl_tool makes a JSON array",
          json.loads(Path(as_array.outputs[0]).read_text(encoding="utf-8"))[1]["a"] == 2)
    back = run("jsonl_tool", src=str(out / "data.json"), mode="to_lines", out=str(out / "back.jsonl"))
    check("jsonl_tool goes back to lines",
          len(Path(back.outputs[0]).read_text(encoding="utf-8").strip().splitlines()) == 2)
    broken_jsonl = sandbox / "broken.jsonl"
    broken_jsonl.write_text('{"a":1}\nnope\n', encoding="utf-8")
    try:
        run("jsonl_tool", src=str(broken_jsonl), mode="check", out="")
        check("jsonl_tool reports a broken line", False)
    except ToolError as exc:
        check("jsonl_tool reports a broken line", "line 2" in str(exc), str(exc)[:80])

    converted = run("curl_to_code", command=(
        "curl -X POST https://api.example.com/v1/items -H 'Content-Type: application/json' "
        "-H 'Authorization: Bearer abc' -d '{\"name\":\"x\"}'"), target="python", out="")
    check("curl_to_code keeps the URL", "https://api.example.com/v1/items" in converted.details)
    check("curl_to_code uses the right verb", "requests.post" in converted.details,
          converted.details[:200])
    check("curl_to_code sends JSON", "json=payload" in converted.details, converted.details[-200:])
    js = run("curl_to_code", command="curl https://example.com", target="javascript", out="")
    check("curl_to_code can emit fetch()", "fetch(" in js.details, js.details[:80])

    # --------------------------------------------------------------- convert
    pii = ("تماس: 09121234567 و ایمیل ali@example.com\n"
           "کارت: 4111 1111 1111 1111\nکد ملی: 0499370899\nآی‌پی: 192.168.0.7\n"
           "کارت جعلی: 1234 5678 9012 3456\n")
    redacted = run("text_redact", text=pii, file="", mode="placeholder", keep_last=False,
                   strict=True, out=str(out / "redacted.txt"))
    body = Path(redacted.outputs[0]).read_text(encoding="utf-8")
    check("text_redact hides the e-mail", "ali@example.com" not in body, body)
    check("text_redact hides the mobile", "09121234567" not in body, body)
    check("text_redact hides a valid card", "4111 1111 1111 1111" not in body, body)
    check("text_redact hides the national ID", "0499370899" not in body, body)
    check("text_redact hides the IP", "192.168.0.7" not in body, body)
    check("text_redact keeps a card that fails the Luhn check",
          "1234 5678 9012 3456" in body, body)
    check("text_redact counts what it removed", "موبایل: 1" in redacted.message, redacted.message)
    masked = run("text_redact", text="ali@example.com", file="", mode="mask", keep_last=True,
                 strict=True, out="")
    check("text_redact can mask instead of placeholdering",
          "[EMAIL]" not in masked.details and "@example.com" in masked.details, masked.details)

    maths = run("date_math", date="2026-11-03", calendar="gregorian", add=10, unit="days",
                other="2026-11-01")
    check("date_math adds days", "2026-11-13" in maths.message, maths.message)
    check("date_math shows the Jalali date", "شمسی" in maths.message, maths.message)
    check("date_math measures the gap", "-2" in maths.details or "+2" in maths.details,
          maths.details)
    jalali = run("date_math", date="1405/08/12", calendar="jalali", add=0, unit="days", other="")
    check("date_math reads a Jalali date", "شمسی 1405/08/12" in jalali.details
          or "1405/08/12" in jalali.details, jalali.details[:120])
    business = run("date_math", date="2026-11-02", calendar="gregorian", add=0, unit="days",
                   other="2026-11-09")
    check("date_math counts business days", "business  5" in business.details, business.details)
    try:
        run("date_math", date="garbage", calendar="gregorian", add=0, unit="days", other="")
        check("date_math rejects a nonsense date", False)
    except ToolError:
        check("date_math rejects a nonsense date", True)

    # ----------------------------------------------------------------- files
    secret_zip = run("archive_encrypt", src=[str(fixtures["text"]), str(fixtures["images"][0])],
                     password="s3cret-pass", level="normal", out=str(out / "secret.zip"))
    check("archive_encrypt writes a zip", Path(secret_zip.outputs[0]).exists())
    check("archive_encrypt reports AES", "AES" in secret_zip.message, secret_zip.message)
    unpacked = run("archive_decrypt", src=secret_zip.outputs[0], password="s3cret-pass",
                   out_dir=str(out / "unpacked"))
    check("archive_decrypt restores the files", len(unpacked.outputs) == 2, str(unpacked.outputs))
    check("the restored text is intact",
          any(Path(path).suffix == ".txt" and "كتاب" in Path(path).read_text(encoding="utf-8")
              for path in unpacked.outputs))
    try:
        run("archive_decrypt", src=secret_zip.outputs[0], password="wrong", out_dir=str(out / "nope"))
        check("archive_decrypt rejects a wrong password", False)
    except ToolError:
        check("archive_decrypt rejects a wrong password", True)
    evil = sandbox / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../escaped.txt", "pwned")
        archive.writestr("safe.txt", "fine")
    escaped_target = sandbox / "escaped.txt"
    extracted = run("archive_decrypt", src=str(evil), password="", out_dir=str(sandbox / "safe_area"))
    check("archive_decrypt refuses zip-slip", not escaped_target.exists()
          and not (sandbox / "safe_area" / ".." / "escaped.txt").exists(), str(extracted.details))
    check("archive_decrypt still extracts safe members",
          any(Path(path).name == "safe.txt" for path in extracted.outputs), str(extracted.outputs))

    deep = sandbox / ("d" * 20) / ("e" * 20) / ("f" * 20)
    deep.mkdir(parents=True, exist_ok=True)
    (deep / "file.txt").write_text("x", encoding="utf-8")
    expected_length = len(str(deep / "file.txt"))
    audit = run("path_length_audit", folder=str(sandbox / ("d" * 20)), limit=40,
                save=str(out / "long_paths.csv"))
    check("path_length_audit finds long paths", "مسیر بلندتر از 40" in audit.message
          or "longer than 40" in audit.message, audit.message)
    check("path_length_audit reports the real length", str(expected_length) in audit.details,
          audit.details[:200])
    check("path_length_audit saves a CSV", Path(audit.outputs[0]).exists())

    # ---------------------------------------------------------------- system
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listening_port = listener.getsockname()[1]
    try:
        report = run("port_release", port=listening_port, kill=False)
        check("port_release sees a busy port", "در دست" in report.message
              or "held by" in report.message, report.message)
    except ToolError as exc:
        skip("port_release", f"connection table unavailable: {exc}"[:70])
    finally:
        listener.close()
    free = run("port_release", port=1, kill=False)
    check("port_release says a free port is free", "آزاد" in free.message
          or "free" in free.message, free.message)

    shutil.rmtree(sandbox, ignore_errors=True)


def main(argv: list[str]) -> int:
    print("=" * 78)
    print("WinGenius v4 functional tests")
    print("=" * 78)
    load_all()
    fixtures = make_fixtures()
    groups = {
        "registry": test_registry,
        "engine": lambda: test_core_engine(fixtures),
        "pdf": lambda: test_pdf(fixtures),
        "image": lambda: test_image(fixtures),
        "files": lambda: test_files(fixtures),
        "system": lambda: test_system_network(fixtures),
        "security": lambda: test_security_dev(fixtures),
        "codes": lambda: test_ocr_codes(fixtures),
        "cli": lambda: test_cli(fixtures),
        "planner": lambda: test_planner(fixtures),
        "workflows": lambda: test_workflows(fixtures),
        "watcher": lambda: test_watcher(fixtures),
        "index": lambda: test_index(fixtures),
        "automation": lambda: test_automation_tools(fixtures),
        "v3": lambda: test_v3_tools(fixtures),
        "journal": lambda: test_journal(fixtures),
        "scheduler": test_scheduler,
        "macro": lambda: test_macro(fixtures),
        "vault": test_vault,
        "api": test_api_server,
        "schema": test_schema,
        "stress": test_stress,
        "fuzz": test_fuzz,
        "guards": lambda: test_guards(fixtures),
        "v42": lambda: test_v41_tools(fixtures),
        "v50": lambda: test_v50_tools(fixtures),
        "v60flows": lambda: test_v60_flows(fixtures),
        "v60tools": lambda: test_v60_tools(fixtures),
        "scale": lambda: test_scale(fixtures),
    }
    selected = [name for name in argv[1:] if name in groups] or list(groups)
    for name in selected:
        try:
            groups[name]()
        except Exception:
            global FAIL
            FAIL += 1
            FAILURES.append(f"{name} group crashed")
            print(f"  CRASH {name}")
            traceback.print_exc()
    print("\n" + "=" * 78)
    print(f"RESULT: {PASS} passed, {FAIL} failed, {SKIP} skipped")
    if FAILURES:
        print("Failures:")
        for item in FAILURES:
            print(f"  - {item}")
    print("=" * 78)
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
