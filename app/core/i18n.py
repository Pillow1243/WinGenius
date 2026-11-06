"""Tiny two-language (Persian / English) string table for the shell UI."""
from __future__ import annotations

import re
from typing import Any, Dict

# Persian/Arabic letters only: Persian and Arabic-Indic *digits* are legitimate
# inside numbers and labels, so they are deliberately excluded.
ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u066E-\u06D3\u067E\u0686\u0698\u06A9\u06AF\u06CC]")

STRINGS: Dict[str, Dict[str, str]] = {
    # app shell
    "app.title": {"fa": "وین‌جنیوس", "en": "WinGenius"},
    "app.subtitle": {"fa": "دستیار همه‌کاره‌ی ویندوز", "en": "Your all-in-one Windows assistant"},
    "home.hint": {"fa": "یک دسته را انتخاب کنید", "en": "Pick a category"},
    "home.search": {"fa": "جست‌وجو / دستور… (Ctrl+K)", "en": "Search or command… (Ctrl+K)"},
    "center.home": {"fa": "خانه", "en": "Home"},
    "center.back": {"fa": "بازگشت", "en": "Back"},
    "center.tools": {"fa": "ابزار", "en": "tools"},
    "node.favorite": {"fa": "برگزیده", "en": "Favorite"},
    "page.of": {"fa": "صفحه {0} از {1}", "en": "Page {0} of {1}"},
    # toolbar
    "tb.minimize": {"fa": "کوچک کردن", "en": "Minimize"},
    "tb.close": {"fa": "بستن", "en": "Close"},
    "tb.settings": {"fa": "تنظیمات", "en": "Settings"},
    "tb.lang": {"fa": "EN", "en": "فا"},
    "tb.theme": {"fa": "پوسته", "en": "Theme"},
    # tool panel
    "tp.run": {"fa": "اجرا", "en": "Run"},
    "tp.cancel": {"fa": "لغو", "en": "Cancel"},
    "tp.close": {"fa": "بستن", "en": "Close"},
    "tp.browse": {"fa": "انتخاب…", "en": "Browse…"},
    "tp.browse_dir": {"fa": "انتخاب پوشه…", "en": "Choose folder…"},
    "tp.save_as": {"fa": "ذخیره در…", "en": "Save as…"},
    # Placeholders show an *example path*; the button next to the field already
    # says "Browse…", so repeating it inside the field was pure noise.
    "tp.path_file": {"fa": "مثال: C:\\Users\\me\\Document.pdf", "en": "e.g. C:\\Users\\me\\Document.pdf"},
    "tp.path_files": {"fa": "یک یا چند فایل — برای افزودن دوباره بزنید", "en": "one or more files — press Browse again to add"},
    "tp.path_dir": {"fa": "مثال: C:\\Users\\me\\Output", "en": "e.g. C:\\Users\\me\\Output"},
    "tp.path_save": {"fa": "مثال: C:\\Users\\me\\result.pdf", "en": "e.g. C:\\Users\\me\\result.pdf"},
    "tp.ready": {"fa": "آماده", "en": "Ready"},
    "tp.running": {"fa": "در حال اجرا…", "en": "Running…"},
    "tp.done": {"fa": "پایان یافت", "en": "Finished"},
    "tp.failed": {"fa": "خطا", "en": "Failed"},
    "tp.open_output": {"fa": "باز کردن خروجی", "en": "Open output"},
    "tp.show_in_folder": {"fa": "نمایش در پوشه", "en": "Show in folder"},
    "tp.copy": {"fa": "کپی", "en": "Copy"},
    "tp.copied": {"fa": "کپی شد", "en": "Copied"},
    "tp.log": {"fa": "گزارش", "en": "Log"},
    "tp.clear": {"fa": "پاک کردن", "en": "Clear"},
    "tp.output_dir": {"fa": "پوشه‌ی خروجی", "en": "Output folder"},
    "tp.favorite": {"fa": "افزودن به برگزیده‌ها", "en": "Add to favorites"},
    "tp.unfavorite": {"fa": "حذف از برگزیده‌ها", "en": "Remove from favorites"},
    "tp.required": {"fa": "این فیلد الزامی است", "en": "This field is required"},
    "tp.not_found": {"fa": "مسیر پیدا نشد: {0}", "en": "Path not found: {0}"},
    "tp.confirm_danger": {
        "fa": "این عملیات غیرقابل بازگشت است. ادامه می‌دهید؟",
        "en": "This cannot be undone. Continue?",
    },
    # search / assistant
    "as.title": {"fa": "دستیار", "en": "Assistant"},
    "as.placeholder": {
        "fa": "مثلاً: پی‌دی‌اف را به عکس تبدیل کن، حجم عکس را کم کن، پسورد بساز…",
        "en": "e.g. pdf to images, compress image, generate password…",
    },
    "as.no_result": {"fa": "ابزاری پیدا نشد. کلمه‌ی دیگری امتحان کنید.", "en": "No tool found. Try another word."},
    "as.best": {"fa": "پیشنهاد دستیار", "en": "Assistant suggestion"},
    "as.results": {"fa": "نتایج", "en": "Results"},
    "as.open": {"fa": "باز کردن", "en": "Open"},
    "as.esc": {"fa": "برای بستن Esc", "en": "Esc to close"},
    # settings
    "st.title": {"fa": "تنظیمات", "en": "Settings"},
    "st.language": {"fa": "زبان", "en": "Language"},
    "st.theme": {"fa": "پوسته", "en": "Theme"},
    "st.accent": {"fa": "رنگ تأکیدی", "en": "Accent color"},
    "st.animations": {"fa": "انیمیشن‌ها", "en": "Animations"},
    "st.glow": {"fa": "درخشش نئونی", "en": "Neon glow"},
    "st.confirm": {"fa": "تأیید قبل از عملیات خطرناک", "en": "Confirm dangerous actions"},
    "st.output": {"fa": "پوشه‌ی خروجی پیش‌فرض", "en": "Default output folder"},
    "st.open_result": {"fa": "باز کردن خودکار نتیجه", "en": "Open result automatically"},
    "st.ocr_index": {"fa": "خواندن متن PDFهای اسکن‌شده در ایندکس (OCR)",
                     "en": "OCR scanned PDFs into the search index"},
    "st.quick_actions": {"fa": "میان‌بر «ارسال به» ویندوز", "en": "Windows “Send to” shortcut"},
    "st.install_sendto": {"fa": "نصب", "en": "Install"},
    "st.remove_sendto": {"fa": "حذف", "en": "Remove"},
    "st.sendto_missing": {"fa": "این گزینه فقط در ویندوز معنی دارد.",
                          "en": "This only means something on Windows."},
    "st.about": {"fa": "درباره", "en": "About"},
    "st.logs": {"fa": "باز کردن پوشه‌ی لاگ", "en": "Open log folder"},
    "st.about_text": {
        "fa": "وین‌جنیوس {0} — ساخته‌شده با PySide6. بدون تله‌متری و بدون حساب کاربری؛ پردازش فایلها محلی است. ابزارهای مشخص‌شده‌ی شبکه (دانلود، WHOIS، بررسی نشت رمز، دانلود بسته‌ی زبان و مدل گفتار) فقط با اجرای خودتان به اینترنت وصل می‌شوند.",
        "en": "WinGenius {0} — built with PySide6. No telemetry, no account, and all file processing is local. Only the explicitly network-enabled tools (download, WHOIS, breach check, OCR language packs and speech models) contact the internet, and only when you run them.",
    },
    "st.close": {"fa": "بستن", "en": "Close"},
    "st.reset": {"fa": "بازنشانی تنظیمات", "en": "Reset settings"},
    # job queue drawer
    "jb.title": {"fa": "صف کارها", "en": "Job queue"},
    "jb.subtitle": {
        "fa": "هر اجرا در پس‌زمینه انجام می‌شود؛ می‌توانید همین‌جا چند کار را هم‌زمان پیش ببرید، لغو کنید یا نتیجه را باز کنید.",
        "en": "Every run happens in the background. Start several, cancel or open results from here.",
    },
    "jb.empty": {"fa": "هنوز کاری اجرا نشده. یک ابزار را باز کنید و اجرا بزنید (یا Ctrl+Enter).",
                 "en": "No jobs yet. Open a tool and press Run (or Ctrl+Enter)."},
    "jb.cancel": {"fa": "لغو", "en": "Cancel"},
    "jb.again": {"fa": "اجرای دوباره", "en": "Run again"},
    "jb.clear": {"fa": "پاک کردن تمام‌شده‌ها", "en": "Clear finished"},
    "jb.badge": {"fa": "{0} در حال اجرا", "en": "{0} running"},
    # smart drop
    "dz.title": {"fa": "فایل را همین‌جا رها کنید", "en": "Drop files here"},
    "dz.hint": {"fa": "بر اساس نوع فایل، بهترین ابزارها را پیشنهاد دادیم.",
                "en": "These tools fit the file types you dropped."},
    "dz.browse": {"fa": "انتخاب فایل از دیسک", "en": "Browse for files"},
    "dz.all": {"fa": "همه‌ی ابزارها…", "en": "All tools…"},
    "dz.none": {"fa": "برای این نوع فایل پیشنهاد ویژه‌ای نداریم؛ از «همه‌ی ابزارها» استفاده کنید.",
                "en": "No specific match for these files — try 'All tools'."},
    "dz.received": {"fa": "{0} فایل دریافت شد", "en": "{0} file(s) received"},
    # tool panel extras
    "tp.queue": {"fa": "اجرا در پس‌زمینه", "en": "Run in background"},
    "tp.queued": {"fa": "به صف اضافه شد — در «صف کارها» ببینید", "en": "Queued — see Job queue"},
    "tp.prefilled": {"fa": "فیلدها از فایل‌های رهاشده پر شد", "en": "Fields filled from dropped files"},
    "tp.copy_cli": {"fa": "کپی دستور خط فرمان", "en": "Copy CLI command"},
    # commands / palette
    "cmd.jobs": {"fa": "صف کارها", "en": "Job queue"},
    "cmd.doctor": {"fa": "بررسی سلامت و وابستگی‌ها", "en": "Run diagnostics"},
    "cmd.plugins": {"fa": "پوشه‌ی افزونه‌ها", "en": "Plugins folder"},
    "cmd.history": {"fa": "تاریخچه‌ی اجراها", "en": "Run history"},
    "cmd.cli": {"fa": "کپی دستور CLI", "en": "Copy CLI command"},
    "cmd.dashboard": {"fa": "داشبورد", "en": "Dashboard"},
    "cmd.plan": {"fa": "دستیار برنامه‌ریز", "en": "Planner assistant"},
    "cmd.workflows": {"fa": "گردش‌کارها", "en": "Workflows"},
    "cmd.automation": {"fa": "خودکارسازی پوشه", "en": "Folder automation"},
    "cmd.index": {"fa": "جست‌وجو در فایل‌ها", "en": "Search inside files"},
    "cmd.scheduler": {"fa": "زمان‌بند و ماکرو", "en": "Scheduler & macros"},
    "cmd.undo": {"fa": "بازگردانی آخرین عملیات", "en": "Undo last operation"},
    "cmd.api": {"fa": "API محلی: روشن/خاموش", "en": "Local API: on/off"},
    "cmd.vault": {"fa": "گاوصندوق رمزدار", "en": "Encrypted vault"},
    "cmd.update": {"fa": "بررسی و نصب به‌روزرسانی", "en": "Check & install updates"},
    "cmd.section": {"fa": "دستورها", "en": "Commands"},
    "cmd.recent": {"fa": "اخیر", "en": "Recent"},
    # settings: updates section
    "st.updates": {"fa": "به‌روزرسانی برنامه", "en": "Application updates"},
    "st.auto_update": {
        "fa": "بررسی خودکار نسخه‌ی جدید هنگام شروع برنامه",
        "en": "Automatically check for updates on startup",
    },
    "st.check_update_btn": {
        "fa": "بررسی و نصب نسخه‌ی جدید…",
        "en": "Check & install updates…",
    },
    "st.rollback_btn": {
        "fa": "بازگشت به نسخه‌ی قبلی ({0})",
        "en": "Rollback to previous version ({0})",
    },
    # settings: system section
    "st.system": {"fa": "سیستم و عیب‌یابی", "en": "System and diagnostics"},
    "st.doctor": {"fa": "گزارش کامل وضعیت", "en": "Full status report"},
    "st.plugins": {"fa": "افزونه‌ها", "en": "Plugins"},
    "st.plugins_open": {"fa": "باز کردن پوشه‌ی افزونه‌ها", "en": "Open plugins folder"},
    "st.plugins_example": {"fa": "ساخت افزونه‌ی نمونه", "en": "Create example plugin"},
    "st.plugins_reload": {"fa": "بارگذاری دوباره", "en": "Reload plugins"},
    "st.parallel": {"fa": "تعداد کارهای هم‌زمان", "en": "Parallel jobs"},
    "st.history_clear": {"fa": "پاک کردن تاریخچه", "en": "Clear history"},
    "st.copied": {"fa": "کپی شد", "en": "Copied"},
    "st.plugins_count": {"fa": "{0} افزونه بارگذاری شد", "en": "{0} plugin(s) loaded"},
    # toast / errors
    "err.title": {"fa": "خطا", "en": "Error"},
    "err.unexpected": {"fa": "خطای غیرمنتظره", "en": "Unexpected error"},
    "err.missing_dep": {
        "fa": "کتابخانه‌ی «{0}» نصب نیست. دستور «pip install {1}» را اجرا کنید.",
        "en": "The '{0}' package is missing. Run 'pip install {1}'.",
    },
    "err.windows_only": {
        "fa": "این ابزار فقط روی ویندوز کار می‌کند.",
        "en": "This tool only works on Windows.",
    },
    "err.admin": {
        "fa": "این ابزار به دسترسی مدیر (Run as administrator) نیاز دارد.",
        "en": "This tool needs administrator rights.",
    },
    "toast.output_copied": {"fa": "مسیر در کلیپ‌بورد کپی شد", "en": "Path copied to clipboard"},
    "toast.no_output": {"fa": "خروجی‌ای وجود ندارد", "en": "No output yet"},
    "toast.saved": {"fa": "ذخیره شد", "en": "Saved"},
    "warn.admin_start": {
        "fa": "برای برخی ابزارها (پاک‌سازی سیستم، سرویس‌ها) اجرای برنامه به‌عنوان مدیر لازم است.",
        "en": "Some tools (system cleanup, services) need the app to run as administrator.",
    },
}

_current = "fa"


def set_language(lang: str) -> None:
    global _current
    _current = "en" if lang == "en" else "fa"


def language() -> str:
    return _current


def t(key: str, *args: Any) -> str:
    entry = STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_current) or entry.get("fa") or key
    if args:
        try:
            return text.format(*args)
        except (IndexError, KeyError):
            return text
    return text
