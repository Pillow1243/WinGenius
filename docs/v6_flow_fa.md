# موتور جریان (Flow Engine) — راهنمای نسخه‌ی ۶

نسخه‌ی ۶ وینجینیوس ابزارها را از «یک کار در یک زمان» به **خط تولید واقعی** تبدیل می‌کند.
یک *جریان* همان گردش‌کار (workflow) است با چهار کنترل تازه:

| کنترل | کار |
|---|---|
| `foreach` | یک گام را روی **هر فایل** یک پوشه تکرار می‌کند |
| `when` | گام را **شرطی** می‌کند (`{prev} endswith .pdf`) |
| `retries` | گام خراب را چند بار دیگر امتحان می‌کند |
| `on_error` | `stop` (پیش‌فرض) یا `continue` (خطا اجرا را متوقف نکند) |

بیشتر ابزارهای WinGenius یک فایل می‌گیرند؛ با `foreach` همان ابزار روی ۵۰۰ فایل هم کار
می‌کند، و با `when` لازم نیست چند جریان جدا بسازید.

---

## ۱. یک جریان چیست؟

جریان یک فایل JSON ساده است: فهرستی از گام‌ها که هر گام یک ابزار و مقادیرش است.

```json
{
  "id": "pack_photos",
  "name": "بسته‌ی عکس‌ها",
  "name_en": "Photo pack",
  "steps": [
    {
      "tool_id": "image_compress",
      "foreach": "{input}/**/*.{jpg,jpeg,png}",
      "retries": 1,
      "on_error": "continue",
      "values": { "src": ["{item}"], "quality": 80, "max_side": 2000, "out_dir": "{out_dir}" }
    },
    {
      "tool_id": "zip_create",
      "when": "{prev_all} not empty",
      "values": { "src": ["{prev_all}"], "level": "normal" }
    }
  ]
}
```

سه راه برای ساختن جریان دارید:

1. **خودکار:** ابزار **«ساخت خودکار جریان از یک پوشه»** (`flow_build`) محتوای پوشه را
   می‌شناسد (عکس، PDF، سند، ویدیو، صدا) و جریان مناسب را می‌سازد.
2. **از روی نمونه:** جریان‌های آماده (۱۰ عدد) در **«کتابخانه‌ی جریان‌ها»** (`flow_library`) هستند؛
   `flow_export` هرکدام را به یک اسکریپت پایتون مستقل تبدیل می‌کند.
3. **دستی:** فایل JSON را بنویسید — با `flow_validate` قبل از اجرا بازرسی کنید.

---

## ۲. جای‌نگهدارها (Placeholders)

| جای‌نگهدار | معنا |
|---|---|
| `{input}` | اولین فایل/پوشه‌ای که به جریان داده‌اید |
| `{input_all}` | همه‌ی ورودی‌ها (برای فیلدهای چندفایلی) |
| `{prev}` | اولین خروجی گام قبلی |
| `{prev_all}` | همه‌ی خروجی‌های گام قبلی |
| `{prev_dir}` / `{prev_name}` | پوشه / نام گام قبلی |
| `{out_dir}` | پوشه‌ی خروجی تنظیم‌شده |
| `{item}` | **داخل `foreach`:** فایل جاری |
| `{item_name}` / `{item_dir}` / `{item_ext}` | نام، پوشه و پسوند فایل جاری |
| `{item_index}` / `{item_count}` | شماره و تعداد کل (از ۱) |

دو حالت ویژه:

* اگر مقدار یک فیلد **دقیقاً** `{prev_all}` یا `{input_all}` باشد، به فهرست مسیرها تبدیل
  می‌شود (نه رشته).
* داخل `foreach`، الگو می‌تواند glob باشد: `{input}/*.pdf`، `{input}/**/*.pdf` (بازگشتی) و
  حتی `*.{jpg,png}` (آکولادی).

---

## ۳. شرط‌ها (`when`)

بدون `eval` و بدون خطر: یک زبان کوچک و امن.

| نمونه | معنا |
|---|---|
| `{prev} endswith .pdf` | اگر خروجی قبلی PDF بود |
| `{prev_name} contains draft` | اگر نام فایل «draft» داشت |
| `{item_ext} == .png` | پسوند دقیقاً png |
| `{prev_all} not empty` | اگر گام قبلی خروجی داشت |
| `2 < 10` | مقایسه‌ی عددی (اعداد واقعی، نه رشته) |
| `{prev} endswith .pdf and {prev_name} != scan` | ترکیب با `and` |

عملگرها: `==`، `!=`، `>`، `<`، `>=`، `<=`، `contains`، `startswith`/`starts with`،
`endswith`/`ends with`، `exists`، `not exists`، `is empty`، `not empty`، و `not` در ابتدا.
ترکیب `and`/`or`/`&&`/`||` **از چپ به راست** ارزیابی می‌شود (`a or b and c` یعنی `(a or b) and c`).

> مقدار خالی، `0`، `false`، `no` و `[]` «نادرست» حساب می‌شوند.

---

## ۴. خطا، تلاش مجدد و ادامه

```json
{ "tool_id": "pdf_compress", "foreach": "{input}/*.pdf", "retries": 2,
  "on_error": "continue", "values": { "src": "{item}", "mode": "balanced" } }
```

