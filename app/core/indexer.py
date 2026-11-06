"""Local full-text search over your own documents (SQLite FTS5).

Everything stays on the machine: the index is a single SQLite file in the app
data folder, filled with the text of the files you choose.  Persian and English
both work; the query language supports quotes, `-word` and `word*`.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .logger import get_logger
from .paths import data_dir

log = get_logger("indexer")

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml",
    ".yml", ".ini", ".cfg", ".conf", ".toml", ".html", ".htm", ".css", ".js", ".ts", ".py",
    ".ps1", ".bat", ".cmd", ".sh", ".sql", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
    ".php", ".rb", ".kt", ".swift", ".gitignore", ".tex", ".srt", ".vtt",
}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
SUPPORTED = TEXT_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES

SENSITIVE_SUFFIXES = {
    ".env", ".pem", ".key", ".pfx", ".p12", ".kdbx", ".wgv", ".wgenc", ".jks", ".keystore",
}
SENSITIVE_NAMES = {
    ".env", ".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "id_ecdsa",
    "api_token.txt", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea",
             ".vs", ".gradle", "target", ".mypy_cache", ".pytest_cache", ".cache", ".ssh", ".gnupg"}

MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_CHARS = 400_000

# Scanned PDFs have no text layer at all: with Tesseract installed we render the first
# pages and index the recognised text, which is what makes "why is my scan not in the
# search results?" go away.
OCR_MIN_CHARS = 24         # below this, a PDF is treated as "no text layer"
OCR_MAX_PAGES = 5
OCR_DPI = 200


def ocr_enabled() -> bool:
    try:
        from .settings import Settings

        wanted = Settings().get("index/ocr", True)
    except Exception:
        wanted = True
    if not wanted:
        return False
    from .tesseract import ocr_available

    return ocr_available()


@dataclass
class Hit:
    path: str
    snippet: str = ""
    size: int = 0
    modified: float = 0.0

    @property
    def name(self) -> str:
        return Path(self.path).name

    def label(self, query: str, lang: str = "fa") -> str:
        return f"{self.name} — {self.snippet}".replace("\n", " ") if self.snippet else self.name


def excluded_dirs() -> set:
    """User-defined folders that must never enter the (plaintext) FTS index."""
    try:
        from .settings import Settings

        raw = str(Settings().get("index/exclude_folders", "") or "")
    except Exception:                                   # pragma: no cover
        raw = ""
    parts = [part.strip().lower().replace("\\", "/")
             for part in raw.replace(";", "\n").splitlines() if part.strip()]
    return set(parts)


def _excluded_by(path: Path, extra: set) -> bool:
    try:
        full = str(path.resolve()).lower().replace("\\", "/")
    except OSError:                                     # pragma: no cover
        full = str(path).lower().replace("\\", "/")
    return any(part and part in full for part in extra)


def index_path() -> Path:
    return data_dir() / "search_index.sqlite3"


def _connect() -> sqlite3.Connection:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS docs (
               path TEXT PRIMARY KEY,
               mtime REAL, size INTEGER, chars INTEGER, added REAL, language TEXT)""")
    try:
        conn.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                   path UNINDEXED, body, tokenize='unicode61 remove_diacritics 2')""")
    except sqlite3.OperationalError as exc:  # pragma: no cover - depends on build flags
        log.warning("FTS5 unavailable: %s", exc)
        raise
    return conn


def extract_text(path: Path) -> str:
    """Best-effort text extraction for one file."""
    return extract_text_with_meta(path)[0]


def extract_text_with_meta(path: Path) -> tuple:
    """(text, used_ocr) — the flag lets the crawl report how many scans it rescued."""
    suffix = path.suffix.lower()
    used_ocr = False
    try:
        if suffix in PDF_SUFFIXES:
            reader = _pdf_reader(path)
            if _looks_scanned(path, reader) and ocr_enabled():
                recognised = ocr_pdf(path)
                if recognised.strip():
                    log.info("indexed a scanned pdf through OCR: %s", path.name)
                    return recognised[:MAX_CHARS], True
            return reader[:MAX_CHARS], False
        if suffix in DOCX_SUFFIXES:
            return _docx_text(path)[:MAX_CHARS], False
        if suffix in TEXT_SUFFIXES:
            with path.open("rb") as handle:
                raw = handle.read(MAX_FILE_BYTES)
            for encoding in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
                try:
                    return raw.decode(encoding)[:MAX_CHARS], False
                except (UnicodeDecodeError, LookupError):
                    continue
    except Exception as exc:  # pragma: no cover - a broken file must not stop the crawl
        log.info("cannot extract %s: %s", path, exc)
    return "", used_ocr


def _pdf_reader(path: Path) -> str:
    try:
        from . import persian_pdf  # repaired font maps + logical-order Persian text

        return persian_pdf.pdf_text(path)
    except Exception:
        pass
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(path)) as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        log.info("cannot read pdf %s: %s", path, exc)
        return ""


def _looks_scanned(path: Path, text: str) -> bool:
    """A scan = almost no text layer, but a big picture covering the page.

    Checking the picture matters: a short one-line PDF (a stamp, a form header) is not a
    scan and must not cost five seconds of OCR.
    """
    if len(text.strip()) >= OCR_MIN_CHARS:
        return False
    try:
        import fitz

        with fitz.open(str(path)) as doc:
            for index in range(min(3, doc.page_count)):
                page = doc[index]
                area = abs(page.rect.width * page.rect.height) or 1
                for info in page.get_images(full=True):
                    try:
                        for rect in page.get_image_rects(info[0]):
                            if abs(rect.width * rect.height) / area >= 0.35:
                                return True
                    except Exception:
                        continue
    except Exception:
        return False
    return False


def ocr_pdf(path: Path, max_pages: int = OCR_MAX_PAGES, dpi: int = OCR_DPI) -> str:
    """Render the first pages of a PDF and OCR them; '' when nothing is readable."""
    try:
        import fitz

        from .tesseract import image_to_text, installed_langs
    except Exception:
        return ""
    langs = installed_langs()
    # Persian text needs the fas pack; fall back to English when it is missing.
    language = "fas+eng" if "fas" in langs else "eng"
    parts: List[str] = []
    temp_dir = data_dir() / "cache"
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    try:
        with fitz.open(str(path)) as doc:
            for index in range(min(max_pages, doc.page_count)):
                pixmap = doc[index].get_pixmap(dpi=dpi)
                fd, tmp_name = tempfile.mkstemp(prefix="ocr_", suffix=".png", dir=temp_dir)
                os.close(fd)
                image_path = Path(tmp_name)
                try:
                    pixmap.save(str(image_path))
                    parts.append(image_to_text(image_path, lang=language, psm=6))
                finally:
                    try:
                        image_path.unlink()
                    except OSError:
                        pass
    except Exception as exc:               # a broken scan must not stop the crawl
        log.info("ocr failed for %s: %s", path, exc)
        return ""
    return "\n".join(part for part in parts if part.strip())


def _docx_text(path: Path) -> str:
    try:
        import docx

        document = docx.Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:
        log.info("cannot read docx %s: %s", path, exc)
        return ""


def guess_language(text: str) -> str:
    persian = sum(1 for char in text[:4000] if "\u0600" <= char <= "\u06ff")
    latin = sum(1 for char in text[:4000] if char.isascii() and char.isalpha())
    if persian > latin:
        return "fa"
    if latin:
        return "en"
    return "-"


def _is_sensitive_file(path: Path) -> bool:
    lower_name = path.name.lower()
    if lower_name in SENSITIVE_NAMES or lower_name.startswith(".env."):
        return True
    return path.suffix.lower() in SENSITIVE_SUFFIXES


# --------------------------------------------------------------------- build
def build(folders: Iterable[str], patterns: str = "", recursive: bool = True,
          on_progress: Optional[Callable[[int, int, str], None]] = None,
          cancel: Optional[Callable[[], bool]] = None,
          limits: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Index every supported file below `folders`; returns counters."""
    folders = [str(folder) for folder in folders if str(folder).strip()]
    if not folders:
        raise ValueError("no folder")
    wanted = [part.strip().lower() for part in (patterns or "").replace(";", ",").split(",") if part.strip()]
    extra_excludes = excluded_dirs()
    files: List[Path] = []
    for folder in folders:
        root = Path(folder)
        if root.is_symlink():
            continue
        if root.is_file():
            if (not _is_sensitive_file(root) and not _excluded_by(root, extra_excludes)
                    and root.suffix.lower() in SUPPORTED and root.stat().st_size <= MAX_FILE_BYTES):
                files.append(root)
            continue
        if not root.exists():
            continue
        if recursive:
            candidates: List[Path] = []
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
                ]
                for fname in filenames:
                    candidates.append(Path(dirpath) / fname)
        else:
            candidates = list(root.glob("*"))
        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if _is_sensitive_file(path):
                    continue
                if any(part.lower() in SKIP_DIRS for part in path.parts):
                    continue
                if extra_excludes and _excluded_by(path, extra_excludes):
                    continue
                suffix = path.suffix.lower()
                if wanted:
                    if not any(suffix == pattern.lstrip("*") or pattern in path.name.lower()
                               for pattern in wanted):
                        continue
                elif suffix not in SUPPORTED:
                    continue
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                files.append(path)
            except OSError:
                continue
    counters = {"files": len(files), "indexed": 0, "skipped": 0, "chars": 0, "ocr": 0}
    conn = _connect()
    try:
        for position, path in enumerate(files, start=1):
            if cancel and cancel():
                break
            if on_progress:
                on_progress(position, len(files), path.name)
            try:
                stat = path.stat()
            except OSError:
                counters["skipped"] += 1
                continue
            key = str(path.resolve())
            row = conn.execute("SELECT mtime, size FROM docs WHERE path=?", (key,)).fetchone()
            if row and float(row[0]) == stat.st_mtime and int(row[1]) == stat.st_size:
                counters["skipped"] += 1
                continue
            body, used_ocr = extract_text_with_meta(path)
            if not body.strip():
                # A changed file that became empty must not retain its old contents.
                conn.execute("DELETE FROM docs_fts WHERE path=?", (key,))
                conn.execute("DELETE FROM docs WHERE path=?", (key,))
                counters["skipped"] += 1
                continue
            conn.execute("DELETE FROM docs_fts WHERE path=?", (key,))
            conn.execute(
                "INSERT OR REPLACE INTO docs(path, mtime, size, chars, added, language)"
                " VALUES(?,?,?,?,?,?)",
                (key, stat.st_mtime, stat.st_size, len(body), time.time(), guess_language(body)))
            conn.execute("INSERT INTO docs_fts(path, body) VALUES(?,?)", (key, body))
            counters["indexed"] += 1
            counters["chars"] += len(body)
            if used_ocr:
                counters["ocr"] += 1
            if position % 25 == 0:
                conn.commit()
        # Only prune missing paths under roots actually scanned, never while cancelled
        # or when the root itself is missing (e.g. a disconnected drive).
        if not (cancel and cancel()):
            roots = [Path(folder).resolve() for folder in folders
                     if Path(folder).is_dir() and not Path(folder).is_symlink()]
            for (key,) in conn.execute("SELECT path FROM docs").fetchall():
                indexed = Path(key)
                in_scope = any(indexed.is_relative_to(root) if recursive else indexed.parent == root
                               for root in roots)
                if in_scope and not indexed.exists():
                    conn.execute("DELETE FROM docs_fts WHERE path=?", (key,))
                    conn.execute("DELETE FROM docs WHERE path=?", (key,))
        conn.commit()
    finally:
        conn.close()
    return counters


