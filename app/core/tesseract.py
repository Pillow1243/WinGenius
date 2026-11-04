"""Tesseract discovery shared by the tools, the search index and the settings UI.

Keeping it in `core` matters: `core.indexer` must be able to OCR a scanned PDF without
importing the tool package (that would be a circular import), and every layer should
agree on *which* engine and *which* language folder is in use.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Set

MODULE_HINT_FA = "pip install pytesseract"
MODULE_HINT_EN = "pip install pytesseract"
_LANG_RE = re.compile(r"^[A-Za-z0-9_+]{1,64}$")


def _settings_value(key: str) -> str:
    try:
        from .settings import Settings

        return str(Settings().get(key, "") or "").strip()
    except Exception:
        return ""


def _standard_paths() -> List[str]:
    """Where a Tesseract install usually hides when it is not on PATH."""
    return [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ]


def find_tesseract() -> str:
    """PATH first, then the configured override, then the usual install folders."""
    found = shutil.which("tesseract")
    if found:
        return found
    configured = _settings_value("ocr_tesseract_path")
    if configured:
        cand = Path(configured)
        if (
            not cand.is_symlink()
            and cand.is_file()
            and cand.stem.lower().startswith("tesseract")
        ):
            return str(cand)
    for candidate in _standard_paths():
        if Path(candidate).is_file():
            return candidate
    return ""


def tessdata_dir(exe: str = "") -> Path:
    """The tessdata folder that belongs to the engine we are going to run."""
    configured = _settings_value("ocr_tessdata_path")
    if configured and Path(configured).is_dir():
        return Path(configured)
    env = str(os.environ.get("TESSDATA_PREFIX", "") or "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    exe = exe or find_tesseract()
    if exe:
        beside = Path(exe).parent / "tessdata"
        if beside.is_dir():
            return beside
        # Linux packages keep it under ../share/tesseract-ocr/<version>/tessdata
        share = Path(exe).parent.parent / "share" / "tesseract-ocr"
        if share.is_dir():
            found = sorted(share.glob("*/tessdata")) + sorted(share.glob("tessdata"))
            if found:
                return found[0]
    return Path.home() / ".tessdata"


def installed_langs(exe: str = "") -> Set[str]:
    """Ask the engine itself first — that is the only authoritative answer."""
    exe = exe or find_tesseract()
    if exe:
        try:
            out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20)
            lines = [line.strip() for line in (out.stdout or "").splitlines()]
            langs = {line for line in lines if line and " " not in line and ":" not in line}
            if langs:
                return langs
        except Exception:
            pass
    try:
        return {path.stem for path in tessdata_dir(exe).glob("*.traineddata")}
    except OSError:
        return set()


def pytesseract_ready() -> bool:
    try:
        import pytesseract  # noqa: F401

        return True
    except Exception:
        return False


def ocr_available() -> bool:
    """True when both the engine and the Python wrapper are usable."""
    return bool(find_tesseract()) and pytesseract_ready()


def image_to_text(path: Path, lang: str = "eng", psm: int = 3) -> str:
    """Run OCR on one image/PDF page render; raises on failure (callers degrade)."""
    import pytesseract
    from PIL import Image

    clean_lang = str(lang or "eng").strip()
    if not _LANG_RE.fullmatch(clean_lang):
        raise ValueError(f"invalid OCR language spec: {clean_lang!r}")
    exe = find_tesseract()
    if not exe:
        raise RuntimeError("tesseract engine not found")
    pytesseract.pytesseract.tesseract_cmd = exe
    with Image.open(str(path)) as image:
        if image.mode not in ("L", "RGB"):
            image = image.convert("RGB")
        return pytesseract.image_to_string(image, lang=clean_lang, config=f"--oem 3 --psm {int(psm)}")