* `retries: 2` یعنی تا ۳ بار تلاش (اصلی + ۲). برای I/O لرزان (شبکه، USB) مفید است.
* با `on_error: "continue"` یک فایل خراب بقیه‌ی جریان را نمی‌خواباند؛ وضعیت نهایی جریان
  `partial` می‌شود و در CLI کد خروج ۱ می‌گیرید.
* پیش‌فرض `stop` است: جریان در همان گام متوقف می‌شود و پیام می‌دهد.

---

## ۵. اجرا

### در برنامه

* دسته‌ی **«جریان هوشمند»** در منوی رادیالی ۶ ابزار دارد: اجرا، ساخت خودکار، بازرسی، توضیح،
  خروجی اسکریپت و کتابخانه.
* در **گردش‌کارها** (`Ctrl+3`) گام‌های شرطی/حلقه‌دار با نشان `برای هر …` و «اگر …» دیده می‌شوند.

### در خط فرمان

```bash
python main.py --flows                                  # فهرست جریان‌ها
python main.py --explain-flow photo_pdf_pack --lang en   # چه می‌کند؟
python main.py --lint-flow my_flow.json                  # فقط بازرسی
python main.py --run-flow my_flow.json --file C:\in\ --out C:\out --json
python main.py --run-workflow pdf_to_images_zip --file in.pdf   # جریان‌های قدیمی هم کار می‌کنند
```

### در API محلی

ابزارها از راه API هم در دسترس‌اند (بدون تغییر در قرارداد):

```bash
curl -X POST http://127.0.0.1:8770/run -H "Authorization: Bearer <token>" \
     -d '{"tool":"flow_run","values":{"flow":"photo_pdf_pack","inputs":["C:/in"]}}'
```

### به‌عنوان اسکریپت مستقل

`flow_export` یک فایل `.py` می‌سازد که همان موتور را صدا می‌زند:

```bash
python pack_photos.py C:\photos
```

---

## ۶. دستور پخت‌های نمونه

### الف) پوشه‌ی دانلود را مرتب کن

```
foreach  {input}/**/*.{jpg,png,webp}   → image_compress (quality 80, max_side 2000)
foreach  {input}/**/*.pdf              → pdf_compress   (mode balanced)
when     {prev_all} not empty          → zip_create      ({prev_all})
```

### ب) PDFهای اسکن‌شده را جست‌وجوپذیر کن (فقط اگر PDF باشد)

```
foreach {input}/**/*.pdf → pdf_ocr (fas+eng, out_text, out_pdf, retries 1, on_error continue)
foreach {input}/**/*.png → image_ocr (فقط اگر پیش‌فرض لازم بود)
```

### ج) پیش از فرستادن عکس‌ها، حریم خصوصی را پاک کن

```
foreach {input}/**/*.{jpg,jpeg} → image_metadata_strip  (EXIF/GPS حذف)
foreach {input}/**/*.pdf        → pdf_flatten_annotations (یادداشت‌ها حذف)
                                → zip_create
```

---

## ۷. بازرسی (`flow_validate` / `flow_lint`)

نگهبان‌ها قبل از وقت‌کشیِ شما:

* ابزار ناشناخته (خطا)
* فیلد لازمِ خالی (خطا)
* جریان بدون گام یا بدون ورودی (خطا)
* جای‌نگهدار ناشناخته، `foreach` بی‌اثر (بدون `{item}`)، الگوی بدون glob، شرط بدون جای‌نگهدار،
  ابزار خطرناک (هشدار)

نمونه‌ی خروجی:

```
1 error(s) · 2 warning(s) · 3 step(s)
✖ step 2: Field 'PDF file' of 'Compress PDF' is empty.
! step 1: The step loops over items but never uses {item}; it may repeat the same work.
```

---

## ۸. محدودیت‌های صادقانه

* `foreach` روی هر مورد **یکی‌یکی** اجرا می‌شود (موازی‌سازی در نقشه‌ی نسخه‌ی بعد است).
* زمان‌بند و Watcher هنوز جریان‌های v6 را با همه‌ی کنترل‌ها اجرا می‌کنند، ولی گزارش
  گام‌به‌گام آن‌ها در ژورنال خلاصه می‌شود.
* `when` عمداً «کوتاه» است: عبارت‌های پیچیده (ریاضی، پرانتز) پشتیبانی نمی‌شوند. برای منطق
  سنگین از ماکرو (`macro_run`) استفاده کنید.
* جریان‌ها مقادیر حساس (رمز، توکن) را در فایل ذخیره نمی‌کنند؛ دوباره باید وارد شوند.

---

## ۹. عیب‌یابی

| نشانه | علت و راه‌حل |
|---|---|
| `nothing to run (empty foreach)` | الگو چیزی پیدا نکرد؛ مسیر `{input}` را بررسی کنید (glob حساس به جداکننده است). |
| همه‌چیز `skipped` | شرط‌ها نادرست‌اند؛ با `--lint-flow` جای‌نگهدارها را ببینید. |
| `partial` | یک گام با `on_error: continue` شکست خورده؛ جزئیات در پیام همان گام است. |
| خروجی خالی | گام‌ها فایلی نساختند؛ فیلد «ذخیره در» یا `out_dir` را بدهید. |

---

بازگشت به فهرست اصلی: [`../README.md`](../README.md) · ساختار کد: [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
