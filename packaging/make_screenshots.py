"""Regenerate every docs/screenshots/*.png from the real app (headless).

    QT_QPA_PLATFORM=offscreen python packaging/make_screenshots.py
"""
import os, sys, tempfile as _tempfile, time
from pathlib import Path as _Path
# every run starts from a clean profile so the shots are reproducible
os.environ.setdefault("XDG_CONFIG_HOME", _tempfile.mkdtemp(prefix="wg-shots-cfg-"))
ROOT = _Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from PySide6.QtCore import QEventLoop, QTimer

from app.bootstrap import create_app
from app.core.registry import load_all
from app.core.settings import settings
from app.core import indexer, history
from app.core.watcher import store as watch_store
from app.ui.main_window import MainWindow

OUT = ROOT / 'docs' / 'screenshots'
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(os.environ.get('WG_SHOT_TMP') or _tempfile.mkdtemp(prefix='shot_fixtures_'))
TMP.mkdir(parents=True, exist_ok=True)
# Tools that build their output path from a relative default would otherwise drop
# page_01.png / *.zip files straight into the repo root — run everything inside TMP.
os.chdir(TMP)

# ------------------------------------------------------------------ fixtures
from reportlab.pdfgen import canvas as _canvas
from PIL import Image, ImageDraw

pdf = TMP / 'report.pdf'
c = _canvas.Canvas(str(pdf))
for page in range(1, 4):
    c.setFont("Helvetica-Bold", 22)
    c.drawString(60, 760, f"WinGenius report — page {page}")
    c.setFont("Helvetica", 12)
    for line in range(8):
        c.drawString(60, 720 - line * 22, f"line {line + 1}: total = {page * 100 + line}, note = report")
    c.showPage()
c.save()

png = TMP / 'photo.png'
im = Image.new("RGB", (900, 600), "#18C8F5")
d = ImageDraw.Draw(im)
d.rectangle([40, 40, 860, 560], outline="white", width=6)
for i in range(6):
    d.ellipse([80 + i * 130, 180, 200 + i * 130, 300], fill="#5B8CFF")
im.save(png)

img_dir = TMP / 'photos'; img_dir.mkdir(exist_ok=True)
for i in range(4):
    Image.new("RGB", (600, 400), ("#FF5A6E", "#2BD9A0", "#FFC24B", "#8B5CFF")[i]).save(img_dir / f"shot_{i}.png")

idx_docs = TMP / 'docs'; idx_docs.mkdir(exist_ok=True)
(idx_docs / 'notes.txt').write_text(
    "WinGenius search index demo.\nنتیجه‌ی نهایی گزارش مالی سه‌ماهه آماده است.\n"
    "invoice total 1240 EUR\n", encoding="utf-8")
(idx_docs / 'todo.md').write_text("# TODO\n- ship v3\n- write the release notes\n", encoding="utf-8")

# ------------------------------------------------------------------- helpers
def spin(ms: int) -> None:
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


app = create_app([])
load_all()
settings.lang = 'fa'; settings.theme = 'aurora'; settings.accent = '#5B8CFF'
settings.output_dir = str(TMP / 'out'); (TMP / 'out').mkdir(exist_ok=True)
settings.save() if hasattr(settings, 'save') else None

w = MainWindow(); w.resize(1000, 920); w.show(); spin(700)


def shot(name: str, wait: int = 800) -> None:
    spin(wait)
    toast = getattr(w, "toast", None)
    if toast is not None:
        toast.hide()
    w.repaint(); spin(120); w.repaint()
    w.grab().save(str(OUT / name))
    print('saved', name)


# ------------------------------------------------------------------ 1..3 core
w.show_home(); shot('home_fa.png', 1100)
w.open_category('pdf'); shot('pdf_fa.png', 950)
w._open_tool_by_id('pdf_to_images'); shot('tool_fa.png', 800)
w._close_overlay(); spin(250)

# 4 assistant palette + commands
w.open_search(); w.quick_search._input.setText('فشرده کردن عکس'); shot('assistant_fa.png', 700)
w.quick_search._input.setText(''); w.quick_search._refresh(); spin(300)
w.quick_search._list.scrollToBottom(); shot('palette_commands.png', 500)
w.quick_search.close_palette(); spin(250)

# 5 smart drop
w._show_drop([str(pdf), str(png)]); shot('drop_zone.png', 700)
w._close_overlay(); spin(250)

# 6 job queue with real jobs
j1 = w.jobs.submit_by_id('pdf_to_text', {'src': str(pdf), 'out': str(TMP / 'text.txt')})
j2 = w.jobs.submit_by_id('image_palette', {'src': str(png), 'count': 6})
j3 = w.jobs.submit_by_id('pdf_find_text', {'src': str(pdf), 'query': 'report', 'ignore_case': True})
print('jobs submitted', j1 is not None, j2 is not None, j3 is not None)
deadline = time.time() + 40
while time.time() < deadline and w.jobs.has_active():
    spin(120)
print('stats', w.jobs.stats())
w.open_jobs(); shot('job_queue.png', 900)
w._close_overlay(); spin(250)

