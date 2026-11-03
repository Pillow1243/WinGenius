"""Encrypted vault tools: one portable `.wgv` file for sensitive material."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_DIR, K_FILES, K_MULTILINE, K_PASSWORD, K_SAVE, K_TEXT, Field,
    RunContext, RunResult, ToolError,
)
from ..core.vault import Vault, VaultError, human_size
from ._helpers import out_dir


def _vault(values: Dict[str, Any], ctx: RunContext) -> Vault:
    raw = str(values.get("vault") or "").strip()
    password = str(values.get("password") or "")
    if not raw:
        raise ToolError(ctx.M("مسیر فایل گاوصندوق را انتخاب کنید.",
                              "Choose the vault file path."))
    if not password:
        raise ToolError(ctx.M("رمز گاوصندوق لازم است.", "The vault password is required."))
    return Vault(Path(raw), password)


def _friendly(ctx: RunContext, exc: Exception) -> ToolError:
    text = str(exc)
    if "wrong password" in text.lower() or "damaged" in text.lower():
        return ToolError(ctx.M("رمز اشتباه است یا فایل گاوصندوق آسیب دیده.",
                               "Wrong password, or the vault file is damaged."))
    return ToolError(text)


@tool(
    id="vault_create", category="security", name="ساخت گاوصندوق رمزدار", name_en="Create encrypted vault",
    desc="یک فایل .wgv می‌سازد که فایل‌ها و یادداشت‌های رمزی را با AES-256-GCM نگه می‌دارد.",
    desc_en="Creates a .wgv file that stores files and secrets with AES-256-GCM.",
    icon="shield", order=41, keywords=("vault", "encrypt", "گاوصندوق", "رمز", "امن"),
    fields=[
        Field("vault", K_SAVE, "فایل گاوصندوق", "Vault file", required=True,
              default="vault.wgv", filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
        Field("confirm", K_PASSWORD, "تکرار رمز", "Repeat password", required=True),
        Field("iterations", K_CHOICE, "استحکام", "Strength", default="15",
              choices=[("14", "سریع (N=2¹⁴)", "fast (N=2¹⁴)"),
                       ("15", "استاندارد (N=2¹⁵)", "standard (N=2¹⁵)"),
                       ("17", "سخت‌گیرانه (N=2¹⁷)", "paranoid (N=2¹⁷)")]),
    ],
)
def vault_create(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if str(v.get("password") or "") != str(v.get("confirm") or ""):
        raise ToolError(ctx.M("رمز و تکرارش یکسان نیستند.", "The two passwords do not match."))
    if len(str(v.get("password") or "")) < 6:
        raise ToolError(ctx.M("رمز حداقل ۶ نویسه باشد.", "Use at least 6 characters."))
    target = Path(str(v.get("vault")))
    if target.suffix.lower() != ".wgv":
        target = target.with_suffix(".wgv")
    vault = Vault(target, str(v.get("password")), log_n=int(v.get("iterations") or 15))
    if vault.exists():
        raise ToolError(ctx.M(f"این گاوصندوق از قبل هست: {target}",
                              f"that vault already exists: {target}"))
    try:
        vault.create()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    return RunResult(
        message=ctx.M("گاوصندوق ساخته شد.", "Vault created."),
        outputs=[str(target)],
        details=ctx.M(f"مسیر: {target}\nالگوریتم: AES-256-GCM · کلید از scrypt ({vault.strength()})\n"
                      f"هدر گاوصندوق هم با GCM احراز می‌شود و هر فایل جداگانه رمز می‌شود.",
                      f"Path: {target}\nAES-256-GCM · key from scrypt ({vault.strength()})\n"
                      f"the header is authenticated too and every file is encrypted separately."))


@tool(
    id="vault_add", category="security", name="افزودن به گاوصندوق", name_en="Add files to vault",
    desc="فایل‌ها یا یک یادداشت متنی را داخل گاوصندوق رمزدار می‌گذارد.",
    desc_en="Puts files, or a text note, inside the encrypted vault.",
    icon="lock", order=42, keywords=("vault", "add", "گاوصندوق", "افزودن", "یادداشت"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
        Field("files", K_FILES, "فایل‌ها", "Files"),
        Field("note_name", K_TEXT, "نام یادداشت", "Note name", default="",
              placeholder="note.txt"),
        Field("note_text", K_MULTILINE, "متن یادداشت", "Note text", default="", sensitive=True),
    ],
)
def vault_add(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    vault = _vault(v, ctx)
    try:
        vault.unlock()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    raw_files = v.get("files") or []
    if isinstance(raw_files, str):
        raw_files = [part for chunk in raw_files.splitlines() for part in chunk.split("|")]
    files = [Path(str(item).strip()) for item in raw_files if str(item).strip()]
    added: List[str] = []
    if files:
        added.extend(vault.add(files))
    note_text = str(v.get("note_text") or "")
    if note_text.strip():
        name = str(v.get("note_name") or "").strip() or "note.txt"
        added.append(vault.add_text(name, note_text))
    if not added:
        raise ToolError(ctx.M("نه فایلی انتخاب شد و نه یادداشتی نوشته شد.",
                              "Neither files nor a note were provided."))
    info = vault.info()
    return RunResult(
        message=ctx.M(f"{len(added)} مورد رمز شد.", f"{len(added)} item(s) encrypted."),
        outputs=[str(vault.path)],
        details=ctx.M(f"افزوده شد: {'، '.join(added)}\nمجموع: {info['entries']} مورد · "
                      f"{human_size(info['bytes'])} محتوا · فایل {human_size(info['size'])}",
                      f"added: {', '.join(added)}\n{info['entries']} item(s) · "
                      f"{human_size(info['bytes'])} of data · {human_size(info['size'])} file"))


@tool(
    id="vault_list", category="security", name="فهرست گاوصندوق", name_en="List vault contents",
    desc="محتویات گاوصندوق را بعد از باز کردن رمز نشان می‌دهد و سلامت همه‌ی رکوردها را می‌سنجد.",
    desc_en="Unlocks the vault and lists everything inside, verifying each record.",
    icon="list", order=43, keywords=("vault", "list", "گاوصندوق", "فهرست"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
    ],
)
def vault_list(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    vault = _vault(v, ctx)
    try:
        entries = vault.unlock()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    if not entries:
        return RunResult(message=ctx.M("گاوصندوق خالی است.", "The vault is empty."))
    verify = vault.verify()
    lines = [f"• {entry['name']} — {human_size(int(entry.get('size', 0)))}" for entry in entries]
    lines.append("")
    lines.append(ctx.M(f"بررسی سلامت: {verify['checked']} رکورد رمزگشایی شد",
                       f"integrity check: {verify['checked']} record(s) decrypted"))
    if verify["problems"]:
        lines.extend(f"! {problem}" for problem in verify["problems"])
    info = vault.info()
    return RunResult(
        message=ctx.M(f"{len(entries)} مورد در گاوصندوق ({human_size(info['bytes'])}).",
                      f"{len(entries)} item(s) in the vault ({human_size(info['bytes'])})."),
        details="\n".join(lines))


@tool(
    id="vault_extract", category="security", name="استخراج از گاوصندوق", name_en="Extract from vault",
    desc="همه یا بخشی از محتویات گاوصندوق را در پوشه‌ی خروجی باز می‌کند.",
    desc_en="Writes all (or the named) vault entries back out to a folder.",
    icon="download", order=44, keywords=("vault", "extract", "گاوصندوق", "استخراج", "بازکردن"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
        Field("names", K_TEXT, "نام‌ها", "Entry names", default="",
              placeholder="a.txt, b.pdf  — خالی = همه"),
        Field("out", K_DIR, "پوشه‌ی خروجی", "Output folder", default=""),
    ],
)
def vault_extract(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    vault = _vault(v, ctx)
    try:
        vault.unlock()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    requested = [item.strip() for item in str(v.get("names") or "").replace("،", ",").split(",")
                 if item.strip()]
    target_dir = Path(str(v.get("out"))) if str(v.get("out") or "").strip() else out_dir(ctx, "vault")
    try:
        written = vault.extract(requested or vault.names(), target_dir)
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    if not written:
        return RunResult(message=ctx.M("هیچ موردی پیدا نشد.", "Nothing matched."))
    return RunResult(
        message=ctx.M(f"{len(written)} فایل استخراج شد.", f"{len(written)} file(s) extracted."),
        outputs=[str(path) for path in written],
        details="\n".join(str(path) for path in written))


@tool(
    id="vault_remove", category="security", name="حذف از گاوصندوق", name_en="Remove from vault",
    danger=True,
    desc="یک یا چند مورد را از گاوصندوق حذف می‌کند (بدون امکان بازگردانی).",
    desc_en="Deletes one or more entries from the vault (not recoverable).",
    icon="trash", order=45, keywords=("vault", "remove", "گاوصندوق", "حذف"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
        Field("names", K_TEXT, "نام‌ها", "Entry names", required=True),
    ],
)
def vault_remove(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    vault = _vault(v, ctx)
    try:
        vault.unlock()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    names = [item.strip() for item in str(v.get("names") or "").replace("،", ",").split(",")
             if item.strip()]
    removed = vault.remove(names)
    return RunResult(message=ctx.M(f"{removed} مورد حذف شد.", f"{removed} entry(ies) removed."),
                     outputs=[str(vault.path)])


@tool(
    id="vault_password", category="security", name="تغییر رمز گاوصندوق", name_en="Change vault password",
    desc="کلید گاوصندوق را با رمز تازه از نو می‌سازد (محتویات دوباره رمز می‌شوند).",
    desc_en="Re-keys the vault with a new password (everything is re-encrypted).",
    icon="key", order=46, keywords=("vault", "password", "گاوصندوق", "رمز", "تغییر"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز فعلی", "Current password", required=True),
        Field("new_password", K_PASSWORD, "رمز تازه", "New password", required=True),
        Field("confirm", K_PASSWORD, "تکرار رمز تازه", "Repeat new password", required=True),
    ],
)
def vault_password(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    if str(v.get("new_password") or "") != str(v.get("confirm") or ""):
        raise ToolError(ctx.M("رمزهای تازه یکسان نیستند.", "The new passwords do not match."))
    vault = _vault(v, ctx)
    try:
        vault.unlock()
        vault.change_password(str(v.get("new_password")))
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    return RunResult(message=ctx.M("رمز گاوصندوق عوض شد و همه‌چیز دوباره رمز شد.",
                                   "Vault password changed; everything re-encrypted."),
                     outputs=[str(vault.path)])


@tool(
    id="vault_upgrade", category="security", name="ارتقای گاوصندوق", name_en="Upgrade vault format",
    desc="گاوصندوق قدیمی (PBKDF2) را پس از بررسی رمز، به قالب تازه با کلید scrypt تبدیل می‌کند.",
    desc_en="Converts an old PBKDF2 vault to the current scrypt format after checking the password.",
    icon="shield", order=47, keywords=("vault", "upgrade", "گاوصندوق", "ارتقا", "scrypt"),
    fields=[
        Field("vault", K_TEXT, "فایل گاوصندوق", "Vault file", required=True, filt="WinGenius Vault (*.wgv)"),
        Field("password", K_PASSWORD, "رمز", "Password", required=True),
    ],
)
def vault_upgrade(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    vault = _vault(v, ctx)
    try:
        vault.unlock()
        before = vault.format_version()
        outcome = vault.upgrade()
    except VaultError as exc:
        raise _friendly(ctx, exc) from exc
    if outcome == "already-v2":
        return RunResult(message=ctx.M("این گاوصندوق از قبل با قالب تازه (scrypt) ساخته شده.",
                                       "This vault already uses the current scrypt format."),
                         outputs=[str(vault.path)],
                         details=ctx.M(f"استحکام: {vault.strength()}", f"strength: {vault.strength()}"))
    info = vault.info()
    return RunResult(
        message=ctx.M("گاوصندوق به قالب تازه ارتقا یافت.", "Vault upgraded to the current format."),
        outputs=[str(vault.path)],
        details=ctx.M(f"قالب {before} → {vault.format_version()} · {vault.strength()}\n"
                      f"{info['entries']} مورد دوباره رمز شد.",
                      f"format {before} → {vault.format_version()} · {vault.strength()}\n"
                      f"{info['entries']} item(s) re-encrypted."))
