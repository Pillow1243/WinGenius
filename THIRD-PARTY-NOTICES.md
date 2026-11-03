# Third-party notices — WinGenius 5.0.1

WinGenius itself is released under the **GNU Affero General Public License v3.0 or
later** (see `LICENSE`). That choice is not decorative: the app renders and edits
PDFs with **PyMuPDF**, which is AGPL-3.0 (or a paid Artifex commercial licence),
so a permissively-licensed wrapper around it would be a licence violation the
moment an `.exe` is handed to somebody else.

If you need to ship WinGenius under a permissive licence, read
[“How to move to a permissive licence”](#how-to-move-to-a-permissive-licence) at
the bottom — it is a real, small piece of work, not a hand-wave.

## Runtime dependencies

| Package | License | Why it is here |
|---|---|---|
| `PySide6-Essentials` | LGPL-3.0 / GPL-2.0 / GPL-3.0 (commercial available) | the whole UI (Qt 6) |
| `PyMuPDF` | **AGPL-3.0** or Artifex commercial | PDF rendering, page operations, PDF → image |
| `pypdf` | BSD-3-Clause | PDF structure work that does not need rendering |
| `reportlab` | BSD-3-Clause | creating PDFs (text → PDF, images → PDF) |
| `Pillow` | MIT-CMU | every image operation |
| `python-docx` | MIT | Word documents |
| `openpyxl` | MIT | Excel workbooks |
| `qrcode` | BSD | QR generation |
| `python-barcode` | MIT | linear barcodes |
| `cryptography` | Apache-2.0 OR BSD-3-Clause | AES-256-GCM, scrypt, PBKDF2 (vault, file encryption) |
| `pyzipper` | MIT | AES-encrypted ZIP archives |
| `psutil` | BSD-3-Clause | processes, memory, disks |
| `requests` | Apache-2.0 | HTTP-based tools |
| `markdown` | BSD-3-Clause | Markdown → HTML |
| `imageio-ffmpeg` | BSD-2-Clause | ships an ffmpeg binary used by the media tools |
| `pytesseract` | Apache-2.0 | OCR front-end (needs the Tesseract engine) |
| `dnspython` | ISC | MX/NS/TXT records |
| `opencv-python-headless` | Apache-2.0 (OpenCV) | QR decoding |
| `pyHanko` | MIT | real PDF digital signatures and validation (bundled in the exe) |
| `pyhanko-certvalidator` | MIT | certificate path validation for pyHanko |
| `asn1crypto` | MIT | ASN.1 structures used by pyHanko |
| `mss` | MIT | fast cross-platform screenshots |
| `aiohttp` | Apache-2.0 | HTTP client pulled in by pyHanko |
| `pywin32` | PSF-2.0 (Windows only) | COM automation (Office → PDF) |

### Bundled binaries

* **ffmpeg** — via `imageio-ffmpeg`. ffmpeg is LGPL/GPL depending on the build
  options; do not assume every wheel is LGPL-only. Record the exact bundled binary
  configuration (`ffmpeg -version` and `ffmpeg -L`) for each platform/release.
  A GPL-enabled build adds GPL distribution obligations for that component;
  inventory its corresponding source and notices before distributing it.
* **Tesseract** (optional, installed by the user) — Apache-2.0.
* **Qt 6** — LGPL-3.0; PyInstaller builds must therefore stay dynamically linked
  and must keep the LGPL notice reachable. `packaging/WinGenius.spec` does not
  strip Qt plugins for this reason.

### Optional system tools

`7-Zip`, LibreOffice, `winget`, `tesseract` and `ffmpeg` are *called*, never
bundled, when they happen to be installed. Their licences apply to those programs
only.

## How to move to a permissive licence

The only hard blocker is `PyMuPDF`. Two routes, both already compatible with the
code structure:

1. **Swap the renderer.** Replace the `fitz` (PyMuPDF) calls in
   `app/tools/pdf_tools.py` with `pypdfium2` (Apache-2.0 / BSD-3-Clause, also
   pip-installable, same “render a page to a bitmap” API). Text extraction and
   page surgery can move to `pypdf`. Audit every `fitz`/`pymupdf` call across the PDF modules; this is not a guaranteed small swap.
2. **Buy a PyMuPDF commercial licence** from Artifex and keep everything else as
   it is.

Until one of those happens, treat the AGPL terms as binding: publish the source
(including modifications) when you distribute the app, and keep this notice and
`LICENSE` next to the binary.

### Vazirmatn font
The bundled Vazirmatn font by Rastikerdar is licensed under the SIL Open Font
License 1.1. The full license is included in `app/assets/fonts/OFL.txt`.
Source: https://github.com/rastikerdar/vazirmatn