# 7 result card of a real run
w._open_tool_by_id('pdf_to_images', values={'src': str(pdf), 'fmt': 'png', 'dpi': 150})
spin(400)
w.tool_panel._run()
spin(2500)
shot('result_fa.png', 700)
w._close_overlay(); spin(250)

# 8 developer category + a Persian tool
w.open_category('dev'); shot('dev_fa.png', 900)
w._open_tool_by_id('jalali_date'); shot('persian_tool.png', 800)
w._close_overlay(); spin(250)

# 9 settings (main + system section)
w.open_settings(); shot('settings_fa.png', 900)
w.settings_panel.scroll_to_system(); shot('settings_system.png', 600)
w._close_overlay(); spin(250)

# 10 English + light theme
settings.lang = 'en'; w._on_language_changed('en')
w.show_home(); shot('home_en.png', 900)
w.open_search(); shot('palette_en.png', 700)
w.quick_search.close_palette(); spin(250)
settings.theme = 'daylight'
w._apply_theme()
w.show_home(); shot('home_light.png', 900)
w._open_tool_by_id('pdf_to_images'); shot('tool_light.png', 800)
w._close_overlay(); spin(250)
settings.theme = 'midnight'
w._apply_theme()
w.open_category('convert'); shot('convert_midnight.png', 900)

# 11 back to Persian + aurora
settings.theme = 'aurora'; settings.lang = 'fa'
w._apply_theme()
w._on_language_changed('fa')
w.show_home(); spin(400)

# 12 v3 sheets
indexer.clear()
indexer.build([str(TMP)])
w.open_dashboard(); shot('dashboard_fa.png', 900)
w._close_overlay(); spin(250)

w.open_plan([str(pdf), str(png)]); spin(200)
w.plan_panel._input.setText('این عکس‌ها را کم‌حجم کن و PDF بساز')
w.plan_panel.make_plan(); shot('plan_fa.png', 800)
w._close_overlay(); spin(250)

w.open_workflows(); spin(250)
w.workflow_panel.select('pdf_to_images_zip'); shot('workflows_fa.png', 800)
w._close_overlay(); spin(250)

rule = watch_store.new()
rule.name = 'پوشه‌ی اسکنر'
rule.folder = str(TMP)
rule.pattern = '*.pdf'
rule.action = 'workflow'
rule.target_id = 'pdf_to_images_zip'
rule.settle = 2
rule.runs = 12
rule.last_status = 'auto'
watch_store.save(rule)
w.open_automation(); shot('automation_fa.png', 800)
w._close_overlay(); spin(250)
for r in watch_store.rules():
    watch_store.delete(r.id)

# 12b ----------------------------------------------------- v4: scheduler sheet
from app.core.scheduler import store as schedule_store
from app.core.journal import journal
from app.core.vault import Vault
from app.core import macro as macro_module

for old in schedule_store.all():
    schedule_store.delete(old.id)
for name, cron, kind, target, enabled in (
        ('هر ۱۵ دقیقه: بسته‌ی عکس‌ها', '*/15 * * * *', 'workflow', 'photo_pdf_pack', True),
        ('هر شب ۲۳:۰۰: بکاپ تنظیمات', '0 23 * * *', 'tool', 'backup_appdata', True),
        ('شنبه‌ها ۰۸:۰۰: پاک‌سازی دانلود', '0 8 * * 6', 'macro', 'clean_downloads', False)):
    item = schedule_store.new()
    item.name = name
    item.cron = cron
    item.kind = kind
    item.target_id = target
    item.enabled = enabled
    item.last_status = 'done'
    item.runs = 4
    schedule_store.save(item)
for _macro in macro_module.builtin_macros('fa'):
    macro_module.save_macro(_macro)
w.open_scheduler(); shot('scheduler_fa.png', 1000)
_bar = w.scheduler_panel._scroll.verticalScrollBar()
_bar.setValue(_bar.maximum()); shot('macros_fa.png', 700)
w._close_overlay(); spin(250)

# 12c --------------------------------------------------------- v4: undo journal
journal().clear()
rename_dir = TMP / 'undo_demo'; rename_dir.mkdir(exist_ok=True)
for index in range(1, 5):
    (rename_dir / f'invoice_{index}.txt').write_text(f'invoice {index}', encoding='utf-8')
w._open_tool_by_id('batch_rename', values={'folder': str(rename_dir), 'mode': 'template',
                                           'template': 'factor_{n}', 'preview': False})
spin(300)
w.tool_panel._run()
spin(1800)
w._close_overlay(); spin(250)
w.open_dashboard(); shot('undo_fa.png', 1000)
w._close_overlay(); spin(250)

# 12d ------------------------------------------------------------ v4: the vault
vault_path = TMP / 'demo.wgv'
if vault_path.exists():
    vault_path.unlink()
