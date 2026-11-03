"""Digital signatures for PDFs: sign with a PKCS#12 certificate, then verify.

Deliberately optional: the signing engine (`pyhanko`) is a heavy dependency, so the
tools explain how to install it instead of breaking the app. Verification degrades
gracefully too — a self-signed signature is reported as "cryptographically intact
but untrusted", which is the truth, not a failure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_FILE, K_INT, K_PASSWORD, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size
from ._helpers import first_file, save_target

PDF_FILTER = "PDF (*.pdf)"
P12_FILTER = "گواهی (*.p12 *.pfx *.pem);;همه فایل‌ها (*)"

INSTALL_HINT_FA = (
    "موتور امضای دیجیتال pyHanko در دسترس نیست؛ برنامه را دوباره نصب کنید:\n"
    "    pip install pyhanko\n\n"
    "امضا با گواهی PKCS#12 (فایل .p12 یا .pfx) انجام می‌شود؛ گواهی معتبر را از "
    "مرکز صدور گواهی خودتان بگیرید."
)
INSTALL_HINT_EN = (
    "The pyHanko signing engine is unavailable; reinstall WinGenius:\n"
    "    pip install pyhanko\n\n"
    "Signing uses a PKCS#12 certificate (.p12 or .pfx) issued by your CA."
)


def _pyhanko(ctx: RunContext):
    # pyHanko shouts every certificate-path failure at stderr; keep the console clean
    # and leave the detail in our own log file.
    import logging

    for name in ("pyhanko", "pyhanko_certvalidator", "asn1crypto"):
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        from pyhanko.sign import signers  # noqa: F401
        from pyhanko.pdf_utils.reader import PdfFileReader  # noqa: F401
    except Exception:
        raise ToolError(ctx.M(INSTALL_HINT_FA, INSTALL_HINT_EN))


def _read_p12(path: Path, password: str, ctx: RunContext):
    """Load a PKCS#12 bundle, with a helpful message for the two classic mistakes."""
    if not path.exists():
        raise ToolError(ctx.M(f"فایل گواهی پیدا نشد: {path}", f"Certificate not found: {path}"))
    try:
        from pyhanko.sign import signers

        signer = signers.SimpleSigner.load_pkcs12(str(path), passphrase=password.encode("utf-8"))
    except Exception as exc:
        signer = None
        ctx.log(f"pkcs12 load failed: {exc}")
    if signer is None:
        raise ToolError(ctx.M(
            "گواهی باز نشد: رمز آن درست نیست یا فایل .p12/.pfx آسیب دیده است.",
            "The certificate could not be opened: wrong password, or a damaged .p12/.pfx file.",
        ))
    return signer


def _coverage_note(coverage_name: str, ctx: RunContext) -> Tuple[bool, str]:
    """(file_untouched, human note).

    `ENTIRE_FILE` means the signature still covers every byte of the document. Anything
    else (`ENTIRE_REVISION`, …) means somebody saved a change *after* signing: the
    signature itself is intact, but the document has moved on.
    """
    untouched = coverage_name.upper() == "ENTIRE_FILE"
    if untouched:
        return True, ctx.M("فایل بعد از امضا دست‌نخورده است",
                           "the file is unchanged since signing")
    return False, ctx.M("هشدار: بعد از امضا تغییراتی روی فایل ذخیره شده است",
                        "warning: changes were saved after signing")


_SUBJECT_FIELDS = ("common_name", "organization_name", "organizational_unit_name",
                   "email_address")


def _cert_subject(reader_cert) -> str:
    """A readable 'CN — O' line instead of asn1crypto's repr."""
    subject = getattr(reader_cert, "subject", None)
    try:
        native = subject.native
        if isinstance(native, dict):
            for key in _SUBJECT_FIELDS:
                value = native.get(key)
                if value:
                    return str(value)
            if native:
                first = next(iter(native.items()))
                return f"{first[0]}: {first[1]}"
    except Exception:
        pass
    try:
        return str(subject)
    except Exception:
        return "?"