# -------------------------------------------------------------------- query
def _fts_quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _clean_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""
    tokens = []
    for token in re.findall(r'"[^"]*"\*?|\S+', text):
        bare = token.strip('"')
        if not bare:
            continue
        if token.startswith("-") and len(token) > 1:
            sub = token[1:].strip('"')
            if sub:
                tokens.append(f"NOT {_fts_quote(sub)}")
        elif token.endswith("*") and len(bare) >= 2:
            prefix = bare[:-1] if bare.endswith("*") else bare
            if prefix:
                tokens.append(f"{_fts_quote(prefix)}*")
        else:
            tokens.append(_fts_quote(bare))
    return " ".join(tokens)


def search(query: str, limit: int = 25) -> List[Hit]:
    cleaned = _clean_query(query)
    if not cleaned:
        return []
    try:
        conn = _connect()
    except sqlite3.OperationalError:
        return []
    hits: List[Hit] = []
    try:
        rows = conn.execute(
            """SELECT f.path, snippet(docs_fts, 1, '«', '»', '…', 14), d.size, d.mtime
                 FROM docs_fts f LEFT JOIN docs d ON d.path = f.path
                WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?""",
            (cleaned, max(1, int(limit)))).fetchall()
        for path, snippet, size, mtime in rows:
            hits.append(Hit(path=path, snippet=(snippet or "").replace("\n", " "),
                            size=int(size or 0), modified=float(mtime or 0)))
    except sqlite3.OperationalError as exc:
        log.info("bad query %r: %s", query, exc)
        return []
    finally:
        conn.close()
    return hits