vault = Vault(vault_path, 'demo-pass', iterations=120_000)
vault.create()
secret = TMP / 'contract.txt'
secret.write_text('final contract text', encoding='utf-8')
vault.add([secret])
vault.add_text('passwords.txt', 'mail: demo-pass-1234')
vault.add_text('notes.txt', 'سلام؛ این یادداشت رمز شده است.')
w._open_tool_by_id('vault_list', values={'vault': str(vault_path), 'password': 'demo-pass'})
spin(400)
w.tool_panel._run()
spin(1500)
shot('vault_fa.png', 800)
w._close_overlay(); spin(250)

# 12d2 -------------------------------------------------- v4.2: redaction sheet
redact_src = TMP / 'invoice.pdf'
try:
    import pymupdf as _fitz

    _doc = _fitz.open()
    _page = _doc.new_page()
    _page.insert_text((60, 90), 'WinGenius invoice', fontsize=18)
    _page.insert_text((60, 130), 'Card number: 6037-9975-1234-5678', fontsize=12)
    _page.insert_text((60, 160), 'Password: hunter2-secret', fontsize=12)
    _page.insert_text((60, 190), 'Public line that stays.', fontsize=12)
    _doc.save(redact_src)
    _doc.close()
    w._open_tool_by_id('pdf_redact', values={'src': str(redact_src), 'mode': 'search',
                                             'terms': '6037-9975-1234-5678\nhunter2-secret'})
    spin(400)
    w.tool_panel._run()
    spin(2000)
    shot('redact_fa.png', 800)
    w._close_overlay(); spin(250)
except Exception as _exc:                      # the sheet is cosmetic; never stop the run
    print('redaction screenshot skipped:', _exc)

# 12d2b --------------------------------------------------- v5.0: signature sheet
try:
    import datetime as _dt

    from cryptography import x509 as _x509
    from cryptography.hazmat.primitives import hashes as _hashes, serialization as _ser
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from cryptography.hazmat.primitives.serialization import pkcs12 as _pkcs12
    from cryptography.x509.oid import NameOID as _OID

    _key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _name = _x509.Name([_x509.NameAttribute(_OID.COMMON_NAME, "نمونه امضاکننده")])
    _cert = (_x509.CertificateBuilder().subject_name(_name).issuer_name(_name)
             .public_key(_key.public_key()).serial_number(_x509.random_serial_number())
             .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1))
             .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=365))
             .sign(_key, _hashes.SHA256()))
    _p12 = TMP / 'demo-signer.p12'
    _p12.write_bytes(_pkcs12.serialize_key_and_certificates(
        b'shot-pass', _key, _cert, None, _ser.BestAvailableEncryption(b'shot-pass')))
    w._open_tool_by_id('pdf_sign', values={'src': str(pdf), 'cert': str(_p12),
                                           'password': 'shot-pass', 'reason': 'تأیید نسخه نهایی',
                                           'out': str(TMP / 'signed.pdf')})
    spin(400)
    w.tool_panel._run()
    spin(2500)
    shot('sign_fa.png', 800)
    w._close_overlay(); spin(250)
except Exception as _exc:
    print('signature screenshot skipped:', _exc)

# 12d3 ------------------------------------------------ v4.2: OCR setup + whisper
w._open_tool_by_id('ocr_setup', values={'action': 'status'})
spin(300)
w.tool_panel._run()
spin(1200)
shot('ocr_setup_fa.png', 800)
w._close_overlay(); spin(250)
w._open_tool_by_id('audio_transcribe')
spin(300)
shot('transcribe_fa.png', 800)
w._close_overlay(); spin(250)

# 12e ----------------------------------------------------------- v4: macro engine
for _macro in macro_module.builtin_macros('fa'):
    macro_module.save_macro(_macro)
templates = macro_module.library()
if templates:
    w._open_tool_by_id('macro_run', values={'file': str(templates[0]), 'dry_run': True})
    spin(400)
    w.tool_panel._run()
    spin(2000)
    shot('macro_fa.png', 800)
    w._close_overlay(); spin(250)

# 13 English versions of the new sheets
settings.lang = 'en'
w._on_language_changed('en')
w.open_dashboard(); shot('dashboard_en.png', 900)
w._close_overlay(); spin(250)
w.open_plan([str(pdf), str(png)]); spin(200)
w.plan_panel._input.setText('compress these photos and build a PDF')
w.plan_panel.make_plan(); shot('plan_en.png', 800)
w._close_overlay(); spin(250)
w.open_scheduler(); shot('scheduler_en.png', 900)
w._close_overlay(); spin(250)
settings.lang = 'fa'
w._on_language_changed('fa')
for old in schedule_store.all():
    schedule_store.delete(old.id)
w.show_home(); spin(300)
print('done')

# ------------------------------------------------------------------- tidy up
# Belt and braces: nothing this script produces should ever land in the repo.
for pattern in ('drop_images', 'fresh_images', 'report_images'):
    stray = ROOT / pattern
    if stray.is_dir():
        for child in stray.glob('*'):
            child.unlink(missing_ok=True)
        stray.rmdir()
for pattern in ('drop_page_*.zip', 'fresh_page_*.zip', 'page_*.png', 'page_*.zip'):
    for stray in ROOT.glob(pattern):
        stray.unlink(missing_ok=True)
