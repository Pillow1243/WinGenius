# نصب و اجرای WinGenius 5.0

سه راه دارید. اگر فقط می‌خواهید برنامه را ببینید، **راه ۲ (اجرا از سورس)** سریع‌ترین و
مطمئن‌ترین است؛ اگر برای دیگران می‌خواهید، **راه ۳** یک فایل `.exe` می‌سازد.

---

## راه ۱ — نصب‌کننده‌ی آماده (فقط اگر از قبل ساخته شده باشد)

فایل `WinGenius-Setup-5.0.0.exe` را اجرا کنید: نصب در `%LOCALAPPDATA%\Programs\WinGenius`،
ساخت میان‌بر در Start Menu و دسکتاپ (اختیاری)، و صفحه‌ی مجوز AGPL در همان مرحله.

> در این پروژه فعلاً پوشه‌ی `dist/` وجود ندارد، چون `.exe` فقط **روی ویندوز** ساخته می‌شود و
> کار ما در محیط لینوکس انجام شده. برای ساختنش راه ۳ را ببینید.

---

## راه ۲ — اجرا از سورس (تأییدشده)

نیازمندی: **پایتون ۳.۱۱ یا بالاتر، ۶۴ بیتی** (ویندوز/لینوکس/مک).

### ویندوز (PowerShell)

```powershell
cd C:\path\to\WinGenius

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # اگر خطای ExecutionPolicy داد:
                                      # Set-ExecutionPolicy -Scope Process RemoteSigned

python -m pip install --upgrade pip
pip install -r requirements.txt

python main.py                        # اجرای برنامه
python main.py --self-test            # آزمون سریع بدون باز کردن پنجره
```

### لینوکس / مک

```bash
cd /path/to/WinGenius
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# فقط لینوکس — کتابخانه‌های سیستمی که Qt لازم دارد:
sudo apt install libxkbcommon0 libegl1 libglib2.0-0 libdbus-1-3 libfontconfig1

python main.py
```

> بدون آن بسته‌ها Qt با خطای `ImportError: libxkbcommon.so.0` بالا نمی‌آید — این تنها
> اصطکاک واقعی روی لینوکس است.

**آنچه در یک venv کاملاً تازه تست شد:** نصب همه‌ی `requirements.txt` بدون خطا، سپس
`python main.py --self-test` با کد خروج `0`، `python main.py --version` → `WinGenius 5.0.0`
و بارگذاری **۲۵۴ ابزار** (`--list`).

### اگر فایل اجرایی می‌خواهید ولی نمی‌خواهید بیلد کنید

```powershell
python main.py --list                      # فهرست ابزارها
python main.py --run pdf_to_images --file "C:\in\a.pdf" --set fmt=jpg
python main.py --serve --port 8770         # API محلی (توکن در %APPDATA%\WinGenius\api_token.txt)
```

روی ویندوز می‌توانید یک فایل `WinGenius.bat` کنار پروژه بسازید:

```bat
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
python main.py %*
```

---

## راه ۳ — ساختن exe و نصب‌کننده (روی ویندوز)

```powershell
cd C:\path\to\WinGenius
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-build.txt      # شامل PyInstaller

# ساده‌ترین راه:
packaging\build.bat

# یا با کنترل بیشتر:
python packaging\build_exe.py              # dist\WinGenius.exe (تک‌فایل)
python packaging\build_exe.py --onedir     # dist\WinGenius\ (سریع‌تر بالا می‌آید)
python packaging\build_exe.py --clean      # پاک‌سازی build/dist قبل از ساخت
python packaging\build_exe.py --installer  # + نصب‌کننده (نیاز به Inno Setup 6)
```

| خروجی | مسیر |
|---|---|
| برنامه‌ی تک‌فایل | `dist\WinGenius.exe` |
| حالت پوشه‌ای | `dist\WinGenius\WinGenius.exe` |
| نصب‌کننده | `dist\WinGenius-Setup-5.0.0.exe` |

Inno Setup 6 را از <https://jrsoftware.org/isdl.php> نصب کنید (یا `winget install JRSoftware.InnoSetup`)
و مطمئن شوید `iscc.exe` در `PATH` است.

قبل از انتشار، بررسی فایل‌های ساخت:

```powershell
python packaging\verify_package.py         # ۲۷ بررسی: نسخه، spec، installer، آیکون، مجوز
```

---

## وابستگی‌های اختیاری (برنامه بدون آن‌ها هم بالا می‌آید)

