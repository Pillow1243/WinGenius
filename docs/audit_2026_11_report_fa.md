# گزارش پایانی — رفع ۱۷ یافته‌ی بازبینی مستقل WinGenius 7.1.0

> مبنای کار: ریپو `Pillow1243/WinGenius` · کامیت پایه `582c252` · شاخه `arena/aedbdf71-wingenius`
> ۱۹ کامیت روی شاخه، ۴۶ فایل تغییر‌یافته (+۲٬۶۶۲ / −۱۳۷ خط)، ۱۳ فایل تست تازه (۶۹ تست + ۱ skip)

---

## ۱. جدول یافته‌ها

| شناسه یافته | فایل‌های تغییریافته | تست اضافه‌شده | نتیجه اجرا | وضعیت |
|---|---|---|---|---|
| F-01 watcher بدون پنجره‌ی آرام‌سازی فایل نیمه‌کپی را برمی‌داشت | `app/core/watcher.py` | `tests/audit_phase1_test.py` | ✅ pass | **رفع شد و تست دارد** |
| F-02 ایندکس، ردیف‌های حذف‌شده/حساس را پاک نمی‌کرد | `app/core/indexer.py` | `tests/audit_phase1_test.py` | ✅ pass | **رفع شد و تست دارد** |
| F-03 نوشتن ماکرو بدون undo و با بازنویسی خاموش | `app/core/macro.py` | `tests/audit_phase1_test.py` | ✅ pass | **رفع شد و تست دارد** |
| F-04 معنای OR در کرون (روز ماه/روز هفته) و نام روزها | `app/core/scheduler.py` | `tests/audit_phase1_test.py` | ✅ pass | **رفع شد و تست دارد** |
| F-05 کلید عمومی امضای آپدیت placeholder است | `app/core/updates.py`, `scripts/sign_release.py`, `scripts/check_release.py` | `tests/audit_f05_test.py` | ✅ ۶ تست pass | ⛔ **منتظر مالک** (کلید واقعی در ریپو نیست؛ مسیر امضا/وریفای تست شد) |
| F-06 کارهای بازیابی‌شده بدون رمز خودکار اجرا می‌شدند | `app/core/jobs.py`, `app/ui/jobs_drawer.py` | `tests/audit_f06_test.py` | ✅ ۳ تست pass | **رفع شد و تست دارد** |
| F-07 زمان‌بندی ابزار بدون اعتبارسنجی ورودی/با رمز ذخیره می‌شد | `app/core/scheduler.py`, `app/ui/scheduler_panel.py` | `tests/audit_f07_test.py` | ✅ pass | **رفع شد و تست دارد** |
| F-08 امتیاز هدرهای امنیتی بر «حضور» نه «مقدار» | `app/tools/network_extra2.py` | `tests/audit_f08_test.py` | ✅ ۴ تست pass | **رفع شد و تست دارد** |
| F-09 سخت‌سازی مجوزها روی ویندوز no-op بود | `app/core/paths.py`, `app/core/api_server.py` | `tests/audit_f09_test.py` | ✅ ۶ تست pass | **رفع شد و تست دارد** (اجرای واقعی icacls نیاز به ویندوز دارد) |
| F-10 کش `seen` واتچر بی‌کران | `app/core/watcher.py` | `tests/audit_phase1_test.py` | ✅ pass (روی ۳.۱۲+) | **رفع شد و تست دارد** |
| F-11 گاوصندوق فایل بزرگ را کامل در حافظه می‌خواند | `app/core/vault.py` | `tests/audit_f11_test.py` | ✅ ۲ تست pass | **رفع شد و تست دارد** |
| F-12 نسخه‌های وابستگی قفل نبودند | `constraints.txt`, `scripts/check_deps.py`, `.github/workflows/ci.yml`, `docs/install_fa.md`, `docs/README.en.md` | `tests/audit_f12_test.py` | ✅ ۵ تست pass | **رفع شد و تست دارد** |
| F-13 mypy پیکربندی شده بود ولی هرگز اجرا نمی‌شد | `scripts/typecheck.py`, `docs/mypy_baseline.json`, `.github/workflows/ci.yml` | `tests/audit_f13_test.py` | ✅ ۵ تست pass | **رفع شد و تست دارد** (اجرای CI روی ۳.۱۲) |
| F-14 پیام رمز اشتباه و پاک‌سازی `.part` در استخراج آرشیو | `app/tools/files_extra3.py` | `tests/audit_f14_test.py` | ✅ ۲ تست pass | **رفع شد و تست دارد** |
| F-15 متن فارسی در رابط انگلیسی باقی می‌ماند | `app/ui/dashboard.py`, `app/ui/automation_panel.py`, `app/ui/tool_panel.py`, `app/ui/jobs_drawer.py` | `tests/audit_f15_test.py` | ✅ ۹ تست pass | **رفع شد و تست دارد** |
| F-16 اعداد منتشرشده (بررسی‌ها/پوشش) بازتولیدپذیر نبودند | `scripts/stats.py`, `docs/stats.json`, `README.md` | `tests/audit_f16_test.py` | ✅ ۸ تست pass | **رفع شد و تست دارد** (بازتولید دقیق ۲٬۳۳۷) |
| F-17 `site_check` با GET کامل و `verify=False` بدون تأیید | `app/tools/network_tools.py`, `app/core/execution.py` | `tests/audit_f17_test.py` | ✅ ۳ تست pass | **رفع شد و تست دارد** |

