"""Security extras: two-factor codes, LSB steganography and HMAC signatures."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_INT, K_PASSWORD, K_SAVE, K_TEXT, Field, RunContext,
    RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import first_file, out_dir, read_text_file, save_target

STEG_MAGIC = b"WGS1"
STEG_SALT = 16
STEG_NONCE = 12
SCRYPT_LOG_N = 14          # ~16 MB, fast enough for an interactive tool


# -------------------------------------------------------------------- TOTP
def _b32_decode(secret: str, ctx: RunContext) -> bytes:
    cleaned = "".join(ch for ch in secret.upper() if ch not in " -")
    cleaned += "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned, casefold=True)
    except Exception:
        raise ToolError(ctx.M(
            "کلید TOTP نامعتبر است؛ باید Base32 باشد (حروف A-Z و 2-7).",
            "Invalid TOTP secret: it must be Base32 (A-Z and 2-7)."))


def totp_at(secret_bytes: bytes, timestamp: float, period: int = 30, digits: int = 6,
            algorithm: str = "sha1") -> str:
    """RFC 6238 TOTP value for a Unix timestamp."""
    counter = int(timestamp // period)
    digest = algorithm.lower().replace("-", "")
    mac = hmac.new(secret_bytes, struct.pack(">Q", counter), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


@tool(
    id="totp_code", category="security", name="کد دو‌مرحله‌ای (TOTP)",
    name_en="Two-factor code (TOTP)",
    desc="از کلید Base32 کد ۶ رقمیِ ۲FA می‌سازد، ثانیه‌ی باقی‌مانده را می‌گوید و کد کاربر را بررسی می‌کند.",
    desc_en="Turns a Base32 secret into the current 6-digit 2FA code, shows the seconds left and verifies a code.",
    icon="key", order=60,
    keywords=("totp", "2fa", "otp", "authenticator", "google authenticator", "دو مرحله",
              "کد", "احراز هویت", "توکن"),
    fields=[
        Field("secret", K_PASSWORD, "کلید Base32", "Base32 secret", required=True,
              help="کلید را از سرویس (یا کد QR) بگیرید؛ در تاریخچه ذخیره نمی‌شود.",
              help_en="Take it from the service or its QR code; it is never saved to history."),
        Field("digits", K_INT, "تعداد رقم", "Digits", default=6, minimum=6, maximum=8),
        Field("period", K_INT, "بازه (ثانیه)", "Period (seconds)", default=30, minimum=10, maximum=120),
        Field("algorithm", K_CHOICE, "الگوریتم", "Algorithm", default="sha1",
              choices=[("sha1", "SHA-1 (رایج)", "SHA-1 (common)"),
                       ("sha256", "SHA-256", "SHA-256"), ("sha512", "SHA-512", "SHA-512")]),
        Field("check", K_TEXT, "کد برای بررسی", "Code to verify"),
    ],
)
def totp_code(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    secret = str(v.get("secret") or "").strip()
    if not secret:
        raise ToolError(ctx.M("کلید Base32 را وارد کنید.", "Enter the Base32 secret."))
    key = _b32_decode(secret, ctx)
    digits = max(6, min(8, int(v.get("digits") or 6)))
    period = max(10, min(120, int(v.get("period") or 30)))
    algorithm = str(v.get("algorithm") or "sha1")
    now = time.time()
    code = totp_at(key, now, period, digits, algorithm)
    remaining = period - int(now) % period
    lines = [
        (f"کد فعلی: {code}" if ctx.lang != "en" else f"Current code: {code}"),
        (f"ثانیه‌ی باقی‌مانده: {remaining}" if ctx.lang != "en"
         else f"Seconds remaining: {remaining}"),
        "",
        ("کدهای بعدی:" if ctx.lang != "en" else "Next codes:"),
    ]
    for step in range(1, 4):
        lines.append(f"  +{step * period}s → "
                     f"{totp_at(key, now + step * period, period, digits, algorithm)}")
    expected = str(v.get("check") or "").strip().replace(" ", "")
    if expected:
        window = [totp_at(key, now + delta * period, period, digits, algorithm)
                  for delta in (-1, 0, 1)]
        if expected == code:
            lines.insert(0, ("✓ کد درست است." if ctx.lang != "en" else "✓ The code is correct."))
        elif expected in window:
            lines.insert(0, ("✓ کد درست است (پنجره‌ی ±۱ دوره)." if ctx.lang != "en"
                             else "✓ The code is correct (±1 period)."))
        else:
            lines.insert(0, ("✖ کد نادرست است." if ctx.lang != "en" else "✖ The code is wrong."))
            return RunResult(message=lines[0], details="\n".join(lines[1:]))
    return RunResult(message=lines[0], details="\n".join(lines[1:]))


# --------------------------------------------------------------- steganography
def _steg_key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=2 ** SCRYPT_LOG_N, r=8, p=1)
    return kdf.derive(password.encode("utf-8"))


def _steg_payload(data: bytes, password: str) -> bytes:
    """Frame: magic | flags | salt | nonce | body_length(uint32) | body.

    `body_length` counts the *framed* body (ciphertext + GCM tag when a password
    is used) so the reader knows exactly how many bits to pull back.
    """
    if password:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt, nonce = os.urandom(STEG_SALT), os.urandom(STEG_NONCE)
        key = _steg_key(password, salt)
        body = AESGCM(key).encrypt(nonce, data, STEG_MAGIC)
        return STEG_MAGIC + b"\x01" + salt + nonce + struct.pack(">I", len(body)) + body
    body = data
    return STEG_MAGIC + b"\x00" + b"\x00" * STEG_SALT + b"\x00" * STEG_NONCE \
        + struct.pack(">I", len(body)) + body


def _steg_unframe(frame: bytes, password: str, ctx: RunContext) -> bytes:
    if len(frame) < 4 + 1 + STEG_SALT + STEG_NONCE + 4 or frame[:4] != STEG_MAGIC:
        raise ToolError(ctx.M("این تصویر پیام مخفی WinGenius ندارد.",
                              "This image carries no WinGenius hidden message."))
    encrypted = frame[4] == 1
    offset = 5
    salt = frame[offset:offset + STEG_SALT]
    offset += STEG_SALT
    nonce = frame[offset:offset + STEG_NONCE]
    offset += STEG_NONCE
    length = struct.unpack(">I", frame[offset:offset + 4])[0]
    body = frame[offset + 4:offset + 4 + length]
    if encrypted:
        if not password:
            raise ToolError(ctx.M("این پیام رمز دارد؛ رمز را وارد کنید.",
                                  "This message is encrypted; enter the password."))
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            body = AESGCM(_steg_key(password, salt)).decrypt(nonce, body, STEG_MAGIC)
        except Exception:
            raise ToolError(ctx.M("رمز اشتباه است یا تصویر تغییر کرده.",
                                  "Wrong password, or the image was altered."))
    return body


def _lsb_capacity(image) -> int:
    return (image.width * image.height * 3) // 8


def _bit_stream(image):
    """Yield the low bit of every R/G/B sample, top-left to bottom-right."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, _alpha = pixels[x, y]
            yield red & 1
            yield green & 1
            yield blue & 1


