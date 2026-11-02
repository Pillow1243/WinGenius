# اجرای WinGenius مو به مو

سه راه هست. **راه ۱** برای ویندوز و مطمئن‌ترین است (بدون build). **راه ۲** اگر exe می‌خواهی.
**راه ۲** اگر ویندوز نداری (ساخت exe روی رانر گیت‌هاب)، و **راه ۴** همان چیزی است که من همین‌جا اجرا کردم.

---

## راه ۱ — ویندوز، از سورس (توصیه‌شده؛ ۵ دقیقه)

### قدم ۱: پایتون
1. برو به <https://www.python.org/downloads/windows/> و **Python 3.12** (یا 3.13) را بگیر.
   ⚠️ Python 3.11 پشتیبانی نمی‌شود: با PySide6 فعلی در رسم منوی شعاعی کرش می‌کند (در CI ثبت شده).
2. در پنجره‌ی نصب، **حتماً** تیک **`Add python.exe to PATH`** را بزن، بعد `Install Now`.
3. بررسی: `Win+R` → بنویس `cmd` → Enter → بنویس:
   ```bat
   python --version
   ```
   باید چیزی مثل `Python 3.12.7` چاپ شود. اگر `python` باز نشد، دوباره نصب کن و تیک PATH را بزن
   (یا از Microsoft Store نصب نکن — نسخه‌ی python.org بهتر است).

### قدم ۲: رفتن به پوشه‌ی برنامه
پوشه‌ی `WinGenius` را در Explorer باز کن، بعد در **نوار آدرس** همان پنجره تایپ کن `cmd` و Enter.
یک کنسول که در همان مسیر باز شده ظاهر می‌شود. بررسی:
```bat
dir main.py
```
باید `main.py` را ببینی. (اگر «فایل پیدا نشد»، در پوشه‌ی اشتباهی هستی.)

### قدم ۳: محیط مجازی
```bat
python -m venv .venv
.venv\Scripts\activate
```
نشانه‌ی موفقیت: ابتدای خط فرمان `(.venv)` اضافه می‌شود.
اگر PowerShell خطای `running scripts is disabled` داد، از همان `cmd` استفاده کن (نه PowerShell).

### قدم ۴: نصب وابستگی‌ها (یک‌بار، ~۲ تا ۵ دقیقه)
```bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```
این خط 100+ مگابایت دانلود می‌کند (PySide6 و PyMuPDF بزرگ‌ترین‌ها هستند). اگر وسط کار قطع شد،
همان دستور را دوباره بزن؛ pip از جایی که مانده ادامه می‌دهد.

### قدم ۵: اجرا
دو راه، هر دو درست:
```bat
python main.py
```
یا **دوبار کلیک روی `run.bat`** — این فایل خودش `.venv` را پیدا می‌کند، UTF-8 را تنظیم می‌کند
(`chcp 65001`، لازم برای فارسی) و اگر برنامه خطا داد پنجره را باز نگه می‌دارد.

پنجره‌ی برنامه باید باز شود: سایدبار دسته‌ها در سمت راست (RTL)، جست‌وجوی سراسری با `Ctrl+K`،
۲۵۴ ابزار.

### قدم ۶: اطمینان از سالم بودن نصب
در همان کنسول (با `.venv` فعال):
```bat
python main.py --doctor
```
باید ببینی `App : WinGenius 5.0.0`، `Tools registered : 254` و همه‌ی کتابخانه‌ها با ✓.
هر خطی که `✗` داشت، یعنی همان وابستگی نصب نشده — دقیقاً همان را نصب کن.

### قدم ۷ (اختیاری): قابلیت‌های اضافه