@tool(
    id="pdf_sign", category="pdf", name="امضای دیجیتال PDF",
    name_en="Sign PDF digitally",
    desc=("PDF را با گواهی PKCS#12 امضا می‌کند (امضای رمزنگاری‌شده، نه عکسِ امضا). "
          "فایل خروجی هر تغییری را آشکار می‌کند."),
    desc_en=("Sign a PDF with a PKCS#12 certificate — a real cryptographic signature, "
             "not a picture of one. Any later edit becomes visible."),
    icon="key", order=124,
    keywords=("sign pdf", "digital signature", "امضای دیجیتال", "امضا pdf", "p12",
              "pfx", "گواهی", "certificate"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
        Field("cert", K_FILE, "فایل گواهی (.p12 / .pfx)", "Certificate (.p12 / .pfx)",
              required=True, filt=P12_FILTER),
        Field("password", K_PASSWORD, "رمز گواهی", "Certificate password", required=True,
              help="هیچ‌جا ذخیره نمی‌شود؛ فقط برای باز کردن گواهی در همین اجرا استفاده می‌شود.",
              help_en="Never stored; used only to unlock the certificate for this run."),
        Field("reason", K_TEXT, "دلیل امضا", "Reason", default="", placeholder="تأیید نسخه‌ی نهایی",
              placeholder_en="Approved final version"),
        Field("location", K_TEXT, "محل", "Location", default="", placeholder="تهران"),
        Field("page", K_INT, "شماره‌ی صفحه‌ی مهر", "Stamp page", default=1, minimum=1, maximum=9999,
              help="شماره از ۱؛ اگر بیشتر از تعداد صفحه‌ها باشد، صفحه‌ی آخر استفاده می‌شود.",
              help_en="1-based; clamped to the last page."),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt=PDF_FILTER),
    ],
)
def pdf_sign(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    # Check the inputs first: "your certificate file is missing" is a more useful
    # answer than "install pyHanko", and it keeps the message honest when both apply.
    src = first_file(v, "src", ctx)
    cert_path = first_file(v, "cert", ctx)
    password = str(v.get("password") or "")
    if not password:
        raise ToolError(ctx.M("رمز گواهی خالی است.", "The certificate password is empty."))

    _pyhanko(ctx)
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigFieldSpec, append_signature_field

    signer = _read_p12(cert_path, password, ctx)

    import pymupdf

    try:
        with pymupdf.open(str(src)) as probe:
            pages = probe.page_count
    except Exception as exc:
        raise ToolError(ctx.M(f"فایل PDF باز نشد: {exc}", f"Could not open the PDF: {exc}"))
    page_index = max(1, min(int(v.get("page") or 1), pages)) - 1

    dest = save_target(ctx, str(v.get("out") or "").strip(), f"{src.stem}_signed.pdf")
    if dest.resolve() == src.resolve():
        raise ToolError(ctx.M("فایل خروجی نمی‌تواند همان فایل ورودی باشد.",
                              "The output file cannot be the input file."))

    ctx.progress(1, 3, ctx.M("افزودن فیلد امضا", "Adding the signature field"))
    metadata = signers.PdfSignatureMetadata(
        field_name="Signature1",
        reason=str(v.get("reason") or "") or None,
        location=str(v.get("location") or "") or None,
        md_algorithm="sha256",
    )
    try:
        with open(src, "rb") as infile, open(dest, "wb") as outfile:
            writer = IncrementalPdfFileWriter(infile)
            box = (28, 28, 240, 92) if page_index == 0 else None
            append_signature_field(
                writer,
                SigFieldSpec(sig_field_name="Signature1", on_page=page_index,
                             box=box or (28, 28, 240, 92)),
            )
            signers.sign_pdf(writer, signature_meta=metadata, signer=signer, output=outfile)
    except ToolError:
        raise
    except Exception as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise ToolError(ctx.M(
            f"امضا انجام نشد: {exc}\n"
            "اگر فایل قبلاً امضا شده یا فرم دارد، نسخه‌ی خروجی تازه بسازید و دوباره امتحان کنید.",
            f"Signing failed: {exc}\n"
            "If the file already has a signature or a form, flatten it first and retry.",
        ))

    ctx.progress(3, 3, ctx.M("بررسی امضا", "Verifying the signature"))
    good = _verify_file(dest, ctx)
    message = ctx.M(f"امضا شد · {human_size(dest.stat().st_size)}",
                    f"Signed · {human_size(dest.stat().st_size)}")
    details = []
    if good is not None:
        intact, valid, trusted, subject, coverage = good
        message += ctx.M(" · امضا سالم است" if intact else " · امضا سالم نیست",
                         " · signature intact" if intact else " · signature is NOT intact")
        untouched, note = _coverage_note(coverage, ctx)
        message += " · " + note
        details.append(f"{ctx.M('امضاکننده', 'Signer')}: {subject}")
        details.append(f"{ctx.M('پوشش', 'Coverage')}: {coverage}"
                       + ("" if untouched else ctx.M(" (فایل تغییر کرده)", " (file changed)")))
        details.append(f"{ctx.M('سالم', 'Intact')}: {intact} · {ctx.M('معتبر', 'Valid')}: {valid}"
                       f" · {ctx.M('مورد اعتماد', 'Trusted')}: {trusted}")
        if not trusted:
            details.append(ctx.M(
                "«مورد اعتماد» یعنی گواهی در مخزن ریشه‌ی سیستم شما شناخته شده است؛ "
                "برای گواهی خودساخته این طبیعی است.",
                "“Trusted” means the certificate chains to your system trust store; "
                "for a self-signed certificate this is expected.",
            ))
    return RunResult(message=message, details="\n".join(details),
                     outputs=[str(dest)], open_target=str(dest.parent))


def _verify_file(path: Path, ctx: RunContext) -> Optional[Tuple[bool, bool, bool, str, str]]:
    """(intact, valid, trusted, signer subject, coverage) for the first signature."""
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature

        with open(path, "rb") as handle:
            reader = PdfFileReader(handle, strict=False)
            signatures = reader.embedded_signatures
            if not signatures:
                return None
            status = validate_pdf_signature(signatures[0], skip_diff=True)
            return (bool(status.intact), bool(status.valid), bool(status.trusted),
                    _cert_subject(status.signing_cert), status.coverage.name)
    except Exception as exc:
        ctx.log(f"signature verification failed: {exc}")
        return None


@tool(
    id="pdf_signature_check", category="pdf", name="بررسی امضای PDF",
    name_en="Check PDF signatures",
    desc=("همه‌ی امضاهای دیجیتال یک PDF را فهرست می‌کند و می‌گوید فایل بعد از امضا "
          "تغییر کرده یا نه."),
    desc_en=("Lists every digital signature in a PDF and tells you whether the file "
             "changed after signing."),
    icon="check", order=125,
    keywords=("verify signature", "check signature", "بررسی امضا", "امضا معتبر است؟",
              "tampered pdf", "دستکاری"),
    fields=[
        Field("src", K_FILE, "فایل PDF", "PDF file", required=True, filt=PDF_FILTER),
    ],
)
def pdf_signature_check(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _pyhanko(ctx)
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    src = first_file(v, "src", ctx)
    try:
        handle = open(src, "rb")
    except OSError as exc:
        raise ToolError(ctx.M(f"فایل باز نشد: {exc}", f"Could not open the file: {exc}"))
    with handle:
        reader = PdfFileReader(handle, strict=False)
        signatures = reader.embedded_signatures
        if not signatures:
            raise ToolError(ctx.M("این PDF هیچ امضای دیجیتالی ندارد.",
                                  "This PDF has no digital signature."))
        lines: List[str] = []
        all_intact = True
        all_unchanged = True
        trusted_any = False
        for index, signature in enumerate(signatures, start=1):
            try:
                status = validate_pdf_signature(signature, skip_diff=True)
            except Exception as exc:
                lines.append(f"{index}) {ctx.M('بررسی نشد', 'could not be checked')}: {exc}")
                all_intact = False
                all_unchanged = False
                continue
            all_intact = all_intact and bool(status.intact)
            unchanged, note = _coverage_note(status.coverage.name, ctx)
            all_unchanged = all_unchanged and unchanged
            trusted_any = trusted_any or bool(status.trusted)
            signed_at = ""
            try:
                if status.signer_reported_dt:
                    signed_at = status.signer_reported_dt.isoformat(timespec="seconds")
            except Exception:
                signed_at = ""
            lines.append("\n".join(filter(None, [
                f"{index}) {_cert_subject(status.signing_cert)}",
                f"   {ctx.M('سالم', 'Intact')}: {status.intact} · "
                f"{ctx.M('معتبر', 'Valid')}: {status.valid} · "
                f"{ctx.M('مورد اعتماد', 'Trusted')}: {status.trusted}",
                f"   {ctx.M('پوشش', 'Coverage')}: {status.coverage.name} — {note}",
                f"   {ctx.M('زمان امضا', 'Signed at')}: {signed_at}" if signed_at else "",
            ])))
        unchanged_files = all_intact and all_unchanged
        message = ctx.M(
            f"{len(signatures)} امضا · " + ("فایل دست‌نخورده است" if unchanged_files
                                           else "هشدار: فایل بعد از امضا تغییر کرده"),
            f"{len(signatures)} signature(s) · " + ("file is unchanged" if unchanged_files
                                                   else "warning: the file changed after signing"),
        )
        if not trusted_any:
            message += ctx.M(" · گواهی خودساخته یا ناشناخته",
                             " · self-signed or unknown certificate")
        return RunResult(message=message, details="\n".join(lines))