---

## ۲. دستورهای اجراشده و کد خروج

همه با `LD_LIBRARY_PATH=/tmp/stublibs QT_QPA_PLATFORM=offscreen WINGENIUS_NO_LAUNCH=1` و پایتون ۳.۱۱.۲ (venv در `/tmp/testenv`) اجرا شد.

| # | دستور | کد خروج | نتیجه |
|---|---|---|---|
| 1 | `python -m pytest tests/ -o addopts="" -q` | **0** | ۲۸۳ passed، ۱ skipped در ۱۱۷ ثانیه |
| 2 | `python -m pytest tests/audit_f05…f17_test.py tests/audit_phase1_test.py -q` | **0** | ۶۹ passed، ۱ skipped (تست‌های خودِ یافته‌ها) |
| 3 | `ruff check main.py app tests packaging scripts` | **0** | All checks passed |
| 4 | `python -m compileall -q app tests packaging scripts main.py` | **0** | — |
| 5 | `python scripts/check_deps.py` | **0** | ۲۱ وابستگی هم‌خوان + ۲۳ پین در `constraints.txt` |
| 6 | `python scripts/stats.py --check` | **0** | `OK 292 tools · 2337 checks · v7.1.0` |
| 7 | `python scripts/stats.py --run --only smoke,functional,output,security,hardening,persian,packaging` | **0** | بازتولید ۲٬۳۳۷ (ui از اجرای قبلی، در `unmeasured` ثبت شد) |
| 8 | `python scripts/check_docs.py` | **0** | ۴۲/۴۲ بررسی مستندات |
| 9 | `python packaging/verify_package.py` | **0** | شامل دو هشدار «کلید آپدیت آماده‌ی انتشار نیست» |
| 10 | `python scripts/typecheck.py` | **0** | ۲۳۵ خطای تایپ در ۴۲ فایل، هیچ‌کدام تازه |
| 11 | `python tests/smoke_test.py` | **0** | PASS 153 / FAIL 0 / SKIP 1 |
| 12 | `python tests/functional_test.py` | **0** | 640 passed, 0 failed, 1 skipped |
| 13 | `python tests/output_test.py` | **0** | PASS 111 / FAIL 0 / SKIP 1 |
| 14 | `python -m coverage run --source=app -m pytest tests/` | **0** | پوشش `app/` = **۶۹٪** |
| 15 | اندازه‌گیری به‌سبک `quality_gate` بدون `ui_test` (smoke+functional+output+security + pytest hardening/persian) | **0** | پوشش = **۵۷٪** (بدون `ui_test`) |
| 16 | `git push origin arena/aedbdf71-wingenius` | **0** | ۱۹ کامیت روی شاخه |

---

## ۳. تست‌هایی که اجرا نشدند (و چرا)

| مورد | دلیل |
|---|---|
| `tests/ui_test.py` (۱٬۲۵۹ بررسی) | روی CPython ۳.۱۱.۲ + PySide6 6.12 با `none_dealloc` در `radial.paintEvent` کرش می‌کند؛ خودِ پروژه ۳.۱۲ را حداقل اعلام کرده و در `ci.yml` هم ۳.۱۱ حذف شده است. |
| تست F-10 (کش `seen` با ۴٬۵۰۰ فایل) | روی همین ترکیب ۳.۱۱/C-extension پس از موفقیت تست‌ها در shutdown کرش می‌کند؛ با `skipif(<3.12)` علامت خورده و در CI روی ۳.۱۲/۳.۱۳ اجرا می‌شود. |
| `scripts/quality_gate.py` کامل | وابسته به `ui_test` است (بند بالا)؛ بنابراین گیت پوشش ۷۰٪ در این محیط **قابل تأیید نبود**. |
| اجرای واقعی `icacls` (F-09) | فقط روی ویندوز وجود دارد؛ منطق با mock آزموده شد. |
| امضا با کلید واقعی آپدیت (F-05) | کلید خصوصی نباید در ریپو/محیط باشد؛ با کلید موقت درون‌حافظه‌ای تست شد. |

---

## ۴. محدودیت‌های محیط

- **پایتون ۳.۱۱.۲** به‌جای ۳.۱۲+ (در دسترس نبود؛ سه مسیر نصب امتحان و ناکام ماند). CI روی ۳.۱۲ و ۳.۱۳ است.
- `libGL/libEGL/libxkbcommon/libdbus` سیستم نبود؛ با stub در `/tmp/stublibs` اجرا شد.
- پوشش تست در این محیط (بدون `ui_test`) ۵۷–۶۹٪ است و **به‌عنوان پوشش پروژه گزارش نمی‌شود**؛ مقدار رسمی فقط از `quality_gate.py` می‌آید و در `docs/stats.json` ثبت می‌شود (اکنون `null`، چون اندازه‌گیری نشده).
- شبکه: فقط `github.com`/`pypi.org`؛ تست‌های شبکه‌ای با سرور محلی انجام شد.