| چه می‌خواهی | دستور |
|---|---|
| **OCR** (خواندن متن تصویر و PDF اسکن‌شده) | `winget install UB-Mannheim.TesseractOCR` بعد `python -m pip install pytesseract` |
| **زبان فارسی OCR** | فایل `fas.traineddata` را از [tessdata_best](https://github.com/tesseract-ocr/tessdata_best) بگیر و در پوشه‌ی `tessdata` کنار `tesseract.exe` بگذار (پیش‌فرض: `C:\Program Files\Tesseract-OCR\tessdata`) |
| **امضای دیجیتال PDF** | `python -m pip install pyhanko` + گواهی `.p12/.pfx` خودت |
| **گفتار به متن / زیرنویس** | `python -m pip install faster-whisper` (مدل بار اول دانلود می‌شود) |
| **ابزارهای DNS** | `python -m pip install dnspython` |
| **تبدیل Word/Excel/PowerPoint به PDF** | `python -m pip install pywin32` + نصب بودن Office |
| **«ارسال به» در Explorer** | `python main.py --install-sendto` (برداشتن: `--remove-sendto`) |

همه‌ی این‌ها اختیاری‌اند: اگر نباشند، بقیه‌ی ۲۵۴ ابزار سالم کار می‌کند و همان ابزار پیام نصب می‌دهد.

### قدم ۸ (اختیاری): میان‌بر روی دسکتاپ
روی `run.bat` راست‌کلیک → **Send to → Desktop (create shortcut)** → روی میان‌بر راست‌کلیک →
**Properties → Shortcut → Run: Minimized** تا کنسول سیاه آزارت ندهد.

---

## راه ۲ — ساختن exe روی گیت‌هاب (بدون ویندوز، توصیه‌شده)

این همان راهی است که یادت بود: **رانر ویندوزی گیت‌هاب** کار را انجام می‌دهد و فایل‌ها را
همان‌جا به Release می‌چسباند. هیچ ابزاری روی لپ‌تاپ لازم نیست.

1. برو به **Actions** → **Build Windows exe** → **Run workflow** (شاخه `main`).
   بعد از ~۱۰ دقیقه، پایین صفحه‌ی همان اجرا، بخش **Artifacts**:
   `WinGenius-windows` = `WinGenius.exe` + `WinGenius-Setup-5.0.0.exe` + `WinGenius-portable.zip`.
2. برای اینکه **همه** بتوانند نصب کنند (و در صفحه‌ی Releases بماند)، یا:
   - یک تگ بزن: `git tag v5.0.1 && git push origin v5.0.1` → خودکار ساخته و به همان Release
     چسبانده می‌شود؛ یا
   - **Releases → Draft a new release** بساز و Publish کن (رویداد `release` هم همین کار را می‌کند).
3. تمام. فایل‌ها با یک لینک ثابت در دسترس‌اند:
   `https://github.com/Pillow1243/WinGenius/releases/latest`

نکته: exe امضای کد ندارد، پس کاربر بار اول باید **More info → Run anyway** بزند
(یا در سازمان، فایل را allowlist کند). این در `SECURITY.md` هم آمده است.

---

## راه ۳ — ساختن exe روی ویندوز خودت

PyInstaller **کراس‌کامپایل نمی‌کند**: exe ویندوزی فقط روی ویندوز ساخته می‌شود — به همین دلیل
راه ۲ (رانر گیت‌هاب) ساده‌ترین انتخاب است. اگر خودت ویندوز داری، این راه یک‌کلیکی است.

```bat
packaging\build.bat
```
این فایل خودش `.venv` می‌سازد، `requirements-build.txt` را نصب می‌کند و exe را می‌سازد.
خروجی‌ها:

| دستور | خروجی | نکته |
|---|---|---|
| `packaging\build.bat` | `dist\WinGenius.exe` | یک فایل، ساده برای دادن به دیگران، بالا آمدن کندتر |
| `python packaging\build_exe.py --onedir` | `dist\WinGenius\` | بالا آمدن سریع‌تر، پوشه‌ی چندفایلی |
| `python packaging\build_exe.py --installer` | `dist\WinGenius-Setup-5.0.0.exe` | نیاز به **Inno Setup 6** نصب‌شده |
| `python packaging\build_exe.py --clean` | — | `build/` و `dist/` را اول پاک می‌کند |

exe امضای دیجیتال کد (code signing) **ندارد**، پس SmartScreen ممکن است بار اول هشدار بدهد:
**More info → Run anyway**. این طبیعی است و در بخش «چه چیزی عمداً انجام نشد» هم آمده.

---

## راه ۴ — لینوکس / مک (همان که واقعاً اجرا شد)

```bash
cd WinGenius
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh                 # گرافیکی
./run.sh --doctor        # وضعیت محیط
```
`run.sh` تازه اضافه شده: `.venv` را خودش پیدا می‌کند، اگر نبود `python3` را استفاده می‌کند و اگر
وابستگی‌ها نصب نباشند، دقیقاً دستور نصب را چاپ می‌کند (تست شد، همان پیام را داد).

اگر روی لینوکس **بدون نمایشگر** هستی (سرور، WSL، SSH):
```bash
QT_QPA_PLATFORM=offscreen ./run.sh --list --quiet --category pdf
```

خروجی واقعی اجرای من در همین محیط:

```
$ python main.py --doctor
App                   : WinGenius 5.0.0
Python                : 3.13.16 (x86_64)
OS                    : Linux 6.1.158+
Data folder           : /home/user/.config/WinGenius
Data version          : 2 (current)
✓ ffmpeg              : …/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v9.0.2
✓ tesseract (OCR)     : /usr/bin/tesseract
Tools registered      : 254
```

---

## بدون پنجره: خط فرمان (در ویندوز و لینوکس یکسان)

همه‌ی این‌ها را اجرا کردم و خروجی‌شان را دیدم:

```bat
python main.py --list                              :: فهرست ۲۵۴ ابزار
python main.py --list --json --category pdf        :: خروجی JSON برای اسکریپت
python main.py --describe pdf_to_images            :: فیلدهای یک ابزار + نوعشان
python main.py --run pdf_to_images --file "C:\in.pdf" --set fmt=jpg --set dpi=150 --out "C:\out"
python main.py --plan "عکس‌ها را PDF کن" --file a.png --file b.png --run
python main.py --list-workflows                    :: زنجیره‌های آماده
python main.py --run-workflow pdf_to_images_zip --file in.pdf
python main.py --undo                              :: آخرین عمل را برمی‌گرداند (مثل Ctrl+Z)
python main.py --list-schedules                    :: زمان‌بندی‌ها
python main.py --run-schedule 1
python main.py --macros                            :: کتابخانه‌ی ماکرو
python main.py --run-macro pack.json --dry-run
python main.py --serve --port 8770                 :: API محلی JSON
python main.py --install-sendto / --remove-sendto  :: منوی «ارسال به» ویندوز
```

نمونه‌ی واقعی اجرای من:
```
$ python main.py --run pdf_to_images --file sample.pdf --set fmt=png --set dpi=110 --out out
  → out/sample_page_01.png
  → out/sample_page_02.png
```

### API محلی
`--serve` موقع بالا آمدن یک **توکن** چاپ می‌کند؛ بدون آن پاسخ `401 {"error": "unauthorised"}` است:
```
$ python main.py --serve --port 8770
WinGenius API → http://127.0.0.1:8770   token: LjDNjxEzSl70Pf6npeAX0QdNQx5qpB90

$ curl -H "X-WinGenius-Token: LjDN…" http://127.0.0.1:8770/health
{"app": "WinGenius", "version": "5.0.0", "status": "ok", "tools": 254, "uptime": 5.8,
 "guard": {"rate_limit": 300, "rate_window": 60.0, "rejected": 0, "max_body": 1048576}}
```
توکن را در آدرس هم می‌شود داد (`?token=…`)، ولی هدر تمیزتر است.

---

## اگر خطا گرفتی

| پیام / نشانه | معنی و راه‌حل |
|---|---|
| `ModuleNotFoundError: No module named 'PySide6'` | `.venv` فعال نیست. `\.venv\Scripts\activate` را بزن، بعد `pip install -r requirements.txt` |
| پنجره باز می‌شود ولی فونت فارسی بد است | فونت Vazirmatn همراه برنامه است؛ اگر سیستم فونت را ندارد، در تنظیمات → ظاهر → فونت را عوض کن |
| متن فارسی در کنسول به‌هم‌ریخته | `chcp 65001` (در `run.bat` هست) — یا از GUI استفاده کن |
| `فایل پیدا نشد: …` | مسیر را با گیومه بده و از `\` استفاده کن؛ مسیر باید کامل باشد |
| `401 unauthorised` در API | هدر `X-WinGenius-Token` را از خط شروع سرور بردار و بفرست |
| OCR کار نمی‌کند | `--doctor` را ببین؛ اگر `tesseract` ✗ بود، Tesseract نصب نیست یا در تنظیمات → OCR مسیرش را بده |
| JSON/تنظیمات خراب شده و بالا نمی‌آید | تنظیمات را از «تنظیمات → پاک‌سازی» بازنشانی کن؛ پشتیبان خودکار در پوشه‌ی داده می‌ماند |
| نمی‌خواهی تست/اسکریپت هیچ پنجره‌ای باز کند | `set WINGENIUS_NO_LAUNCH=1` (لینوکس: `export …`) — همه‌ی «باز کن»ها اجرای آزمایشی می‌شوند |
| لاگ برای بررسی | پوشه‌ی داده: `%APPDATA%\WinGenius\logs` (ویندوز) یا `~/.config/WinGenius/logs` (لینوکس). حتی ساده‌تر: `python main.py --run crash_report --set action=create` یک ZIP تمیز و بی‌خطر می‌سازد |

---

## خلاصه‌ی سه خطی (اگر عجله داری)

```bat
python -m venv .venv && .venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```
