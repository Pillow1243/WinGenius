"""Generate docs/showcase.html — one self-contained page (screenshots inlined as base64).

    python packaging/make_showcase.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS = json.loads((ROOT / "docs" / "stats.json").read_text(encoding="utf-8"))
_PERSIAN = str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")


def fa(number: int) -> str:
    """Western digits with a thousands separator -> Persian digits."""
    return f"{number:,}".translate(_PERSIAN)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.paths import APP_VERSION             # noqa: E402
from app.core import scheduler as scheduler_module  # noqa: E402
from app.core import macro as macro_module          # noqa: E402
from app.core.registry import get as get_tool       # noqa: E402
from app.core.registry import CATEGORIES, load_all, tools_in   # noqa: E402
from app.core.workflows import store as workflow_store          # noqa: E402
from app.core import planner                                    # noqa: E402

SHOTS = [
    ("home_fa.png", "صفحه‌ی اصلی — منوی دایره‌ای", "Home — the radial menu"),
    ("scheduler_fa.png", "زمان‌بند cron — هر کار، سر ساعت خودش",
     "cron scheduler — every job on its own schedule"),
    ("vault_fa.png", "گاوصندوق رمزدار — AES-256-GCM", "Encrypted vault — AES-256-GCM"),
    ("undo_fa.png", "ژورنال و بازگردانی با Ctrl+Z", "Undo journal — Ctrl+Z"),
    ("macros_fa.png", "کتابخانه‌ی ماکرو — اجرای واقعی و تمرینی",
     "Macro library — real and dry runs"),
    ("dashboard_fa.png", "داشبورد زنده — نرخ موفقیت، پرمصرف‌ترین ابزارها، CPU/RAM/Disk",
     "Live dashboard — success rate, top tools, live meters"),
    ("plan_fa.png", "دستیار برنامه‌ریز — یک جمله، یک زنجیره‌ی ابزار",
     "Planner — one sentence, one tool chain"),
    ("workflows_fa.png", "گردش‌کارها — کتابخانه و ویرایشگر مرحله‌به‌مرحله",
     "Workflows — library and step editor"),
    ("automation_fa.png", "خودکارسازی پوشه — قانون‌های دیده‌بان", "Folder automation — watcher rules"),
    ("drop_zone.png", "رهاشدن فایل: پیشنهاد هوشمند ابزار", "Smart drop: tools suggested for your files"),
    ("job_queue.png", "صف کارها: اجرای هم‌زمان و پس‌زمینه", "Job queue: parallel background runs"),
    # caption is computed from the live registry in main() so it cannot rot
    ("pdf_fa.png", "", ""),
    ("tool_fa.png", "پنل ابزار با فرم خودکار", "Tool sheet with an auto-generated form"),
    ("result_fa.png", "اجرای واقعی + کارت نتیجه", "A real run + result card"),
    ("assistant_fa.png", "دستیار: جست‌وجوی زبان طبیعی", "Assistant: natural-language search"),
    ("palette_commands.png", "پالت دستورها و اجراهای اخیر", "Command palette with recents"),
    ("dev_fa.png", "دسته‌ی توسعه‌دهنده (Regex، Diff، JSON، JWT)", "Developer category"),
    ("persian_tool.png", "ابزار تاریخ شمسی ⇄ میلادی", "Jalali ↔ Gregorian date tool"),
    ("settings_fa.png", "تنظیمات: زبان، پوسته، رنگ", "Settings: language, theme, accent"),
    ("settings_system.png", "تنظیمات: کارهای هم‌زمان، افزونه‌ها، درباره",
     "Settings: parallelism, plugins, about"),
    ("dashboard_en.png", "داشبورد در رابط انگلیسی", "Dashboard in English"),
    ("plan_en.png", "برنامه‌ریز در رابط انگلیسی", "Planner in English"),
    ("home_en.png", "رابط انگلیسی", "English interface"),
    ("palette_en.png", "پالت دستور در رابط انگلیسی", "Command palette in English"),
    ("home_light.png", "پوسته‌ی روشن (Daylight)", "Light theme"),
    ("convert_midnight.png", "پوسته‌ی نیمه‌شب + دسته‌ی تبدیل", "Midnight theme + Convert category"),
    ("tool_light.png", "پنل ابزار در پوسته‌ی روشن", "Tool sheet in the light theme"),
    ("macro_fa.png", "اجرای ماکرو با انتخاب فایل", "Running a macro file"),
    ("scheduler_en.png", "زمان‌بند در رابط انگلیسی", "Scheduler in English"),
    ("redact_fa.png", "حذف واقعی محتوا از PDF — متن پاک می‌شود، نه پوشیده",
     "Real PDF redaction — the text is removed, not covered"),
    ("ocr_setup_fa.png", "راه‌اندازی OCR — موتور، مسیر و بسته‌ی زبان",
     "OCR setup — engine, path and language packs"),
    ("transcribe_fa.png", "گفتار به متن و زیرنویس خودکار با Whisper",
     "Speech to text and auto subtitles with Whisper"),
    ("sign_fa.png", "امضای دیجیتال PDF با گواهی PKCS#12 + بررسی دستکاری",
     "PDF digital signature (PKCS#12) with tamper detection"),
]


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def tool_cards() -> str:
    blocks = []
    for category in CATEGORIES:
        if category.key == "home":
            continue
        tools = tools_in(category.key)
        items = "".join(f"<li>{tool.name} <span class='en'>{tool.name_en}</span></li>" for tool in tools)
        blocks.append(
            f"<div class='card' style='--accent:{category.color}'>"
            f"<h3>{category.name} <span class='count'>{len(tools)}</span></h3>"
            f"<p class='en'>{category.name_en} — {category.tagline_en}</p><ul>{items}</ul></div>"
        )
    return "\n".join(blocks)


def main() -> int:
    load_all()
    total_tools = sum(len(tools_in(c.key)) for c in CATEGORIES if c.key != "home")
    categories = len([c for c in CATEGORIES if c.key != "home"])
    workflows = len(workflow_store.all())
    intents = len(planner.INTENTS)
    macros = len(macro_module.builtin_macros("fa"))
    presets = len(scheduler_module.PRESETS)
    suites = STATS["suites"]
    checks = STATS["checks"]                 # docs/stats.json is the only source
    assert checks == sum(suites.values()), "docs/stats.json total is wrong"
    shots_dir = ROOT / "docs" / "screenshots"

    gallery = []
    for filename, caption_fa, caption_en in SHOTS:
        if filename == "pdf_fa.png":
            count = len(tools_in("pdf"))
            caption_fa = f"دسته‌ی PDF — {fa(count)} ابزار"
            caption_en = f"PDF category — {count} tools"
        path = shots_dir / filename
        if not path.exists():
            print("missing screenshot:", filename)
            continue
        gallery.append(
            f"<figure><img src='{data_uri(path)}' alt='{caption_en}'/>"
            f"<figcaption>{caption_fa}<span class='en'>{caption_en}</span></figcaption></figure>"
        )

    html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>WinGenius {APP_VERSION} — دستیار همه‌کاره‌ی ویندوز</title>
<style>
  :root {{ --bg:#070a14; --panel:#121830; --panel2:#1a2140; --border:#2a3557;
           --text:#eaf0ff; --dim:#8d9ac0; --accent:#5B8CFF; --accent2:#8B5CFF; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:radial-gradient(1200px 700px at 80% -10%,#1b2350 0%,var(--bg) 60%);
          color:var(--text); font-family:"Vazirmatn","Segoe UI",Tahoma,sans-serif; line-height:1.75; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:48px 22px 72px; }}
  header {{ text-align:center; margin-bottom:44px; }}
  .logo {{ width:96px; height:96px; border-radius:24px; box-shadow:0 18px 50px rgba(91,140,255,.35); }}
  h1 {{ margin:14px 0 4px; font-size:2.6rem; letter-spacing:-.5px;
        background:linear-gradient(90deg,var(--accent),var(--accent2)); -webkit-background-clip:text;
        background-clip:text; color:transparent; }}
  .tag {{ color:var(--dim); font-size:1.02rem; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; margin-top:26px; }}
  .stat {{ background:linear-gradient(160deg,var(--panel2),var(--panel)); border:1px solid var(--border);
           border-radius:16px; padding:14px 20px; min-width:132px; }}
  .stat b {{ display:block; font-size:1.7rem; background:linear-gradient(90deg,var(--accent),var(--accent2));
             -webkit-background-clip:text; background-clip:text; color:transparent; }}
  .stat span {{ color:var(--dim); font-size:.84rem; }}
  section {{ margin-top:56px; }}
  h2 {{ font-size:1.5rem; margin:0 0 16px; }}
  h2::before {{ content:"◆"; color:var(--accent); margin-inline-end:10px; font-size:.9rem; }}
  .gallery {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:22px; }}
  figure {{ margin:0; background:var(--panel); border:1px solid var(--border); border-radius:18px;
            overflow:hidden; box-shadow:0 16px 40px rgba(0,0,0,.35); }}
  figure img {{ width:100%; display:block; }}
  figcaption {{ padding:12px 16px; font-size:.9rem; }}
  figcaption .en {{ display:block; color:var(--dim); font-size:.78rem; direction:ltr; text-align:left; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:18px; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:16px 18px;
           border-top:3px solid var(--accent); }}
  .card h3 {{ margin:0 0 2px; font-size:1.02rem; }}
  .card .count {{ float:left; background:color-mix(in srgb, var(--accent) 22%, transparent);
                  color:var(--accent); border-radius:999px; padding:0 10px; font-size:.78rem; }}
  .card p.en {{ margin:0 0 8px; color:var(--dim); font-size:.76rem; direction:ltr; text-align:left; }}
  .card ul {{ margin:0; padding-inline-start:18px; font-size:.86rem; columns:2; column-gap:16px; }}
  .card li {{ margin-bottom:2px; }}
  .card li .en {{ color:var(--dim); font-size:.7rem; direction:ltr; }}
  pre {{ background:#05070f; border:1px solid var(--border); border-radius:14px; padding:16px 18px;
         overflow:auto; direction:ltr; text-align:left; font-size:.85rem; color:#cfe0ff; }}
  code {{ font-family:"Cascadia Code",Consolas,monospace; }}
  .note {{ background:linear-gradient(140deg,rgba(91,140,255,.14),rgba(139,92,255,.10));
           border:1px solid rgba(91,140,255,.35); border-radius:16px; padding:16px 20px; }}
  footer {{ margin-top:64px; color:var(--dim); font-size:.82rem; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img class="logo" src="{data_uri(ROOT / 'app' / 'assets' / 'icon.png')}" alt="WinGenius"/>
    <h1>WinGenius {APP_VERSION}</h1>
    <div class="tag">دستیار همه‌کاره‌ی ویندوز — یک جمله بنویسید، وین‌جنیوس ابزارها را می‌چیند و اجرا می‌کند</div>
    <div class="stats">
      <div class="stat"><b>{total_tools}</b><span>ابزار آماده</span></div>
      <div class="stat"><b>{categories}</b><span>دسته‌بندی</span></div>
      <div class="stat"><b>{workflows}</b><span>گردش‌کار آماده</span></div>
      <div class="stat"><b>{intents}</b><span>نیت زبان طبیعی</span></div>
      <div class="stat"><b>{presets}</b><span>زمان‌بندی آماده</span></div>
      <div class="stat"><b>{macros}</b><span>قالب ماکرو</span></div>
      <div class="stat"><b>{checks:,}</b><span>بررسی خودکار PASS</span></div>
      <div class="stat"><b>۲</b><span>زبان (فارسی/انگلیسی)</span></div>
    </div>
  </header>

  <section>
    <h2>نگاهی به برنامه</h2>
    <div class="gallery">{''.join(gallery)}</div>
  </section>

  <section>
    <h2>موتور نسخه ۴ — امنیت، بازگشت‌پذیری، تکرار خودکار</h2>
    <div class="cards">
      <div class="card" style="--accent:#FF4757"><h3>گاوصندوق رمزدار</h3>
        <p class="en">Encrypted vault — AES-256-GCM + scrypt, authenticated header</p>
        <p>فایل <code>.wgv</code> قابل حمل: هر فایل یا یادداشت جدا رمز می‌شود، هدر (پارامترهای KDF و
        salt) احراز می‌شود، تغییر رمز همه‌چیز را از نو رمز می‌کند، گاوصندوق‌های قدیمی در جا ارتقا
        می‌یابند. هفت ابزار کامل.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>ژورنال و بازگردانی (Ctrl+Z)</h3>
        <p class="en">Undo journal — every file change is reversible</p>
        <p>تغییر نام گروهی، دسته‌بندی خودکار و صاف‌کردن پوشه در ژورنال SQLite ثبت می‌شوند و با یک
        کلید برمی‌گردند؛ حذف‌ها به سطل بازیافت داخلی می‌روند.</p></div>
      <div class="card" style="--accent:#5B8CFF"><h3>زمان‌بند cron</h3>
        <p class="en">cron scheduler — with catch-up for missed runs</p>
        <p>عبارت cron معتبرسنجی و به فارسی خوانده می‌شود («هر ۱۵ دقیقه»)، اجرای ازدست‌رفته بعد از
        خاموش‌بودن جبران می‌شود، و هدف می‌تواند گردش‌کار، ابزار یا ماکرو باشد.</p></div>
      <div class="card" style="--accent:#FFB020"><h3>موتور ماکرو شرطی</h3>
        <p class="en">Macro engine — if/else, foreach, dry-run</p>
        <p>گام‌های copy/move/delete/mkdir/write/run/workflow/foreach/if/sleep/log با متغیر و glob؛
        اعتبارسنجی قبل از اجرا و «اجرای تمرینی» بدون دست‌زدن به فایل‌ها.</p></div>
      <div class="card" style="--accent:#8B5CFF"><h3>API محلی JSON</h3>
        <p class="en">Local JSON API — token protected</p>
        <p><code>/health</code>، <code>/tools</code>، <code>/plan</code>، <code>/run</code>،
        <code>/journal</code>، <code>/undo</code> روی <code>127.0.0.1</code> با توکن؛ برای اسکریپت و
        Power Automate.</p></div>
      <div class="card" style="--accent:#18C8F5"><h3>هفت شیت و پنج میان‌بر</h3>
        <p class="en">Five sheets on the nav rail</p>
        <p>داشبورد، برنامه‌ریز، گردش‌کارها، خودکارسازی و زمان‌بند با <code>Ctrl+1…5</code>؛
        <code>Ctrl+Z</code> بازگردانی، و پالت دستورها با چهار فرمان تازه.</p></div>
    </div>
    <pre><code>REM نسخه‌ی ۴ از خط فرمان
WinGenius.exe --list-schedules
WinGenius.exe --run-schedule 3f9a12
WinGenius.exe --macros
WinGenius.exe --run-macro "C:\\Users\\me\\AppData\\Roaming\\WinGenius\\macros\\pack.json" --dry-run
WinGenius.exe --undo
WinGenius.exe --serve --port 8770

REM API محلی (توکن در api_token.txt)
curl -H "X-WinGenius-Token: &lt;token&gt;" http://127.0.0.1:8770/health
curl -H "X-WinGenius-Token: &lt;token&gt;" -X POST http://127.0.0.1:8770/run -d '{{"tool":"uuid_generate"}}'</code></pre>
  </section>

  <section>
    <h2>نسخه ۴.۱ — سخت‌سازی و توزیع</h2>
    <div class="cards">
      <div class="card" style="--accent:#FF4757"><h3>نسخه‌بندی داده و مهاجرت</h3>
        <p class="en">Versioned data folder with automatic backups</p>
        <p>پوشه‌ی داده <code>schema.json</code> دارد؛ قبل از هر مهاجرت یک پشتیبان کامل در
        <code>backups/</code> ساخته می‌شود (۵ نسخه‌ی آخر)، قدم‌ها idempotent‌اند و ژورنال با
        <code>PRAGMA user_version</code> در جا ارتقا می‌یابد.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>تک‌نسخه‌ای</h3>
        <p class="en">Single instance guard</p>
        <p>باز کردن نسخه‌ی دوم، پنجره‌ی نسخه‌ی اول را جلو می‌آورد؛ دیگر دو نسخه روی یک ژورنال و
        زمان‌بند نمی‌جنگند. سگمنت کهنه پس از کرش هم تشخیص داده می‌شود.</p></div>
      <div class="card" style="--accent:#5B8CFF"><h3>API محلی مقاوم</h3>
        <p class="en">Rate limits, body limits, real 413</p>
        <p>۳۰۰ درخواست در دقیقه برای هر IP، بدنه‌ی بیش از ۱ مگابایت با <code>413</code> واقعی رد
        می‌شود (تا ۸ مگابایت خوانده و دور ریخته می‌شود تا کلاینت پیام را ببیند، نه reset).</p></div>
      <div class="card" style="--accent:#FFB020"><h3>تست درستی خروجی</h3>
        <p class="en">Output-correctness suite — does the file make sense?</p>
        <p>{fa(suites["output"])} بررسی که خود فایل تولیدشده را باز می‌کند: تعداد صفحه‌ی PDF، ابعاد تصویر، ستون‌های CSV،
        بازخوانی QR، فریم‌های ویدیو و رمزگشایی واقعی گاوصندوق. سه باگ واقعی همین‌جا پیدا شد.</p></div>
      <div class="card" style="--accent:#8B5CFF"><h3>CI و ابزار کیفیت</h3>
        <p class="en">GitHub Actions + ruff + packaging checks</p>
        <p>ویندوز و لینوکس، پایتون ۳.۱۲ و ۳.۱۳، پنج مجموعه تست، <code>ruff</code> با قواعد
        خطای واقعی و <code>verify_package.py</code> برای هم‌خوانی فایل‌های ساخت.</p></div>
      <div class="card" style="--accent:#18C8F5"><h3>مجوز شفاف</h3>
        <p class="en">AGPL-3.0 + third-party notices</p>
        <p><code>LICENSE</code> کامل، فهرست مجوز وابستگی‌ها و باینری‌های همراه، صفحه‌ی مجوز در
        نصب‌کننده، و دو مسیر مستند برای رسیدن به مجوز سهل‌گیرانه.</p></div>
    </div>
  </section>

  <section>
    <h2>نسخه ۵.۰ — امضا، جست‌وجوی هوشمند، گزارش خطا</h2>
    <div class="cards">
      <div class="card" style="--accent:#FF4757"><h3>امضای دیجیتال PDF</h3>
        <p class="en">Real digital signatures (PKCS#12) + tamper detection</p>
        <p>امضای رمزنگاری‌شده با گواهی <code>.p12/.pfx</code> و بررسی بعد از امضا؛ اگر کسی بعد از
        امضا چیزی در فایل تغییر بدهد، پوشش از <code>ENTIRE_FILE</code> به
        <code>ENTIRE_REVISION</code> می‌افتد و ابزار هشدار می‌دهد.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>جست‌وجو در اسکن‌ها</h3>
        <p class="en">Scanned PDFs become searchable through OCR</p>
        <p>ایندکس، نبودِ لایه‌ی متن را تشخیص می‌دهد (متن کم + تصویر بزرگ روی صفحه)، صفحه‌های اول را
        رندر و با Tesseract می‌خواند و متن را ایندکس می‌کند — با یک کلید در تنظیمات.</p></div>
      <div class="card" style="--accent:#5B8CFF"><h3>گزارش خطای قابل ارسال</h3>
        <p class="en">Crash report bundle with redaction</p>
        <p>یک ZIP با نسخه، وضعیت وابستگی‌ها و انتهای لاگ — بعد از حذف مسیر خانه، نام کاربری و
        مقادیر کنار <code>token/password/secret</code>.</p></div>
      <div class="card" style="--accent:#FFB020"><h3>تست مقیاس</h3>
        <p class="en">Scale tests: 600 files, 250 documents</p>
        <p>۶۰۰ فایل تغییر نام + بازگردانی کامل، و ایندکس ۲۵۰ سند — با زمان‌سنجی؛ اگر از ۹۰ ثانیه
        بگذرد تست شکست می‌خورد.</p></div>
      <div class="card" style="--accent:#8B5CFF"><h3>ژورنال خودترمیم</h3>
        <p class="en">The journal rebuilds itself if the file disappears</p>
        <p>اگر <code>journal.sqlite3</code> وسط اجرا حذف/بازنویسی شود، عمل بعدی با
        <code>no such table</code> نمی‌شکند: شِما همان‌جا بازسازی می‌شود.</p></div>
      <div class="card" style="--accent:#18C8F5"><h3>{fa(checks)} بررسی</h3>
        <p class="en">{checks:,} automated checks, all green</p>
        <p>{fa(suites["smoke"])} دودی · {fa(suites["functional"])} موتور · {fa(suites["output"])} خروجی · {fa(suites["ui"])} رابط · {fa(suites["security"])} امنیت · {fa(suites["packaging"])} بسته‌بندی — همه بدون دخالت دست اجرا شده‌اند.</p></div>

    </div>
  </section>

  <section>
    <h2>نسخه ۴.۲ — حذف واقعی، گفتار، ویندوزی‌تر</h2>
    <div class="cards">
      <div class="card" style="--accent:#FF4757"><h3>حذف واقعی محتوا از PDF</h3>
        <p class="en">Real redaction — the text is gone, not covered</p>
        <p>عبارت یا ناحیه را انتخاب می‌کنید و متن <b>از محتوای فایل پاک</b> می‌شود؛ بعد خودِ ابزار
        فایل را باز می‌کند و تأیید می‌کند که دیگر استخراج نمی‌شود. مستطیل سیاه روی متن، امنیت نیست.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>گفتار به متن و زیرنویس</h3>
        <p class="en">Whisper — local speech to text + SRT</p>
        <p>صدا یا ویدیو → متن و/یا فایل <code>.srt</code> آماده‌ی پخش؛ مدل از tiny تا large-v3،
        تشخیص خودکار زبان، بدون هیچ آپلودی.</p></div>
      <div class="card" style="--accent:#5B8CFF"><h3>OCR بدون دردسر</h3>
        <p class="en">One-click OCR setup and language packs</p>
        <p>ابزار «راه‌اندازی OCR» موتور را پیدا می‌کند، مسیر غیرمعمول را ثبت می‌کند و بسته‌ی زبان
        فارسی را دانلود می‌کند — همه‌ی ابزارهای OCR از همان مسیر استفاده می‌کنند.</p></div>
      <div class="card" style="--accent:#FFB020"><h3>ادغام با ویندوز</h3>
        <p class="en">“Send to” shortcut + portable mode</p>
        <p>راست‌کلیک روی فایل → <code>Send to → WinGenius</code>؛ و با <code>--portable</code>
        همه‌ی داده‌ها کنار exe می‌نشینند (فلش و سیستم مشترک).</p></div>
      <div class="card" style="--accent:#8B5CFF"><h3>دسترس‌پذیری</h3>
        <p class="en">Screen-reader names on every field</p>
        <p>هر فیلد فرم خودکار و هر دکمه‌ی نوار کناری نام و توضیح دسترس‌پذیر دارد و با تغییر زبان
        به‌روز می‌شود؛ تست UI همه‌ی ۵۰۰+ فیلد را بازبینی می‌کند.</p></div>
      <div class="card" style="--accent:#18C8F5"><h3>{fa(suites["output"])} بررسی خروجی</h3>
        <p class="en">Output-correctness suite — {suites["output"]} checks on the produced files</p>
        <p>سیزده بررسی تازه برای ابزارهای PDF که تا حالا فقط «کرش نکردن»شان تست می‌شد: شماره‌صفحه،
        متادیتا، جست‌وجو، Markdown، صفحه‌ی خالی، ترتیب، CropBox، جزوه و پوستر.</p></div>
    </div>
  </section>

  <section>
    <h2>موتور نسخه ۳ — از ابزار به دستیار</h2>
    <div class="cards">
      <div class="card" style="--accent:#6C8CFF"><h3>دستیار برنامه‌ریز</h3>
        <p class="en">Planner — plain language in, tool chain out</p>
        <p>«این عکس‌ها را کم‌حجم کن و PDF بساز» بنویسید؛ از {intents} نیت فارسی/انگلیسی یک زنجیره‌ی
        چندمرحله‌ای ساخته می‌شود، مقادیر خودکار پر می‌شوند و می‌توانید همان را اجرا یا ذخیره کنید.</p></div>
      <div class="card" style="--accent:#9B5CFF"><h3>موتور گردش‌کار</h3>
        <p class="en">Workflows — chain tools, pipe outputs</p>
        <p>{workflows} گردش‌کار آماده + ویرایشگر: افزودن، جابه‌جایی و حذف مرحله؛ خروجی هر مرحله با
        <code>{{prev}}</code>/<code>{{prev_dir}}</code> به مرحله‌ی بعد می‌رود و وضعیت هر مرحله زنده دیده می‌شود.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>خودکارسازی پوشه</h3>
        <p class="en">Folder watcher — new file, automatic run</p>
        <p>پوشه‌ی دانلود یا اسکنر را زیر نظر بگیرید؛ فایل تازه که رسید، ابزار یا کل گردش‌کار
        خودکار اجرا می‌شود (فیلتر پسوند، زیرپوشه، تأخیر تا پایان کپی).</p></div>
      <div class="card" style="--accent:#18C8F5"><h3>ایندکس جست‌وجوی متن</h3>
        <p class="en">FTS5 index — search inside every document</p>
        <p>متن PDF/Word/TXT/Markdown/کدها در یک ایندکس SQLite FTS5 ذخیره می‌شود؛ جست‌وجوی
        لحظه‌ای با تطبیق عبارت و برجسته‌سازی.</p></div>
      <div class="card" style="--accent:#FFB020"><h3>هوش کلیپ‌بورد</h3>
        <p class="en">Clipboard intelligence — recognise, then suggest</p>
        <p>لینک، مسیر، JSON، رنگ، SHA، Base64، ایمیل، تاریخ شمسی و JWT شناسایی می‌شود و
        پیشنهاد اقدام می‌گیرید؛ با <code>Ctrl+Shift+V</code> اجرا کنید.</p></div>
      <div class="card" style="--accent:#FF6C8C"><h3>داشبورد و ریل ناوبری</h3>
        <p class="en">Dashboard + nav rail</p>
        <p>نرخ موفقیت ۲۴ ساعته، پرمصرف‌ترین ابزارها و وضعیت لحظه‌ای سیستم در یک شیت؛
        چهار شیت تازه با <code>Ctrl+1…4</code> (نسخه‌ی ۴ پنجمی را هم اضافه کرد).</p></div>
    </div>
    <pre><code>REM نسخه‌ی ۳ از خط فرمان: گردش‌کار و برنامه‌ریز
WinGenius.exe --list-workflows
WinGenius.exe --run-workflow pdf_to_images_zip --file "C:\\in\\report.pdf" --out "C:\\out"
WinGenius.exe --plan "حجم این PDF را کم کن" --file "C:\\in\\report.pdf"
WinGenius.exe --plan "عکس‌ها را کم‌حجم کن و PDF بساز" --run
WinGenius.exe --plan "سلامت سیستم را چک کن" --dry-run --json</code></pre>
  </section>

  <section>
    <h2>ساخت exe (ویندوز)</h2>
    <pre><code>REM روش تک‌کلیک
packaging\\build.bat

REM یا دستی
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements-build.txt
python packaging\\build_exe.py             REM → dist\\WinGenius.exe
python packaging\\build_exe.py --installer REM → dist\\WinGenius-Setup.exe (با Inno Setup)</code></pre>
    <p class="note">خروجی یک فایل مستقل <code>WinGenius.exe</code> است (بدون نیاز به نصب پایتون روی سیستم مقصد).
    برای تجربه‌ی «نصب واقعی» با میان‌بر Start Menu و حذف‌کننده، از اسکریپت Inno Setup استفاده کنید.</p>
  </section>

  <section>
    <h2>موتور نسخه ۲ (همچنان فعال)</h2>
    <div class="cards">
      <div class="card" style="--accent:#18C8F5"><h3>صف کارها و اجرای هم‌زمان</h3>
        <p class="en">Job engine — queue, parallelism, cancel, live progress</p>
        <p>هر ابزار در پس‌زمینه اجرا می‌شود؛ چند کار هم‌زمان، لغو و اجرای دوباره. رابط هیچ‌وقت قفل نمی‌شود.</p></div>
      <div class="card" style="--accent:#7B61FF"><h3>رهاشدن هوشمند فایل</h3>
        <p class="en">Smart drop — pick the right tool for the file</p>
        <p>فایل را روی پنجره رها کنید؛ بهترین ابزارها پیشنهاد و فیلدها خودکار پر می‌شوند.</p></div>
      <div class="card" style="--accent:#2BD9A0"><h3>افزونه‌پذیری واقعی</h3>
        <p class="en">Plugins — any .py file becomes a tool</p>
        <p>یک فایل پایتون در پوشه‌ی افزونه‌ها بگذارید؛ همان لحظه ابزار می‌شود.</p></div>
      <div class="card" style="--accent:#FFB020"><h3>تاریخچه، عیب‌یابی و CLI</h3>
        <p class="en">History, diagnostics and automation</p>
        <p>هر اجرا با خروجی‌هایش ذخیره می‌شود؛ گزارش وضعیت با یک کلیک؛ همان ابزارها از خط فرمان.</p></div>
    </div>
    <p class="note">افزونه‌ی نمونه (در پوشه‌ی <code>plugins</code> بگذارید و برنامه را دوباره باز کنید):</p>
    <pre><code>from app.core.registry import tool
from app.core.spec import K_TEXT, Field, RunResult

@tool(id="hello", category="dev", name="سلام", name_en="Hello World",
      desc="نمونه‌ی افزونه", desc_en="Example plugin", icon="puzzle",
      fields=[Field("name", K_TEXT, "نام", "Name", default="دنیا")])
def hello(ctx, values):
    return RunResult(message=f"سلام {{values['name']}}!")</code></pre>
  </section>

  <section>
    <h2>همه‌ی ابزارها ({total_tools})</h2>
    <div class="cards">{tool_cards()}</div>
  </section>

  <section>
    <h2>معماری</h2>
    <pre><code>app/core    spec.py · registry.py · worker.py (QThread) · nlsearch.py · theme.py
            jobs.py (صف و اجرای هم‌زمان) · history.py · plugins.py · diagnostics.py
            cli.py (خط فرمان) · suggest.py (تشخیص نوع فایل)
            planner.py ({intents} نیت) · workflows.py (اجرای زنجیره) · watcher.py (دیده‌بان)
            indexer.py (SQLite FTS5) · clipboard.py (تشخیص محتوا)
            vault.py (گاوصندوق) · journal.py (ژورنال + سطل) · scheduler.py (cron)
            macro.py (DSL ماکرو) · api_server.py (API محلی)
            upgrade.py (نسخه‌بندی داده + مهاجرت) · single_instance.py (تک‌نسخه‌ای)
app/ui      radial.py · main_window.py · tool_panel.py · quick_search.py
            jobs_drawer.py · drop_zone.py · settings_panel.py
            overlay.py · dashboard.py · plan_panel.py · workflow_panel.py · automation_panel.py · tray.py
app/tools   ماژول‌های اعلانی در {categories} دسته (+ افزونه‌های کاربر)
tests       smoke_test.py ({fa(suites["smoke"])}) · functional_test.py ({fa(suites["functional"])}، شامل schema/stress/fuzz/guards) ·
            output_test.py ({fa(suites["output"])} بررسی درستی خروجی) · security_test.py ({fa(suites["security"])} بررسی امنیتی) ·
            ui_test.py ({fa(suites["ui"])} رابط + دسترس‌پذیری) · verify_package.py ({fa(suites["packaging"])}) → جمع {fa(checks)} بررسی PASS</code></pre>
    <p class="note">افزودن ابزار جدید = یک تابع با دکوراتور <code>@tool(...)</code>؛ فرم، اجرا در ترد،
    پیشرفت، لغو، نتیجه، جست‌وجوی دستیار و آیکون به‌صورت خودکار ساخته می‌شوند.</p>
  </section>

  <footer>WinGenius {APP_VERSION} — ساخته‌شده با PySide6 · {total_tools} ابزار ·
  بدون تله‌متری؛ پردازش فایل‌ها محلی، ابزارهای شبکه فقط به درخواست شما</footer>
</div>
</body>
</html>
"""
    output = ROOT / "docs" / "showcase.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"showcase written → {output} ({size_mb:.1f} MB, {len(gallery)} screenshots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