| قابلیت | چه نصب کنید | اگر نباشد |
|---|---|---|
| **OCR** (PDF اسکن، عکس) | `winget install UB-Mannheim.TesseractOCR` و سپس `pip install pytesseract` | پیام راهنمای نصب نشان داده می‌شود، نه کرش |
| OCR **فارسی** | در برنامه: ابزار **«راه‌اندازی OCR»** → «دانلود بسته‌ی زبان» → فارسی (نیازی به کار دستی نیست) | OCR فقط انگلیسی کار می‌کند |
| **گفتار به متن / زیرنویس** | `pip install faster-whisper` (مدل بار اول خودش دانلود می‌شود) | پیام نصب دقیق نشان داده می‌شود |
| **امضای دیجیتال PDF** | `pip install pyhanko` + گواهی `.p12/.pfx` خودتان | پیام نصب می‌دهد؛ بقیه‌ی برنامه سالم کار می‌کند |
| **جست‌وجو در PDFهای اسکن‌شده** | همان Tesseract بالا (روشن/خاموش از تنظیمات) | اسکن‌ها ایندکس نمی‌شوند |
| **ffmpeg** (صدا/ویدیو) | با `imageio-ffmpeg` در همان `pip install` می‌آید | ابزارهای مدیا پیام نصب می‌دهند |
| تبدیل **Word/Excel/PowerPoint → PDF** | Microsoft Office نصب باشد (`pywin32` در requirements هست) | ابزار پیام خطا می‌دهد |
| **LibreOffice** (جایگزین آفیس) | `winget install TheDocumentFoundation.LibreOffice` | همان بالا |

---

## ادغام با ویندوز و حالت پرتابل (نسخه‌ی ۴.۲)

```powershell
# راست‌کلیک روی هر فایل → Send to → WinGenius
WinGenius.exe --install-sendto
WinGenius.exe --remove-sendto

# داده‌ها کنار exe (فلش، سیستم مشترک) به‌جای %APPDATA%
WinGenius.exe --portable
# یا: set WINGENIUS_PORTABLE=1
# یا: یک فایل خالی portable.flag کنار exe
```

از خط فرمان هم می‌شود فایل‌ها را مستقیم به صفحه‌ی پیشنهاد فرستاد:

```powershell
WinGenius.exe --sendto "C:\in\report.pdf" "C:\in\photo.jpg"
```

---

## اولین اجرا

- پنجره بدون قاب باز می‌شود؛ نوار بالا: جست‌وجو (`Ctrl+K`)، دکمه‌ی `EN` برای رابط انگلیسی،
  صف کارها، و منوی دایره‌ای دسته‌ها.
- داده‌ها در `%APPDATA%\WinGenius` (لینوکس: `~/.config/WinGenius`) ذخیره می‌شوند:
  `settings.ini`، ژورنال SQLite، زمان‌بندها، ماکروها، `logs/`، `backups/` و `schema.json`.
  همین نسخه اول، پوشه را نسخه‌دار می‌کند و از این به بعد قبل از هر مهاجرت پشتیبان می‌گیرد.
- اگر دو نسخه باز کنید، دومی پنجره‌ی اولی را جلو می‌آورد و خودش بسته می‌شود
  (برای غیرفعال‌کردن: `--allow-multi`).
- شبکه لازم نیست؛ هیچ داده‌ای ارسال نمی‌شود.

---

## عیب‌یابی

| پیام | علت و راه‌حل |
|---|---|
| `ModuleNotFoundError: No module named 'PySide6'` | venv فعال نیست یا `pip install -r requirements.txt` اجرا نشده |
| `ImportError: libxkbcommon.so.0` (لینوکس) | بسته‌های سیستمی Qt را نصب کنید (بالا) |
| `An unexpected error occurred` هنگام اجرا | `%APPDATA%\WinGenius\logs\` را ببینید؛ نسخه‌ی پایتون باید ≥ ۳.۱۱ باشد |
| ویندوز هشدار SmartScreen می‌دهد | exe امضای دیجیتال ندارد؛ «More info → Run anyway» یا خودتان کد را از سورس اجرا کنید |
| `Another instance is already running` | نسخه‌ی اول باز است؛ یا آن را ببندید یا `--allow-multi` |
| OCR می‌گوید زبان پیدا نشد | `fas.traineddata` را در پوشه‌ی `tessdata` بگذارید |
| ابزارهای ویدیو کار نمی‌کنند | `pip install imageio-ffmpeg` (داخل venv) یا ffmpeg سیستمی در `PATH` |
| متن فارسی در ترمینال به‌هم‌ریخته | `chcp 65001` در cmd؛ یا از خروجی `--json` استفاده کنید |

---

## حذف برنامه

- نسخه‌ی نصب‌شده: Settings → Apps → WinGenius → Uninstall.
- داده‌ها را هم می‌خواهید پاک کنید: پوشه‌ی `%APPDATA%\WinGenius` را حذف کنید
  (ژورنال، تنظیمات، گاوصندوق‌ها و پشتیبان‌ها همان‌جا هستند).
- از سورس: کافی است پوشه‌ی پروژه و venv را پاک کنید.
