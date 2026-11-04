"""Security tools: password generation, hashing, local file/text encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import secrets
import string
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_FILES, K_FILE, K_INT, K_PASSWORD, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import hash_file, human_size, safe_filename, unique_path
from ._helpers import as_path_list, ensure_dir, first_file, out_dir, require, save_target

MAGIC = b"WGF1"      # legacy stream format (file_decrypt read-only support)
MAGIC_AES = b"WGA1"  # AES-256-GCM (authenticated encryption)
KDF_ITERATIONS = 240_000

PASSPHRASE_WORDS = (
    "acorn", "admiral", "almond", "alpine", "amber", "anchor", "anvil", "apple",
    "apricot", "archway", "arctic", "armada", "arrow", "aspen", "atlas", "aurora",
    "autumn", "avocado", "badger", "bamboo", "banjo", "banner", "barley", "basalt",
    "beacon", "beaver", "belfry", "berry", "birch", "bison", "blizzard", "blossom",
    "bonfire", "boulder", "bramble", "breeze", "bridge", "bronze", "brook", "buckle",
    "cactus", "camera", "campfire", "canary", "candle", "canyon", "captain", "caravan",
    "caribou", "cascade", "castle", "cavern", "cedar", "celeste", "chalet", "charcoal",
    "cherry", "chestnut", "cipher", "citadel", "citrus", "cliff", "clover", "cobalt",
    "coconut", "comet", "compass", "condor", "copper", "coral", "cosmos", "cotton",
    "cougar", "cradle", "cranberry", "crater", "creek", "crimson", "crystal", "cypress",
    "dagger", "dahlia", "daisy", "dancer", "dawn", "delta", "desert", "diamond",
    "dolphin", "domino", "dragon", "driftwood", "droplet", "dynamo", "eagle", "eclipse",
    "emerald", "ember", "empire", "encore", "engine", "enigma", "equinox", "estuary",
    "evergreen", "fabric", "falcon", "feather", "fennel", "ferry", "festival", "fiddle",
    "fjord", "flame", "flannel", "flint", "flora", "forest", "fortress", "fossil",
    "fountain", "foxglove", "frost", "galaxy", "galleon", "garden", "garnet", "gazelle",
    "geyser", "ginger", "glacier", "glider", "granite", "griffin", "grove", "guitar",
    "halcyon", "hammer", "harbor", "harmony", "harvest", "hawthorn", "hazel", "heather",
    "helmet", "herald", "hibiscus", "hickory", "hollow", "horizon", "Hummingbird".lower(), "hunter",
    "iceberg", "indigo", "infinity", "island", "ivory", "jacket", "jaguar", "jasmine",
    "jasper", "journal", "jubilee", "jungle", "juniper", "kayak", "kestrel", "kettle",
    "kingdom", "kitten", "lagoon", "lantern", "lapwing", "larch", "laurel", "lavender",
    "ledger", "legend", "lemon", "leopard", "liberty", "lighthouse", "lilac", "linden",
    "lotus", "lumen", "lynx", "magnet", "magnolia", "mahogany", "mammoth", "mandarin",
    "mango", "mantle", "maple", "marble", "marigold", "mariner", "meadow", "mercury",
    "meridian", "meteor", "midnight", "mimosa", "mineral", "mirage", "monarch", "monsoon",
    "morning", "mosaic", "mountain", "mulberry", "mustang", "narwhal", "nebula", "nectar",
    "needle", "neptune", "nightfall", "nimbus", "nomad", "nutmeg", "oasis", "obsidian",
    "ocean", "octave", "olive", "onyx", "opal", "orange", "orbit", "orchid",
    "origami", "osprey", "otter", "outpost", "oyster", "paddle", "palace", "panther",
    "papaya", "paradox", "paragon", "parsley", "pavilion", "peacock", "pebble", "pelican",
    "pendulum", "peony", "pepper", "peregrine", "phantom", "phoenix", "pioneer", "planet",
    "platinum", "plover", "plum", "polar", "poplar", "prairie", "prism", "pulsar",
    "pumpkin", "pyramid", "python", "quartz", "quasar", "quiver", "radish", "rainbow",
    "rambutan", "ranger", "rapids", "raven", "redwood", "reef", "relic", "ribbon",
    "ridge", "ripple", "river", "rocket", "rosemary", "ruby", "saffron", "sailor",
    "salmon", "sandstone", "sapphire", "saturn", "savanna", "scarlet", "scepter", "sequoia",
    "seraph", "shadow", "shamrock", "shelter", "shimmer", "sierra", "silver", "skyline",
    "solstice", "sonnet", "sparrow", "spectrum", "sphinx", "spruce", "starlight", "summit",
    "sundial", "sunflower", "sunrise", "sunset", "swallow", "sycamore", "talisman", "tangerine",
    "tempest", "temple", "terrace", "thistle", "thunder", "tiger", "timber", "topaz",
    "tornado", "torrent", "trident", "trillium", "triumph", "tundra", "turbine", "twilight",
    "umbrella", "unicorn", "upland", "urban", "valley", "velvet", "venture", "verbena",
    "vertex", "vessel", "vibrant", "village", "violet", "voyager", "wagon", "walnut",
    "wanderer", "warden", "waterfall", "whisper", "willow", "windmill", "winter", "wisteria",
    "wizard", "wombat", "woodland", "wren", "yarrow", "yellow", "yonder", "zenith",
    "zephyr", "zircon",
)


def _aesgcm():
    """Return (AESGCM factory, key-derivation helper) or (None, None)."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        def derive(password: str, salt: bytes) -> bytes:
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                             iterations=KDF_ITERATIONS)
            return kdf.derive(password.encode("utf-8"))

        return AESGCM, derive
    except Exception:
        return None, None