---

## ۵. اقدام‌های باقی‌مانده برای مالک

1. **F-05 (تنها یافته‌ی باز):** تولید کلید Ed25519 خارج از ریپو، قرار دادن کلید خصوصی فقط در `secrets.WINGENIUS_UPDATE_SIGNING_KEY` و جای‌گذاری کلید عمومی در `TRUSTED_RELEASE_PUBKEYS` (`app/core/updates.py`). تا آن زمان `scripts/check_release.py` آن را `BLOCKED-NEEDS-OWNER` گزارش می‌کند و `sign_release.py` با کلید غیرجفت امضا نمی‌کند.
2. (اختیاری) پین کردن thumbprint امضای Authenticode در `TRUSTED_AUTHENTICODE_THUMBPRINTS`.
3. تأیید نهایی روی ویندوز/پایتون ۳.۱۲: `ui_test`، اجرای واقعی `icacls`، و عبور گیت پوشش ۷۰٪.
4. اگر انتشار لازم است: ابتدا `constraints.txt` را با نسخه‌های تازه به‌روز کنید (`pip install -r requirements.txt -c constraints.txt` سپس تست‌ها).

---

## ۶. مهاجرت داده‌ها

**هیچ مهاجرتی لازم نیست.** قالب‌های `queue.json`، `schedules.json`، `watch_rules.json`، گاوصندوق v1/v2 و schema ژورنال دست‌نخورده‌اند:

- `queue.json` فقط یک فیلد جدید `needs_secrets` می‌گیرد (فایل‌های قدیمی بدون آن، مقدار پیش‌فرض `False` می‌گیرند → رفتار قبلی).
- `schedules.json`: فیلد `payload` برای زمان‌بندی‌های ابزار اکنون معتبر است؛ رکوردهای قدیمی بدون آن همچنان بارگذاری می‌شوند.
- گاوصندوق: فقط سقف اندازه (۶۴MiB هر فایل / ۵۱۲MiB مجموع) اضافه شد؛ قالب بایت‌به‌بایت همان است.
- `docs/stats.json`: فیلدهای `skips` و `unmeasured` اضافه شدند (افزایشی).

---

## ۷. ریسک‌های باقی‌مانده (صادقانه)

| ریسک | شدت | توضیح |
|---|---|---|
| آپدیت خودکار بدون کلید واقعی کار نمی‌کند | **بالا** | تا اقدام مالک، همه‌ی مسیرهای امضا/وریفای تست‌شده‌اند ولی هیچ بیلد واقعی verify نمی‌شود (F-05 باز). |
| سخت‌سازی ACL ویندوز تأیید نشده | متوسط | منطق `icacls` تست واحد دارد، ولی روی ویندوز واقعی (به‌ویژه دامنه/گروپ‌پالیسی) اجرا نشده؛ خطا هم بی‌صدا نادیده گرفته می‌شود تا استارت‌آپ نشکند. |
| پوشش ۷۰٪ تأیید نشده | متوسط | با ۳.۱۱ و بدون `ui_test` فقط ۵۷–۶۹٪ اندازه‌گیری شد؛ عدد پروژه اکنون منتشر نمی‌شود (به‌جای عدد دست‌نویس قبلی). |
| `ui_test` روی ۳.۱۲+ | متوسط | کرش فقط برای ترکیب ۳.۱۱ + PySide6 6.12 دیده شد؛ روی ۳.۱۲ تأیید نشده. |
| سقف‌های گاوصندوق | پایین | سقف جدید می‌تواند کاربری را که فایل ۷۰MiB داشته غافلگیر کند؛ پیام دوزبانه و روشن است. |
| بک‌لاگ تایپ (۲۳۵ خطا) | پایین | فقط «خطای تازه» مسدود است؛ کاهش تدریجی به‌عهده‌ی تیم. |
| امتیاز هدرها heuristic است | پایین | در پیام ابزار صریح نوشته شده که ممیزی کامل امنیتی سایت نیست. |

---

## ۸. یادداشت روش

ترتیب کار طبق مأموریت بود: ابتدا F-01..F-04 (فاز ۱)، سپس F-05 (آماده‌سازی) + F-09 + F-16، سپس F-06، F-07، F-08، F-10، F-11، F-17، و در پایان F-12..F-15.

برای هر یافته **اول تست شکست‌خورده** نوشته شد (اجرای اول: ۱۰ تست FAIL روی APIهای موجودنبوده)، بعد رفع، بعد سبز شدن. هیچ‌کدام از کنترل‌های `ExecutionService`/`ExecutionPolicy`، الزام امضای آپدیت، اعتماد پلاگین، حذف اسرار از صف، محافظت `save_target` و مسیر خروجی تضعیف نشد؛ برعکس، F-06/F-07/F-17 آن‌ها را سخت‌تر کردند.

دو تست قدیمی به‌دلیل تغییر *رفتار درست* به‌روزرسانی شدند (نه برای سبز شدن): `functional_test.py` نمره‌ی هدرها و `regression_test.py` بازیابی صف.
