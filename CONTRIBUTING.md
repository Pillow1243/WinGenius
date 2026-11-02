# راهنمای مشارکت در WinGenius

ممنون که وقت می‌گذاری. سه قاعده‌ی این پروژه ساده است:

1. **فارسی اول** — متن رابط، پیام‌های خطا، README و یادداشت نسخه فارسی‌اند؛ انگلیسی ترجمه‌ی دوم است.
2. **هر ادعا با اجرا** — هیچ تست یا قابلیتی «فرضی» ثبت نمی‌شود. اگر تستی اضافه می‌کنی، باید در همین
   مخزن اجرا شود و نتیجه‌اش را در PR بنویسی.
3. **RTL-safe** — هر ویجت تازه باید در حالت راست‌به‌چپ و چپ‌به‌راست درست دیده شود و نام دسترس‌پذیر
   (`setAccessibleName`) داشته باشد.

## راه‌اندازی محیط

```bash
python -m venv .venv
.venv\Scripts\activate          # ویندوز   |   source .venv/bin/activate در لینوکس/مک
pip install -r requirements.txt
pip install ruff coverage
python main.py --doctor         # باید ۲۵۴ ابزار و همه‌ی کتابخانه‌ها را ✓ نشان دهد
```

راهنمای کامل: [`docs/run_fa.md`](docs/run_fa.md).

## افزودن یک ابزار تازه

1. یک ماژول در `app/tools/` بساز (یا به ماژول دسته‌ی مربوط اضافه کن) و با دکوراتور `@tool(...)`
   اعلان کن؛ `name`/`name_en`/`desc`/`desc_en` هر چهار لازم‌اند.
2. **نام ماژول را در لیست `app/tools/__init__.py` اضافه کن** — اگر این کار را نکنی، ابزار در
   رجیستری `None` برمی‌گرداند (این اشتباه یک‌بار اتفاق افتاد و ثبت شده است).
3. به `tests/output_test.py` یک بررسی «آیا فایل خروجی درست است؟» اضافه کن، نه فقط «اجرا شد».
4. اگر ابزار حالت خطرناک دارد، `danger=True` بگذار تا رابط تأیید بگیرد و ژورنال بازگردانی ثبت کند.

## قبل از ارسال PR

```bash
ruff check main.py app tests packaging
QT_QPA_PLATFORM=offscreen python tests/smoke_test.py
QT_QPA_PLATFORM=offscreen python tests/functional_test.py
QT_QPA_PLATFORM=offscreen python tests/output_test.py
QT_QPA_PLATFORM=offscreen python tests/ui_test.py
python packaging/verify_package.py
```

هر پنج مورد باید سبز باشند. اگر شمارشی در README عوض شد (تعداد ابزار، تعداد بررسی)، همان را در
`README.md`، `docs/README.en.md` و `docs/showcase.html` هم به‌روز کن.

## سبک کد

- خط حداکثر ۱۰۰ کاراکتر · importها مرتب · بدون وابستگی سنگین تازه بدون توضیح در PR.
- وابستگی‌های اختیاری (Whisper، pyHanko، Tesseract، dnspython) باید بدون نصب‌بودن هم برنامه را سالم
  نگه دارند و پیام نصب فارسی بدهند.
- هیچ مسیر یا رمز کاربر در لاگ/گزارش ننشیند.

## گزارش باگ

از قالب Issues استفاده کن و اگر می‌توانی خروجی `python main.py --run crash_report --set action=create`
را پیوست کن (خودش مسیرها و رمزها را حذف می‌کند).
