"""Image privacy and perception: strip metadata, audit it, straighten scans, blur faces."""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_DIR, K_FILE, K_FILES, K_INT, K_SAVE, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import files_or_raise, first_file, fmt_out_message, out_dir, save_target

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"

# EXIF tags that leak something about the person who took the photo.
PRIVATE_TAGS = {
    0x010F: ("سازنده‌ی دوربین", "Camera make"),
    0x0110: ("مدل دوربین", "Camera model"),
    0x0131: ("نرم‌افزار", "Software"),
    0x0132: ("تاریخ تغییر", "Modify date"),
    0x829A: ("زمان نوردهی", "Exposure time"),
    0x8827: ("ISO", "ISO"),
    0x9003: ("تاریخ عکس", "Date taken"),
    0x9291: ("زیرثانیه", "Sub-second"),
    0xA430: ("صاحب دوربین", "Camera owner"),
    0xA431: ("شماره‌ی بدنه", "Body serial"),
    0xA433: ("لنز", "Lens"),
    0xA434: ("مدل لنز", "Lens model"),
    0xA435: ("شماره‌ی لنز", "Lens serial"),
}

GPS_TAG = 0x8825


def _pillow(ctx: RunContext):
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageOps

        return Image, ImageDraw, ImageFilter, ImageOps
    except Exception:
        raise ToolError(ctx.M(
            "کتابخانه‌ی Pillow نصب نیست. با «pip install Pillow» نصبش کنید.",
            "Pillow is not installed — run “pip install Pillow”."))


def _target_dir(ctx: RunContext, values: Dict[str, Any], fallback: str) -> Path:
    """Output folder chosen by the user, or a named sub-folder of the output dir."""
    requested = str(values.get("out_dir") or "").strip()
    folder = Path(requested) if requested else out_dir(ctx, fallback)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cv2(ctx: RunContext):
    try:
        import cv2

        return cv2
    except Exception:
        raise ToolError(ctx.M(
            "برای این ابزار OpenCV لازم است: pip install opencv-python-headless",
            "This tool needs OpenCV: pip install opencv-python-headless"))