# ------------------------------------------------------------- password generator
@tool(
    id="password_generate", category="security", name="ساخت رمز عبور", name_en="Password generator",
    desc="رمزهای تصادفی قوی یا عبارت عبور (passphrase) با آنتروپی محاسبه‌شده می‌سازد.",
    desc_en="Generate strong random passwords or passphrases with entropy info.",
    icon="key", order=10, keywords=("password generator", "پسورد", "رمز قوی", "passphrase"),
    fields=[
        Field("mode", K_CHOICE, "نوع", "Type", default="password",
              choices=[("password", "رمز تصادفی", "Random password"),
                       ("passphrase", "عبارت عبور", "Passphrase")]),
        Field("length", K_INT, "طول", "Length", default=20, minimum=6, maximum=128,
              visible_when=("mode", "password")),
        Field("count", K_INT, "تعداد", "How many", default=5, minimum=1, maximum=100),
        Field("digits", K_BOOL, "شامل اعداد", "Include digits", default=True, visible_when=("mode", "password")),
        Field("symbols", K_BOOL, "شامل نمادها", "Include symbols", default=True, visible_when=("mode", "password")),
        Field("upper", K_BOOL, "شامل حروف بزرگ", "Include uppercase", default=True, visible_when=("mode", "password")),
        Field("words", K_INT, "تعداد کلمه", "Words", default=5, minimum=3, maximum=12,
              visible_when=("mode", "passphrase")),
        Field("simple", K_BOOL, "بدون کاراکترهای گیج‌کننده (0/O/1/l)", "Avoid look-alikes", default=True,
              visible_when=("mode", "password")),
        Field("save", K_SAVE, "ذخیره در فایل (اختیاری)", "Save to file (optional)", filt="Text (*.txt)"),
    ],
)
def password_generate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    mode = str(v.get("mode") or "password")
    count = int(v.get("count") or 5)
    results: List[str] = []
    if mode == "passphrase":
        words = PASSPHRASE_WORDS
        n_words = int(v.get("words") or 5)
        for _ in range(count):
            chosen = [secrets.choice(words) for _ in range(n_words)]
            results.append("-".join(chosen) + str(secrets.randbelow(90) + 10))
        entropy = n_words * math.log2(len(words)) + math.log2(90)
    else:
        length = max(6, int(v.get("length") or 20))
        alphabet = string.ascii_lowercase
        if v.get("upper", True):
            alphabet += string.ascii_uppercase
        if v.get("digits", True):
            alphabet += string.digits
        if v.get("symbols", True):
            alphabet += "!@#$%^&*()-_=+[]{};:,.?/|~"
        if v.get("simple", True):
            for bad in "0O1lI|`'\"":
                alphabet = alphabet.replace(bad, "")
        minimal = [string.ascii_lowercase]
        if v.get("upper", True):
            minimal.append(string.ascii_uppercase)
        if v.get("digits", True):
            minimal.append(string.digits)
        if v.get("symbols", True):
            minimal.append("!@#$%^&*()-_=+")
        if v.get("simple", True):
            minimal = ["".join(ch for ch in pool if ch not in "0O1lI|`'\"") for pool in minimal]
            minimal = [pool for pool in minimal if pool]
        for _ in range(count):
            chars = [secrets.choice(pool) for pool in minimal]
            while len(chars) < length:
                chars.append(secrets.choice(alphabet))
            secrets.SystemRandom().shuffle(chars)
            results.append("".join(chars))
        pool_size = max(2, len(set(alphabet)))
        entropy = length * math.log2(pool_size)
    details = "\n".join(results)
    outputs: List[str] = []
    if str(v.get("save") or "").strip():
        dest = save_target(ctx, str(v.get("save")), "passwords.txt")
        dest.write_text(details + "\n", encoding="utf-8")
        outputs.append(str(dest))
    strength = ctx.M("بسیار قوی", "very strong") if entropy > 80 else ctx.M("قوی", "strong")
    return RunResult(
        message=ctx.M(f"{len(results)} رمز ساخته شد — تخمین قدرت: {strength} (~{int(entropy)} بیت)",
                      f"{len(results)} password(s) generated — {strength} (~{int(entropy)} bits)"),
        details=details, outputs=outputs,
    )