def stats() -> Dict[str, int]:
    try:
        conn = _connect()
    except sqlite3.OperationalError:
        return {"docs": 0, "chars": 0, "size": 0, "languages": 0}
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(chars),0), COALESCE(SUM(size),0) FROM docs").fetchone()
        langs = conn.execute("SELECT COUNT(DISTINCT language) FROM docs").fetchone()[0]
    finally:
        conn.close()
    return {"docs": int(row[0]), "chars": int(row[1]), "size": int(row[2]), "languages": int(langs)}


def clear() -> None:
    try:
        conn = _connect()
    except sqlite3.OperationalError:
        return
    try:
        conn.execute("DELETE FROM docs_fts")
        conn.execute("DELETE FROM docs")
        conn.commit()
        try:
            conn.isolation_level = None
            conn.execute("VACUUM")
        except sqlite3.Error:
            pass
    finally:
        conn.close()


def remove(path: str) -> bool:
    key = str(Path(path).resolve())
    conn = _connect()
    try:
        conn.execute("DELETE FROM docs_fts WHERE path=?", (key,))
        cursor = conn.execute("DELETE FROM docs WHERE path=?", (key,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def report(query: str, hits: List[Hit], lang: str = "fa") -> str:
    """Plain-text report, used by the index_search tool."""
    if not hits:
        return ("چیزی پیدا نشد." if lang != "en" else "Nothing found.")
    head = (f"{len(hits)} نتیجه برای «{query}»" if lang != "en"
            else f"{len(hits)} result(s) for “{query}”")
    lines = [head, "-" * 46]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"{index}. {hit.path}")
        if hit.snippet:
            lines.append(f"   … {hit.snippet}")
    return "\n".join(lines)