def _extract_bytes(image, count: int) -> bytes:
    """Read `count` bytes out of the low bits (missing bits read as zero)."""
    stream = _bit_stream(image)
    out = bytearray()
    for _ in range(count):
        value = 0
        for _bit in range(8):
            value = (value << 1) | next(stream, 0)
        out.append(value)
    return bytes(out)


def _embed_bytes(image, frame: bytes) -> None:
    """Write `frame` into the low bits of an RGBA image, in place."""
    bits = [(byte >> bit) & 1 for byte in frame for bit in range(7, -1, -1)]
    pixels = image.load()
    index = 0
    total = len(bits)
    for y in range(image.height):
        if index >= total:
            return
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            channels = [red & 0xFE, green & 0xFE, blue & 0xFE]
            for channel in range(3):
                if index < total:
                    channels[channel] |= bits[index]
                    index += 1
            pixels[x, y] = (channels[0], channels[1], channels[2], alpha)
            if index >= total:
                return


@tool(
    id="steg_hide", category="security", name="پنهان‌کردن پیام در عکس",
    name_en="Hide a message inside a photo",
    desc="متن یا فایل را به‌صورت رمزدار در بیت‌های نامحسوس یک عکس PNG جاسازی می‌کند.",
    desc_en="Hides text or a file (optionally AES-encrypted) in the invisible low bits of a PNG.",
    icon="eye", order=61,
    keywords=("steganography", "hide", "secret", "lsb", "پنهان", "مخفی", "جاسازی", "استگانوگرافی"),
    fields=[
        Field("carrier", K_FILE, "عکس میزبان (PNG)", "Cover image (PNG)", required=True,
              filt="Images (*.png *.bmp *.tif *.tiff)"),
        Field("text", K_TEXT, "پیام", "Message"),
        Field("file", K_FILE, "یا فایل برای پنهان‌کردن", "or file to hide"),
        Field("password", K_PASSWORD, "رمز (اختیاری)", "Password (optional)",
              help="بدون رمز، هر کسی با این برنامه پیام را می‌خواند.",
              help_en="Without a password anyone with WinGenius can read the message."),
        Field("out", K_SAVE, "ذخیره‌ی عکس", "Save image", filt="PNG (*.png)"),
    ],
)
def steg_hide(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    try:
        from PIL import Image
    except Exception:
        raise ToolError(ctx.M("Pillow لازم است: pip install Pillow",
                              "Pillow is required: pip install Pillow"))
    carrier = first_file(v, "carrier", ctx)
    payload = str(v.get("text") or "").encode("utf-8")
    hidden_path = str(v.get("file") or "").strip()
    if hidden_path:
        if not Path(hidden_path).is_file():
            raise ToolError(ctx.M(f"فایل پیدا نشد: {hidden_path}", f"File not found: {hidden_path}"))
        payload = Path(hidden_path).read_bytes()
    if not payload:
        raise ToolError(ctx.M("یک پیام یا یک فایل برای پنهان‌کردن بدهید.",
                              "Provide a message or a file to hide."))
    password = str(v.get("password") or "")
    frame = _steg_payload(payload, password)
    image = Image.open(carrier).convert("RGBA")
    capacity = _lsb_capacity(image)
    if len(frame) > capacity:
        raise ToolError(ctx.M(
            f"عکس کوچک است: {human_size(len(frame))} پیام در {human_size(capacity)} ظرفیت جا نمی‌شود.\n"
            "یک عکس بزرگ‌تر انتخاب کنید.",
            f"The image is too small: {human_size(len(frame))} does not fit in {human_size(capacity)}.\n"
            "Pick a larger image."))
    ctx.progress(0, 1, "جاسازی")
    _embed_bytes(image, frame)
    ctx.progress(1, 1, "ذخیره")
    target = save_target(ctx, str(v.get("out") or "").strip(), f"{carrier.stem}_hidden.png")
    if target.suffix.lower() != ".png":
        target = target.with_suffix(".png")
    image.save(target, "PNG")
    return RunResult(
        message=ctx.M(
            f"{human_size(len(payload))} در عکس پنهان شد" +
            (" (رمزدار)." if password else " (بدون رمز)."),
            f"{human_size(len(payload))} hidden inside the photo" +
            (" (encrypted)." if password else " (no password).")),
        details=ctx.M(f"خروجی: {target}\nظرفیت استفاده‌شده: "
                      f"{len(frame) * 100 / max(1, capacity):.1f}%",
                      f"Output: {target}\nCapacity used: "
                      f"{len(frame) * 100 / max(1, capacity):.1f}%"),
        outputs=[str(target)], open_target=str(target))


@tool(
    id="steg_reveal", category="security", name="خواندن پیام پنهان از عکس",
    name_en="Reveal a hidden message",
    desc="پیام یا فایل پنهان‌شده در عکس را بیرون می‌کشد و اگر رمز داشته باشد رمزگشایی می‌کند.",
    desc_en="Extracts the message or file hidden in a photo, decrypting it when it is password-protected.",
    icon="search", order=62,
    keywords=("steganography", "reveal", "extract", "secret", "خواندن", "پنهان", "بازیابی"),
    fields=[
        Field("carrier", K_FILE, "عکس حاوی پیام", "Image with the message", required=True,
              filt="Images (*.png *.bmp *.tif *.tiff)"),
        Field("password", K_PASSWORD, "رمز", "Password"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی فایل", "Output folder for the file"),
        Field("just_text", K_BOOL, "فقط متن را نشان بده", "Show text only", default=True),
    ],
)
def steg_reveal(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    try:
        from PIL import Image
    except Exception:
        raise ToolError(ctx.M("Pillow لازم است: pip install Pillow",
                              "Pillow is required: pip install Pillow"))
    carrier = first_file(v, "carrier", ctx)
    image = Image.open(carrier).convert("RGBA")
    header_len = 4 + 1 + STEG_SALT + STEG_NONCE + 4
    if _lsb_capacity(image) < header_len:
        raise ToolError(ctx.M("این عکس برای حاوی بودن پیام خیلی کوچک است.",
                              "The image is too small to hold a message."))
    head = _extract_bytes(image, header_len)
    if head[:4] != STEG_MAGIC:
        raise ToolError(ctx.M("این عکس پیام مخفی WinGenius ندارد.",
                              "This image has no WinGenius hidden message."))
    declared = struct.unpack(">I", head[-4:])[0]
    if declared <= 0 or declared > 64 * 1024 * 1024 or             header_len + declared > _lsb_capacity(image):
        raise ToolError(ctx.M("طول پیام نامعتبر است؛ احتمالاً عکس تغییر کرده.",
                              "Invalid message length; the image was probably altered."))
    frame = _extract_bytes(image, header_len + declared)
    payload = _steg_unframe(frame, str(v.get("password") or ""), ctx)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if text and bool(v.get("just_text", True)):
        return RunResult(
            message=ctx.M(f"پیام پنهان ({len(payload)} بایت):",
                          f"Hidden message ({len(payload)} bytes):"),
            details=text[:20000])
    folder = Path(str(v.get("out_dir") or "")).strip() if str(v.get("out_dir") or "").strip() \
        else out_dir(ctx, "revealed")
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder / f"{carrier.stem}_hidden.bin")
    target.write_bytes(payload)
    return RunResult(
        message=ctx.M(f"فایل پنهان ({human_size(len(payload))}) بیرون کشیده شد.",
                      f"Hidden file ({human_size(len(payload))}) extracted."),
        details=text[:4000], outputs=[str(target)], open_target=str(target))


# --------------------------------------------------------------------- HMAC
@tool(
    id="hmac_sign", category="security", name="امضای HMAC متن و فایل",
    name_en="HMAC-sign text or files",
    desc="با یک کلید مشترک، اثر انگشت HMAC-SHA256 می‌سازد و امضای دریافتی را بررسی می‌کند.",
    desc_en="Creates an HMAC-SHA256 signature with a shared key and verifies a signature you received.",
    icon="shield", order=63,
    keywords=("hmac", "sign", "signature", "verify", "امضا", "کلید", "اثر انگشت", "تأیید"),
    fields=[
        Field("text", K_TEXT, "متن", "Text"),
        Field("file", K_FILE, "یا فایل", "or file"),
        Field("key", K_PASSWORD, "کلید مشترک", "Shared key"),
        Field("algorithm", K_CHOICE, "الگوریتم", "Algorithm", default="sha256",
              choices=[("sha256", "SHA-256", "SHA-256"), ("sha512", "SHA-512", "SHA-512"),
                       ("sha1", "SHA-1", "SHA-1")]),
        Field("expected", K_TEXT, "امضای دریافتی (برای بررسی)", "Signature to verify"),
    ],
)
def hmac_sign(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    raw = str(v.get("text") or "").encode("utf-8")
    if not raw:
        path = str(v.get("file") or "").strip()
        if path:
            raw = first_file(v, "file", ctx).read_bytes()
    if not raw:
        raise ToolError(ctx.M("متن یا فایل را بدهید.", "Provide text or a file."))
    key = str(v.get("key") or "").encode("utf-8")
    if not key:
        raise ToolError(ctx.M("کلید مشترک را وارد کنید.", "Enter the shared key."))
    algorithm = str(v.get("algorithm") or "sha256").replace("-", "").lower()
    signature = hmac.new(key, raw, getattr(hashlib, algorithm)).hexdigest()
    expected = str(v.get("expected") or "").strip().lower().replace(" ", "")
    details = (f"algorithm: HMAC-{algorithm.upper()}\n"
               f"bytes: {len(raw)}\n"
               f"signature: {signature}")
    if expected:
        matches = hmac.compare_digest(signature, expected)
        verdict = (("✓ امضا معتبر است." if matches else "✖ امضا معتبر نیست.")
                   if ctx.lang != "en" else
                   ("✓ The signature is valid." if matches else "✖ The signature is NOT valid."))
        return RunResult(message=verdict, details=details)
    return RunResult(message=f"HMAC-{algorithm.upper()}: {signature}", details=details)