# ------------------------------------------------------------ metadata strip
@tool(
    id="image_metadata_strip", category="image", name="حذف فراداده و GPS عکس",
    name_en="Strip image metadata & GPS",
    desc="EXIF، GPS، XMP و پروفایل رنگی را از عکس حذف می‌کند و عکس تمیز برای انتشار می‌سازد.",
    desc_en="Removes EXIF, GPS, XMP and the ICC profile so the image is safe to publish.",
    icon="eye", order=60,
    keywords=("exif", "gps", "metadata", "privacy", "strip", "فراداده", "جی‌پی‌اس",
              "حریم خصوصی", "حذف"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
        Field("quality", K_INT, "کیفیت JPEG", "JPEG quality", default=95, minimum=50, maximum=100),
    ],
)
def image_metadata_strip(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    quality = int(v.get("quality") or 95)
    folder = _target_dir(ctx, v, "clean")
    made: List[str] = []
    total_removed = 0
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        try:
            image = Image.open(path)
            removed = len(getattr(image, "_getexif", lambda: {})() or {})
        except Exception as exc:                     # noqa: BLE001 - unreadable image
            raise ToolError(ctx.M(f"عکس خوانده نشد: {path.name} ({exc})",
                                  f"Could not read {path.name} ({exc})"))
        mode = "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
        clean = Image.new(mode, image.size)
        clean.paste(image.convert(mode))
        suffix = path.suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
            suffix = ".png"
        target = unique_path(folder / f"{path.stem}_clean{suffix}")
        save_kwargs: Dict[str, Any] = {}
        if suffix in (".jpg", ".jpeg"):
            save_kwargs = {"quality": quality, "optimize": True}
        clean.save(target, **save_kwargs)
        made.append(str(target))
        total_removed += removed
    return RunResult(
        message=ctx.M(
            f"{len(made)} عکس پاک شد ({total_removed} تگ EXIF حذف شد).",
            f"{len(made)} image(s) cleaned ({total_removed} EXIF tag(s) removed)."),
        details=fmt_out_message(ctx, len(made), str(folder)), outputs=made)


# ------------------------------------------------------------- privacy scan
@tool(
    id="image_privacy_scan", category="image", name="بازرسی حریم خصوصی عکس",
    name_en="Scan photos for private data",
    desc="می‌گوید هر عکس چه چیزی لو می‌دهد: مکان GPS، مدل دوربین، نام صاحب، نرم‌افزار و تاریخ.",
    desc_en="Reports what each photo leaks: GPS position, camera model, owner name, software and dates.",
    icon="scan", order=61,
    keywords=("exif", "gps", "privacy", "scan", "risk", "فراداده", "مکان", "لو رفتن"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
    ],
)
def image_privacy_scan(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    lines: List[str] = []
    risky_files = 0
    gps_files = 0
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        try:
            image = Image.open(path)
            exif = image.getexif() or {}
        except Exception as exc:                     # noqa: BLE001 - unreadable image
            lines.append(f"✖ {path.name}: {exc}")
            continue
        findings: List[str] = []
        if GPS_TAG in exif:
            gps_files += 1
            findings.append(("مکان GPS ثبت شده", "GPS location embedded"))
        for tag, (fa, en) in PRIVATE_TAGS.items():
            if tag in exif:
                value = exif.get(tag)
                findings.append((f"{fa}: {value}", f"{en}: {value}"))
        if findings:
            risky_files += 1
            lines.append(f"⚠ {path.name} — {len(findings)} مورد")
            lines.extend("    · " + (item[0] if ctx.lang != "en" else item[1]) for item in findings)
        else:
            lines.append(f"✓ {path.name} — {'تمیز است' if ctx.lang != 'en' else 'clean'}")
    return RunResult(
        message=ctx.M(
            f"{risky_files} از {len(files)} عکس داده‌ی خصوصی دارد"
            + (f" · {gps_files} عکس با مکان GPS" if gps_files else ""),
            f"{risky_files} of {len(files)} image(s) leak data"
            + (f" · {gps_files} with GPS" if gps_files else "")),
        details="\n".join(lines))


# ---------------------------------------------------------------- deskew scan
@tool(
    id="image_deskew", category="image", name="صاف کردن عکس اسکن‌شده",
    name_en="Straighten a scanned photo",
    desc="کجی جزئی اسکن یا عکسِ برگه را تشخیص می‌دهد و تصویر را دقیقاً صاف می‌کند.",
    desc_en="Detects the tilt of a scanned page and rotates it back to perfectly straight.",
    icon="ruler", order=62,
    keywords=("deskew", "straighten", "rotate", "scan", "صاف", "کج", "اسکن", "چرخش", "راست"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
        Field("max_angle", K_INT, "حداکثر زاویه‌ی اصلاح (درجه)", "Max correction angle (degrees)",
              default=15, minimum=1, maximum=45),
    ],
)
def image_deskew(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    cv2 = _cv2(ctx)
    Image, *_ = _pillow(ctx)
    try:
        import numpy as np
    except Exception:                                 # pragma: no cover - numpy ships with cv2
        raise ToolError(ctx.M("numpy لازم است: pip install numpy", "numpy is required: pip install numpy"))
    files = files_or_raise(v, "src", ctx)
    limit = float(v.get("max_angle") or 15)
    folder = _target_dir(ctx, v, "deskew")
    made: List[str] = []
    angles: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
        gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise ToolError(ctx.M(f"تصویر خوانده نشد: {path.name}", f"Cannot read {path.name}"))
        # Blur + Canny + Hough: the dominant straight edge wins, which is exactly
        # what a tilted scanned page has. minAreaRect alone reported 0° for a
        # rotated rectangle because of how OpenCV encodes the angle.
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        minimum = max(40, min(gray.shape) // 5)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=minimum,
                                minLineLength=minimum, maxLineGap=12)
        candidates: List[float] = []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if x2 == x1:
                    continue
                angle = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
                while angle > 45:
                    angle -= 90
                while angle < -45:
                    angle += 90
                if abs(angle) <= limit and abs(angle) > 0.05:
                    candidates.append(angle)
        angle = float(np.median(candidates)) if candidates else 0.0
        image = Image.open(path)
        if abs(angle) > 0.05:
            upright = image.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC,
                                   fillcolor="white" if image.mode == "RGB" else None)
        else:
            upright = image.copy()
        target = unique_path(folder / f"{path.stem}_straight{path.suffix or '.png'}")
        upright.save(target)
        made.append(str(target))
        angles.append(f"{path.name}: {angle:+.2f}°")
    return RunResult(
        message=ctx.M(f"{len(made)} تصویر صاف شد.", f"{len(made)} image(s) straightened."),
        details="\n".join(angles), outputs=made)


# ------------------------------------------------------------------ face blur
@tool(
    id="image_face_blur", category="image", name="مخفی کردن چهره‌ها",
    name_en="Blur faces in photos",
    desc="چهره‌ها را در عکس پیدا و تار یا پیکسلی می‌کند — پیش از انتشار عکس در اینترنت.",
    desc_en="Finds faces and blurs or pixelates them before a photo goes public.",
    icon="eye", order=63,
    keywords=("face", "blur", "privacy", "pixelate", "چهره", "صورت", "تار", "پیکسل", "حریم خصوصی"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("mode", K_CHOICE, "نوع پوشش", "Cover style", default="blur",
              choices=[("blur", "تار", "Blur"), ("pixelate", "پیکسلی", "Pixelate"),
                       ("box", "کادر توپر", "Solid box")]),
        Field("strength", K_INT, "شدت", "Strength", default=25, minimum=4, maximum=100),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_face_blur(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    cv2 = _cv2(ctx)
    Image, ImageDraw, ImageFilter, _ = _pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    mode = str(v.get("mode") or "blur")
    strength = max(4, int(v.get("strength") or 25))
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if not cascade_path.exists():                     # pragma: no cover - broken cv2 install
        raise ToolError(ctx.M("مدل تشخیص چهره در OpenCV پیدا نشد؛ OpenCV را دوباره نصب کنید.",
                              "OpenCV's face model is missing; reinstall OpenCV."))
    cascade = cv2.CascadeClassifier(str(cascade_path))
    folder = _target_dir(ctx, v, "faces")
    made: List[str] = []
    report: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        try:
            import numpy as np
        except Exception:                             # pragma: no cover
            raise ToolError(ctx.M("numpy لازم است.", "numpy is required."))
        image = Image.open(path).convert("RGB")
        array = np.array(image)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        for (x, y, w, h) in faces:
            pad = int(max(w, h) * 0.18)
            box = (max(0, x - pad), max(0, y - pad),
                   min(image.width, x + w + pad), min(image.height, y + h + pad))
            if mode == "box":
                ImageDraw.Draw(image).rectangle(box, fill=(20, 20, 20))
                continue
            region = image.crop(box)
            if mode == "pixelate":
                small = region.resize((max(2, region.width // strength),
                                       max(2, region.height // strength)),
                                      Image.Resampling.BILINEAR)
                region = small.resize(region.size, Image.Resampling.NEAREST)
            else:
                region = region.filter(ImageFilter.GaussianBlur(radius=strength / 2))
            image.paste(region, box)
        target = unique_path(folder / f"{path.stem}_faces{path.suffix or '.png'}")
        image.save(target)
        made.append(str(target))
        report.append(f"{path.name}: {len(faces)} " + ("چهره" if ctx.lang != "en" else "face(s)"))
    covered = sum(int(line.rsplit(":", 1)[1].split()[0]) for line in report) if report else 0
    return RunResult(
        message=ctx.M(f"{covered} چهره در {len(made)} عکس پوشانده شد.",
                      f"{covered} face(s) covered across {len(made)} image(s)."),
        details="\n".join(report), outputs=made)