# --------------------------------------------------------------------- hashes
@tool(
    id="hash_text", category="security", name="هش متن", name_en="Hash text",
    desc="MD5/SHA-1/SHA-256/SHA-512 یک متن را محاسبه می‌کند و با نسخه‌ی دیگر مقایسه می‌کند.",
    desc_en="Compute MD5/SHA-1/SHA-256/SHA-512 of a text and compare with an expected value.",
    icon="hash", order=20, keywords=("hash text", "هش", "sha256", "بررسی صحت"),
    fields=[
        Field("text", K_TEXT, "متن", "Text", required=True, default=""),
        Field("compare", K_TEXT, "هش مورد انتظار (اختیاری)", "Expected hash (optional)"),
    ],
)
def hash_text(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    text = str(v.get("text") or "")
    if not text:
        raise ToolError(ctx.M("متنی وارد نشد.", "No text given."))
    data = text.encode("utf-8")
    rows = [f"{algo.upper():<8}: {hashlib.new(algo, data).hexdigest()}"
            for algo in ("md5", "sha1", "sha256", "sha512")]
    expected = str(v.get("compare") or "").strip().lower()
    message = ctx.M("هش متن", "Text hashes")
    if expected:
        match = any(row.split(":")[1].strip().lower() == expected for row in rows)
        message = ctx.M("✅ مطابقت دارد", "✅ Match") if match else ctx.M("❌ مطابقت ندارد", "❌ No match")
    return RunResult(message=message, details="\n".join(rows))


# -------------------------------------------------------------- file encrypt
def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000, dklen=32)


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Simple, dependency-free stream cipher (SHA-256 counter mode)."""
    out = bytearray(len(data))
    block = 0
    offset = 0
    while offset < len(data):
        digest = hashlib.sha256(key + nonce + block.to_bytes(8, "big")).digest()
        chunk = data[offset:offset + len(digest)]
        for i, byte in enumerate(chunk):
            out[offset + i] = byte ^ digest[i]
        offset += len(chunk)
        block += 1
    return bytes(out)


@tool(
    id="file_encrypt", category="security", name="رمزگذاری فایل", name_en="Encrypt file",
    desc="فایل را با رمز عبور قفل می‌کند (AES-256-GCM با کلید PBKDF2)؛ فقط با همان رمز باز می‌شود.",
    desc_en="Password-encrypt a file with AES-256-GCM (PBKDF2 derived key).",
    icon="lock", order=30, keywords=("encrypt file", "رمزگذاری", "قفل فایل", "aes"),
    fields=[
        Field("src", K_FILES, "فایل‌ها", "Files", required=True, filt="همه فایل‌ها (*.*)", multiple=True),
        Field("password", K_PASSWORD, "رمز عبور", "Password", required=True),
        Field("out_dir", K_SAVE, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def file_encrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = [Path(p) for p in as_path_list(v.get("src"))]
    password = str(v.get("password") or "")
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    if len(password) < 4:
        raise ToolError(ctx.M("رمز باید حداقل ۴ کاراکتر باشد.", "Password must be at least 4 characters."))
    outputs: List[str] = []
    AESGCM, derive_aes = _aesgcm()
    if AESGCM is None:
        raise ToolError(ctx.M("برای رمزگذاری امن AES-256-GCM، بسته‌ی cryptography لازم است.",
                              "The 'cryptography' package is required for AES-256-GCM encryption."))
    for index, path in enumerate(files):
        if not path.exists():
            raise ToolError(ctx.M(f"پیدا نشد: {path}", f"Not found: {path}"))
        ctx.progress(index, len(files), path.name)
        data = path.read_bytes()
        folder = str(v.get("out_dir") or "").strip()
        target_dir = ensure_dir(folder) if folder and Path(folder).is_dir() else out_dir(ctx)
        dest = unique_path(target_dir / f"{safe_filename(path.name)}.wgenc")
        salt, nonce = os.urandom(16), os.urandom(12)
        key = derive_aes(password, salt)
        blob = MAGIC_AES + salt + nonce + AESGCM(key).encrypt(nonce, data, None)
        dest.write_bytes(blob)
        outputs.append(str(dest))
    return RunResult(
        message=ctx.M(f"{len(outputs)} فایل رمزگذاری شد (پسوند .wgenc).\nرمز را فراموش نکنید — بدون آن بازیابی ممکن نیست.",
                      f"{len(outputs)} file(s) encrypted (.wgenc).\nDo not lose the password — it cannot be recovered."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


@tool(
    id="file_decrypt", category="security", name="رمزگشایی فایل", name_en="Decrypt file",
    desc="فایل‌های .wgenc ساخته‌شده با ابزار رمزگذاری را با رمز درست باز می‌کند.",
    desc_en="Decrypt .wgenc files created by the encrypt tool.",
    icon="key", order=40, keywords=("decrypt file", "رمزگشایی", "باز کردن فایل قفل"),
    fields=[
        Field("src", K_FILES, "فایل‌های .wgenc", "Encrypted files", required=True,
              filt="Encrypted (*.wgenc);;همه فایل‌ها (*)", multiple=True),
        Field("password", K_PASSWORD, "رمز عبور", "Password", required=True),
        Field("out_dir", K_SAVE, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def file_decrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("لطفاً فایل رمزشده را انتخاب کنید.",
                              "Please choose the encrypted file."))
    password = str(v.get("password") or "")
    outputs: List[str] = []
    for index, path in enumerate(files):
        if not path.exists():
            raise ToolError(ctx.M(f"پیدا نشد: {path}", f"Not found: {path}"))
        ctx.progress(index, len(files), path.name)
        blob = path.read_bytes()
        raw_name = path.name[:-len(".wgenc")] if path.name.endswith(".wgenc") else path.stem
        original_name = safe_filename(raw_name)
        folder = str(v.get("out_dir") or "").strip()
        target_dir = ensure_dir(folder) if folder and Path(folder).is_dir() else out_dir(ctx)
        dest = unique_path(target_dir / original_name)
        if blob.startswith(MAGIC_AES):
            AESGCM, derive_aes = _aesgcm()
            if AESGCM is None:
                raise ToolError(ctx.M("برای باز کردن این فایل، بسته‌ی cryptography لازم است.",
                                      "The 'cryptography' package is required to open this file."))
            body = blob[len(MAGIC_AES):]
            salt, nonce, cipher_text = body[:16], body[16:28], body[28:]
            key = derive_aes(password, salt)
            try:
                data = AESGCM(key).decrypt(nonce, cipher_text, None)
            except Exception:
                raise ToolError(ctx.M(f"رمز اشتباه است یا فایل خراب شده: {path.name}",
                                      f"Wrong password or corrupted file: {path.name}"))
        elif blob.startswith(MAGIC):
            body = blob[len(MAGIC):]
            salt, nonce, tag, payload = body[:16], body[16:32], body[32:64], body[64:]
            key = _derive_key(password, salt)
            if not hmac.compare_digest(hmac.new(key, payload, hashlib.sha256).digest(), tag):
                raise ToolError(ctx.M(f"رمز اشتباه است یا فایل خراب شده: {path.name}",
                                      f"Wrong password or corrupted file: {path.name}"))
            data = _xor_stream(payload, key, nonce)
        else:
            raise ToolError(ctx.M(f"این فایل با وین‌جنیوس رمزگذاری نشده است: {path.name}",
                                  f"Not a WinGenius-encrypted file: {path.name}"))
        dest.write_bytes(data)
        outputs.append(str(dest))
    if not outputs:
        raise ToolError(ctx.M("هیچ فایلی رمزگشایی نشد.", "No file could be decrypted."))
    return RunResult(message=ctx.M(f"{len(outputs)} فایل رمزگشایی شد.", f"{len(outputs)} file(s) decrypted."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


# ------------------------------------------------------------------ text crypto
@tool(
    id="text_encrypt", category="security", name="رمزگذاری متن", name_en="Encrypt text",
    desc="یک متن را با رمز به رشته‌ی امن تبدیل می‌کند (و برعکس).",
    desc_en="Turn text into an encrypted string and back.",
    icon="clipboard", order=50, keywords=("encrypt text", "متن رمز", "مخفی کردن متن"),
    fields=[
        Field("mode", K_CHOICE, "عملیات", "Operation", default="encrypt",
              choices=[("encrypt", "رمزگذاری", "Encrypt"), ("decrypt", "رمزگشایی", "Decrypt")]),
        Field("text", K_TEXT, "متن", "Text", required=True, sensitive=True),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
    ],
)
def text_encrypt(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    mode = str(v.get("mode") or "encrypt")
    text = str(v.get("text") or "")
    password = str(v.get("password") or "")
    AESGCM, derive_aes = _aesgcm()
    if AESGCM is None:
        raise ToolError(ctx.M("برای رمزگذاری امن متن (AES-256-GCM)، بسته‌ی cryptography لازم است.",
                              "The 'cryptography' package is required for AES-256-GCM text encryption."))
    if mode == "encrypt":
        salt, nonce = os.urandom(16), os.urandom(12)
        key = derive_aes(password, salt)
        blob = MAGIC_AES + salt + nonce + AESGCM(key).encrypt(nonce, text.encode("utf-8"), None)
        return RunResult(message=ctx.M("متن رمزگذاری شد (کپی کنید):", "Encrypted text (copy it):"),
                         details=base64.urlsafe_b64encode(blob).decode())
    try:
        blob = base64.urlsafe_b64decode(str(v.get("text") or "").encode())
    except Exception:
        raise ToolError(ctx.M("رشته‌ی رمز نامعتبر است.", "Invalid encrypted string."))
    if blob.startswith(MAGIC_AES):
        body = blob[len(MAGIC_AES):]
        if len(body) < 28:
            raise ToolError(ctx.M("رشته‌ی رمز ناقص یا دستکاری‌شده است.", "Truncated or corrupted ciphertext."))
        salt, nonce, cipher_text = body[:16], body[16:28], body[28:]
        try:
            plain = AESGCM(derive_aes(password, salt)).decrypt(nonce, cipher_text, None).decode("utf-8")
        except Exception:
            raise ToolError(ctx.M("رمز اشتباه است.", "Wrong password."))
    elif blob.startswith(MAGIC):
        raise ToolError(ctx.M(
            "قالب قدیمی بدون احراز هویت (WGF1) دیگر به دلایل امنیتی پذیرفته نمی‌شود.",
            "Legacy unauthenticated WGF1 text tokens are rejected for security.",
        ))
    else:
        raise ToolError(ctx.M("این رشته با وین‌جنیوس ساخته نشده است.", "Not a WinGenius string."))
    return RunResult(message=ctx.M("متن رمزگشایی شد:", "Decrypted text:"), details=plain)
