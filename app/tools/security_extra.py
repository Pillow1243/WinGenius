"""Security intelligence: password audit, secret scanning, checksums, breach check."""
from __future__ import annotations

import hashlib
import math
import re
import string
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_PASSWORD, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, walk_files
from ._helpers import reject_disk_root
from ._helpers import ensure_dir, files_or_raise, first_file, out_dir, require

IS_WINDOWS = sys.platform.startswith("win")

COMMON_PASSWORDS = {
    "password", "123456", "123456789", "qwerty", "12345678", "111111", "1234567", "sunshine",
    "qwerty123", "1q2w3e", "123123", "admin", "letmein", "welcome", "monkey", "dragon",
    "iloveyou", "princess", "football", "654321", "abc123", "password1", "1234", "12345",
    "1234567890", "000000", "qwertyuiop", "asdfgh", "zxcvbn", "iran", "tehran", "welcome1",
}

SECRET_PATTERNS: List[Tuple[str, str]] = [
    ("AWS access key", r"AKIA[0-9A-Z]{16}"),
    ("AWS secret key", r"(?i)aws(.{0,20})?['\"][0-9a-zA-Z/+]{40}['\"]"),
    ("Google API key", r"AIza[0-9A-Za-z\-_]{35}"),
    ("GitHub token", r"gh[pousr]_[0-9A-Za-z]{20,}"),
    ("Slack token", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("Stripe key", r"sk_live_[0-9a-zA-Z]{16,}"),
    ("Private key block", r"-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----"),
    ("JWT", r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("Password assignment", r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\s]{4,}['\"]"),
    ("Generic API key", r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][0-9A-Za-z\-_]{12,}['\"]"),
    ("Iranian national ID", r"(?<!\d)\d{10}(?!\d)"),
    ("Iranian mobile number", r"(?<!\d)09\d{9}(?!\d)"),
    ("Email address", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
]

SCAN_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yml", ".yaml", ".ini", ".cfg", ".conf",
    ".env", ".php", ".rb", ".go", ".java", ".cs", ".sql", ".sh", ".bat", ".ps1", ".xml",
    ".html", ".css", ".properties", ".toml",
}


@tool(
    id="password_audit", category="security", name="سنجش قدرت رمز عبور",
    name_en="Password audit",
    desc="قدرت رمز را با آنتروپی واقعی می‌سنجد، نقاط ضعف را می‌گوید و نسخه‌ی قوی‌تر پیشنهاد می‌دهد.",
    desc_en="Score a password by real entropy, list its weaknesses and suggest a stronger one.",
    icon="lock", order=100,
    keywords=("password strength", "entropy", "قدرت رمز", "رمز قوی", "امنیت رمز"),
    fields=[
        Field("password", K_PASSWORD, "رمز عبور", "Password", required=True,
              help="رمز فقط در همین رایانه بررسی می‌شود و جایی ارسال نمی‌شود.",
              help_en="Checked locally — never sent anywhere."),
    ],
)
def password_audit(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    password = str(v.get("password") or "")
    if not password:
        raise ToolError(ctx.M("رمز را وارد کنید.", "Enter a password."))
    pools = 0
    if any(ch in string.ascii_lowercase for ch in password):
        pools += 26
    if any(ch in string.ascii_uppercase for ch in password):
        pools += 26
    if any(ch.isdigit() for ch in password):
        pools += 10
    if any(ch in string.punctuation for ch in password):
        pools += 32
    if any(ord(ch) > 127 for ch in password):
        pools += 128
    entropy = len(password) * math.log2(pools) if pools else 0
    if password.lower() in COMMON_PASSWORDS:
        entropy = min(entropy, 12)
    issues: List[str] = []
    if len(password) < 12:
        issues.append(ctx.M("کوتاه‌تر از ۱۲ نویسه است", "shorter than 12 characters"))
    if password.lower() in COMMON_PASSWORDS:
        issues.append(ctx.M("یکی از رمزهای بسیار رایج است", "is one of the most common passwords"))
    if re.search(r"(.)\1{2,}", password):
        issues.append(ctx.M("نویسه‌های تکراری پشت سر هم دارد", "has repeated characters"))
    if re.search(r"(0123|1234|abcd|qwer|9876)", password.lower()):
        issues.append(ctx.M("دنباله‌ی قابل حدس دارد", "contains a guessable sequence"))
    if password.isdigit():
        issues.append(ctx.M("فقط عدد است", "digits only"))
    if password.isalpha():
        issues.append(ctx.M("فقط حرف است", "letters only"))
    if password.lower() == password and password.isalpha():
        issues.append(ctx.M("حروف کوچک تنها", "lowercase only"))
    years = re.search(r"(19|20)\d{2}", password)
    if years:
        issues.append(ctx.M("شامل یک سال است", "contains a year"))
    if entropy >= 90:
        label, color = ctx.M("بسیار قوی", "Very strong"), "🟢"
    elif entropy >= 70:
        label, color = ctx.M("قوی", "Strong"), "🟢"
    elif entropy >= 50:
        label, color = ctx.M("متوسط", "Moderate"), "🟡"
    elif entropy >= 32:
        label, color = ctx.M("ضعیف", "Weak"), "🟠"
    else:
        label, color = ctx.M("بسیار ضعیف", "Very weak"), "🔴"
    crack_seconds = 2 ** entropy / 1e10  # 10 billion guesses per second
    if crack_seconds < 1:
        crack = ctx.M("فوری", "instantly")
    elif crack_seconds < 3600:
        crack = ctx.M(f"{crack_seconds:.0f} ثانیه", f"{crack_seconds:.0f} seconds")
    elif crack_seconds < 86400 * 365:
        crack = ctx.M(f"{crack_seconds / 3600:.1f} ساعت", f"{crack_seconds / 3600:.1f} hours")
    else:
        crack = ctx.M(f"{crack_seconds / (86400 * 365):,.0f} سال", f"{crack_seconds / (86400 * 365):,.0f} years")
    import secrets

    suggestion = (
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%^&*?-_=+")
        + "".join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*?-_=+")
                  for _ in range(14))
    )
    lines = [
        f"{color} {label}",
        ctx.M(f"آنتروپی: {entropy:.1f} بیت", f"Entropy: {entropy:.1f} bits"),
        ctx.M(f"طول: {len(password)} نویسه", f"Length: {len(password)} characters"),
        ctx.M(f"زمان تخمینی شکستن: {crack}", f"Estimated crack time: {crack}"),
    ]
    if issues:
        lines += ["", ctx.M("نقاط ضعف:", "Weaknesses:")] + [f"• {item}" for item in issues]
    lines += ["", ctx.M(f"پیشنهاد قوی‌تر: {suggestion}", f"Stronger suggestion: {suggestion}")]
    return RunResult(message=f"{color} {label} — {entropy:.0f} bit", details="\n".join(lines))


def _mask_secret_match(raw: str) -> str:
    clean = " ".join(raw.strip().splitlines())
    if len(clean) <= 8:
        return "*" * max(4, len(clean))
    return f"{clean[:4]}…{'*' * 6}{clean[-2:]}"


@tool(
    id="secret_scan", category="security", name="یافتن رمز و کلید در فایل‌ها",
    name_en="Scan for secrets",
    desc="پوشه را برای کلیدهای API، توکن‌ها، رمزهای نوشته‌شده و شماره‌های حساس اسکن می‌کند.",
    desc_en="Scan a folder for API keys, tokens, hardcoded passwords and sensitive numbers.",
    icon="search", order=101,
    keywords=("secret scan", "api key leak", "نشت رمز", "کلید api", "رمز نوشته شده"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("max_mb", K_TEXT, "حداکثر حجم هر فایل (مگابایت)", "Max file size (MB)", default="5"),
    ],
)
def secret_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = Path(str(v.get("folder") or ""))
    if not folder.is_dir():
        raise ToolError(ctx.M("پوشه پیدا نشد.", "Folder not found."))
    reject_disk_root(folder, ctx)
    try:
        max_bytes = int(float(str(v.get("max_mb") or "5")) * 1024 * 1024)
    except ValueError:
        max_bytes = 5 * 1024 * 1024
    files = [p for p in walk_files(folder) if p.suffix.lower() in SCAN_EXTENSIONS]
    findings: List[str] = []
    scanned = 0
    for index, path in enumerate(files, start=1):
        if index % 10 == 0:
            ctx.progress(index, len(files), ctx.M(f"اسکن {path.name}", f"Scanning {path.name}"))
        try:
            if path.stat().st_size > max_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned += 1
        for label, pattern in SECRET_PATTERNS:
            for match in re.finditer(pattern, text):
                snippet = match.group(0)
                line_number = text[: match.start()].count("\n") + 1
                if label == "Iranian national ID" and len(set(snippet)) < 3:
                    continue
                masked = _mask_secret_match(snippet)
                findings.append(f"{path.relative_to(folder)}:{line_number}  [{label}]  {masked}")
    ctx.progress(len(files), len(files), "")
    if not findings:
        return RunResult(
            message=ctx.M(f"{scanned} فایل اسکن شد؛ چیز حساسی پیدا نشد. 👌",
                          f"Scanned {scanned} file(s); nothing sensitive found. 👌"))
    by_kind: Dict[str, int] = {}
    for line in findings:
        label = line.split("[", 1)[-1].split("]", 1)[0]
        by_kind[label] = by_kind.get(label, 0) + 1
    summary = "، ".join(f"{kind}: {count}" for kind, count in sorted(by_kind.items(), key=lambda i: -i[1]))
    return RunResult(
        message=ctx.M(f"{len(findings)} مورد حساس در {scanned} فایل پیدا شد.\n{summary}",
                      f"{len(findings)} sensitive finding(s) in {scanned} file(s).\n{summary}"),
        details="\n".join(findings[:300]) + ("\n…" if len(findings) > 300 else ""),
    )


@tool(
    id="checksum_manifest", category="security", name="ساخت / بررسی فهرست هش",
    name_en="Checksum manifest",
    desc="برای فایل‌ها فهرست SHA-256 می‌سازد یا سلامت آن‌ها را با فهرست موجود می‌سنجد.",
    desc_en="Create a SHA-256 manifest for files, or verify files against an existing one.",
    icon="check", order=102,
    keywords=("checksum", "sha256", "manifest", "بررسی سلامت فایل", "هش"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="create",
              choices=[("create", "ساخت فهرست", "Create manifest"),
                       ("verify", "بررسی با فهرست", "Verify against manifest")]),
        Field("items", K_FILES, "فایل‌ها (برای ساخت)", "Files (to create)", filt="همه فایل‌ها (*)",
              multiple=True, visible_when=("action", "create")),
        Field("manifest", K_FILE, "فایل فهرست", "Manifest file", filt="فهرست (*.sha256 *.txt);;همه فایل‌ها (*)",
              visible_when=("action", "verify")),
    ],
)
def checksum_manifest(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    action = str(v.get("action") or "create")
    if action == "create":
        items = files_or_raise(v, "items", ctx)
        lines: List[str] = []
        for index, path in enumerate(items, start=1):
            ctx.progress(index, len(items), path.name)
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            lines.append(f"{digest.hexdigest()}  {path.name}")
        dest = out_dir(ctx) / "checksums.sha256"
        counter = 1
        while dest.exists():
            dest = out_dir(ctx) / f"checksums-{counter}.sha256"
            counter += 1
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return RunResult(
            message=ctx.M(f"فهرست SHA-256 برای {len(items)} فایل ساخته شد.\n{dest}",
                          f"SHA-256 manifest for {len(items)} file(s) created.\n{dest}"),
            outputs=[str(dest)], open_target=str(dest), details="\n".join(lines))
    manifest = first_file(v, "manifest", ctx)
    entries: List[Tuple[str, str]] = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{32,128})\s+\*?(.+)$", line)
        if match:
            entries.append((match.group(1).lower(), match.group(2).strip()))
    if not entries:
        raise ToolError(ctx.M("فایل فهرست خالی یا نامعتبر است.", "The manifest is empty or invalid."))
    ok = bad = missing = 0
    lines: List[str] = []
    base = manifest.parent.resolve()
    for index, (expected, name) in enumerate(entries, start=1):
        ctx.progress(index, len(entries), name)
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            bad += 1
            lines.append(f"✗ {name} — {ctx.M('مسیر ناامن رد شد', 'unsafe path refused')}")
            continue
        raw_candidate = manifest.parent / rel
        if raw_candidate.is_symlink():
            bad += 1
            lines.append(f"✗ {name} — {ctx.M('سیم‌لینک رد شد', 'symlink refused')}")
            continue
        candidate = raw_candidate.resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            bad += 1
            lines.append(f"✗ {name} — {ctx.M('مسیر خارج از پوشه‌ی فهرست رد شد', 'outside manifest directory')}")
            continue
        if not candidate.is_file():
            missing += 1
            lines.append(f"? {name} — " + ctx.M("فایل نیست", "missing"))
            continue
        digest = hashlib.sha256()
        with open(candidate, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() == expected:
            ok += 1
            lines.append(f"✓ {name}")
        else:
            bad += 1
            lines.append(f"✗ {name} — {ctx.M('هش متفاوت است', 'hash mismatch')}")
    message = ctx.M(f"{ok} سالم، {bad} خراب/تغییریافته، {missing} گم‌شده.",
                    f"{ok} ok, {bad} modified, {missing} missing.")
    return RunResult(message=message, details="\n".join(lines[:400]))


@tool(
    id="password_breach_check", category="security", name="بررسی نشت رمز عبور",
    name_en="Password breach check",
    desc="با روش k-anonymity بررسی می‌کند رمز در نشت‌های عمومی دیده شده یا نه (رمز کامل ارسال نمی‌شود).",
    desc_en="Check with k-anonymity whether a password appeared in public breaches (only a hash prefix leaves the PC).",
    icon="shield", order=103,
    keywords=("haveibeenpwned", "breach", "نشت رمز", "رمز لو رفته"),
    fields=[
        Field("password", K_PASSWORD, "رمز عبور", "Password", required=True),
    ],
)
def password_breach_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    requests = require("requests", "requests", ctx)
    password = str(v.get("password") or "")
    if not password:
        raise ToolError(ctx.M("رمز را وارد کنید.", "Enter a password."))
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    ctx.progress(1, 2, ctx.M("پرسش از سرویس نشت", "Contacting the breach service"))
    try:
        response = requests.get(f"https://api.pwnedpasswords.com/range/{prefix}", timeout=15,
                                headers={"User-Agent": "WinGenius/2.0"})
        response.raise_for_status()
    except Exception as exc:
        raise ToolError(ctx.M(f"اتصال به سرویس ممکن نشد: {exc}", f"Could not reach the service: {exc}"))
    ctx.progress(2, 2, "")
    count = 0
    for line in response.text.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 2 and parts[0] == suffix:
            count = int(parts[1])
            break
    if count:
        return RunResult(
            message=ctx.M(f"⛔ این رمز {count:,} بار در نشت‌های عمومی دیده شده است. آن را عوض کنید.",
                          f"⛔ This password appeared {count:,} times in public breaches. Change it."),
        )
    return RunResult(message=ctx.M("✅ این رمز در نشت‌های عمومی شناخته‌شده پیدا نشد.",
                                   "✅ This password was not found in known public breaches."))


@tool(
    id="bitlocker_status", category="security", name="وضعیت BitLocker", name_en="BitLocker status",
    desc="وضعیت رمزنگاری درایوها با BitLocker را می‌خواند و می‌تواند بازیابی‌کلید را ذخیره کند.",
    desc_en="Read BitLocker encryption status of drives and save recovery keys.",
    icon="lock", order=104, windows_only=True,
    keywords=("bitlocker", "encryption", "رمزنگاری درایو", "کلید بازیابی"),
    fields=[
        Field("action", K_CHOICE, "عملیات", "Action", default="status",
              choices=[("status", "نمایش وضعیت", "Show status"),
                       ("recovery", "ذخیره‌ی کلیدهای بازیابی", "Save recovery keys")]),
    ],
)
def bitlocker_status(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from ._helpers import need_windows

    need_windows(ctx)
    import subprocess

    if str(v.get("action") or "status") == "status":
        completed = subprocess.run(["manage-bde", "-status"], capture_output=True, text=True,
                                   timeout=60, errors="replace", creationflags=0x08000000)
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0 and "denied" in output.lower():
            raise ToolError(ctx.M("این دستور نیازمند دسترسی مدیر است.", "This command needs administrator rights."))
        return RunResult(message=ctx.M("وضعیت BitLocker خوانده شد.", "BitLocker status collected."),
                         details=output.strip()[:4000])
    completed = subprocess.run(["manage-bde", "-protectors", "-get", "C:"], capture_output=True,
                               text=True, timeout=60, errors="replace", creationflags=0x08000000)
    output = (completed.stdout or "") + (completed.stderr or "")
    dest = out_dir(ctx) / "bitlocker_recovery.txt"
    dest.write_text(output, encoding="utf-8")
    return RunResult(
        message=ctx.M(f"کلیدها در فایل ذخیره شد — جای امنی نگه دارید.\n{dest}",
                      f"Keys saved — keep the file somewhere safe.\n{dest}"),
        outputs=[str(dest)], details=output.strip()[:2000])
