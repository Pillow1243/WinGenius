"""Image toolkit: convert, resize, compress, watermark, rotate, icons."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_COLOR, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import ensure_dir, files_or_raise, first_file, out_dir, require, save_target

IMG_FILTER = "تصاویر (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.gif *.ico);;همه فایل‌ها (*)"
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".ico"}


def _image(ctx: RunContext):
    return require("PIL.Image", "Pillow", ctx)


def _open_images(ctx: RunContext, files: List[Path]):
    Image = _image(ctx)
    opened = []
    for path in files:
        try:
            im = Image.open(path)
            im.load()
        except Exception as exc:
            raise ToolError(ctx.M(f"این تصویر باز نشد: {path.name}\n{exc}", f"Cannot open image: {path.name}\n{exc}"))
        opened.append((path, im))
    return opened


def _prepare_output(
    ctx: RunContext, v: Dict[str, Any], path: Path, suffix: str, ext: str
) -> Path:
    folder = str(v.get("out_dir") or "").strip()
    target_dir = ensure_dir(folder) if folder else out_dir(ctx)
    ext = ext if ext.startswith(".") else f".{ext}"
    return unique_path(target_dir / f"{path.stem}{suffix}{ext}")


def _flatten(im, background=(255, 255, 255)):
    """Convert to RGB, filling transparency with white for JPEG output."""
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = _new_rgb(im.size, background)
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _new_rgb(size, background):
    from PIL import Image as PILImage

    return PILImage.new("RGB", size, background)


# ------------------------------------------------------------------- convert
@tool(
    id="image_convert", category="image", name="تبدیل فرمت تصویر", name_en="Convert images",
    desc="فرمت تصاویر را به PNG، JPG، WEBP، BMP، TIFF یا ICO تغییر می‌دهد.",
    desc_en="Convert images between PNG, JPG, WEBP, BMP, TIFF and ICO.",
    icon="image", order=10, keywords=("convert image", "png to jpg", "webp", "فرمت عکس"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("fmt", K_CHOICE, "فرمت خروجی", "Target format", default="png",
              choices=[("png", "PNG", "PNG"), ("jpg", "JPG", "JPG"), ("webp", "WEBP", "WEBP"),
                       ("bmp", "BMP", "BMP"), ("tiff", "TIFF", "TIFF"), ("ico", "ICO (آیکون)", "ICO")]),
        Field("quality", K_INT, "کیفیت (JPG/WEBP)", "Quality (JPG/WEBP)", default=92,
              minimum=30, maximum=100),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    fmt = str(v.get("fmt") or "png").lower()
    ext = ".jpg" if fmt == "jpg" else f".{fmt}"
    outputs: List[str] = []
    total = len(opened)
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, ctx.M(f"{path.name}", path.name))
        dest = _prepare_output(ctx, v, path, "", ext)
        save_kwargs: Dict[str, Any] = {}
        if fmt == "jpg":
            im = _flatten(im)
            save_kwargs = {"quality": int(v.get("quality") or 92), "optimize": True, "progressive": True}
        elif fmt == "webp":
            save_kwargs = {"quality": int(v.get("quality") or 92), "method": 4}
        elif fmt == "ico":
            im = im.convert("RGBA")
            save_kwargs = {"sizes": [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]}
        elif fmt == "png":
            save_kwargs = {"optimize": True}
        try:
            im.save(dest, **save_kwargs)
        except Exception as exc:
            raise ToolError(ctx.M(f"ذخیره‌ی {dest.name} ناموفق بود: {exc}", f"Saving {dest.name} failed: {exc}"))
        outputs.append(str(dest))
        im.close()
    return RunResult(
        message=ctx.M(f"{len(outputs)} تصویر تبدیل شد.", f"{len(outputs)} image(s) converted."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# -------------------------------------------------------------------- resize
@tool(
    id="image_resize", category="image", name="تغییر اندازه تصویر", name_en="Resize images",
    desc="ابعاد تصاویر را با درصد یا اندازه‌ی دقیق تغییر می‌دهد و نسبت تصویر را حفظ می‌کند.",
    desc_en="Resize images by percentage or exact pixels, keeping the aspect ratio.",
    icon="crop", order=20, keywords=("resize image", "تغییر اندازه", "کوچک کردن عکس", "ابعاد"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("mode", K_CHOICE, "روش", "Mode", default="percent",
              choices=[("percent", "درصدی", "Percentage"), ("max", "بیشترین ضلع", "Max side"),
                       ("exact", "اندازه‌ی دقیق", "Exact size")]),
        Field("percent", K_INT, "درصد", "Percent", default=50, minimum=1, maximum=400, suffix="%",
              visible_when=("mode", "percent")),
        Field("max_side", K_INT, "بیشترین ضلع (px)", "Max side (px)", default=1920, minimum=16,
              maximum=20000, visible_when=("mode", "max")),
        Field("width", K_INT, "عرض", "Width", default=800, minimum=1, maximum=20000,
              visible_when=("mode", "exact")),
        Field("height", K_INT, "ارتفاع", "Height", default=600, minimum=1, maximum=20000,
              visible_when=("mode", "exact")),
        Field("keep_ratio", K_CHOICE, "نسبت تصویر", "Aspect ratio", default="keep",
              choices=[("keep", "حفظ نسبت", "Keep"), ("stretch", "کشیدن به اندازه‌ی دقیق", "Stretch")],
              visible_when=("mode", "exact")),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_resize(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    total = len(opened)
    mode = v.get("mode") or "percent"
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        w, h = im.size
        if mode == "percent":
            factor = max(1, int(v.get("percent") or 50)) / 100.0
            new_size = (max(1, int(w * factor)), max(1, int(h * factor)))
        elif mode == "max":
            limit = max(16, int(v.get("max_side") or 1920))
            scale = min(1.0, limit / max(w, h))
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        else:
            tw, th = max(1, int(v.get("width") or w)), max(1, int(v.get("height") or h))
            if v.get("keep_ratio", "keep") == "keep":
                scale = min(tw / w, th / h)
                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            else:
                new_size = (tw, th)
        resized = im.resize(new_size, Image.LANCZOS)
        dest = _prepare_output(ctx, v, path, f"_{new_size[0]}x{new_size[1]}", path.suffix or ".png")
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            resized = _flatten(resized)
            resized.save(dest, quality=92, optimize=True)
        else:
            resized.save(dest)
        outputs.append(str(dest))
        im.close()
        if resized is not im:
            try:
                resized.close()
            except Exception:
                pass
    return RunResult(
        message=ctx.M(f"{len(outputs)} تصویر تغییر اندازه داد.", f"{len(outputs)} image(s) resized."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# ----------------------------------------------------------------- compress
@tool(
    id="image_compress", category="image", name="فشرده‌سازی تصویر", name_en="Compress images",
    desc="حجم تصاویر را با کاهش کیفیت و/یا ابعاد کم می‌کند.",
    desc_en="Shrink images by lowering quality and/or dimensions.",
    icon="zip", order=30, keywords=("compress image", "کم کردن حجم عکس", "حجم تصویر"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("quality", K_INT, "کیفیت", "Quality", default=75, minimum=20, maximum=100),
        Field("max_side", K_INT, "بیشترین ضلع (۰ = بدون تغییر)", "Max side (0 = keep)", default=0,
              minimum=0, maximum=20000),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_compress(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    before_total = sum(p.stat().st_size for p, _ in opened)
    total = len(opened)
    limit = int(v.get("max_side") or 0)
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        if limit > 0 and max(im.size) > limit:
            scale = limit / max(im.size)
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        dest = _prepare_output(ctx, v, path, "_small", ".jpg")
        image = _flatten(im)
        image.save(dest, quality=int(v.get("quality") or 75), optimize=True, progressive=True)
        outputs.append(str(dest))
        try:
            im.close()
        except Exception:
            pass
    after_total = sum(Path(p).stat().st_size for p in outputs if Path(p).exists())
    saved = 100.0 * (1 - after_total / before_total) if before_total else 0
    return RunResult(
        message=ctx.M(
            f"{len(outputs)} تصویر فشرده شد: {human_size(before_total)} → {human_size(after_total)} ({saved:.0f}٪ کاهش)",
            f"{len(outputs)} image(s) compressed: {human_size(before_total)} → {human_size(after_total)} ({saved:.0f}% smaller)",
        ),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# ---------------------------------------------------------------- watermark
@tool(
    id="image_watermark", category="image", name="واترمارک تصویر", name_en="Watermark image",
    desc="متن یا لوگو را با شفافیت دلخواه روی تصاویر می‌گذارد.",
    desc_en="Overlay text (or a logo) with custom opacity and position.",
    icon="note", order=40, keywords=("watermark", "واترمارک عکس", "لوگو"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("text", K_TEXT, "متن", "Text", default="@MyBrand"),
        Field("logo", K_FILE, "یا فایل لوگو (اختیاری)", "Or logo file (optional)",
              filt="PNG (*.png);;همه فایل‌ها (*)"),
        Field("position", K_CHOICE, "موقعیت", "Position", default="br",
              choices=[("br", "پایین راست", "Bottom right"), ("bl", "پایین چپ", "Bottom left"),
                       ("c", "وسط", "Center"), ("tl", "بالا چپ", "Top left"), ("tr", "بالا راست", "Top right")]),
        Field("size", K_INT, "اندازه‌ی متن", "Text size", default=40, minimum=8, maximum=400),
        Field("opacity", K_FLOAT, "شفافیت", "Opacity", default=0.55, minimum=0.05, maximum=1.0,
              step=0.05, decimals=2),
        Field("color", K_COLOR, "رنگ", "Color", default="#FFFFFF"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_watermark(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    ImageDraw = require("PIL.ImageDraw", "Pillow", ctx)
    ImageFont = require("PIL.ImageFont", "Pillow", ctx)
    ImageColor = require("PIL.ImageColor", "Pillow", ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    raw_color = str(v.get("color") or "#FFFFFF").strip() or "#FFFFFF"
    try:
        rgb = ImageColor.getrgb(raw_color)
        color_tuple = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        color_tuple = (255, 255, 255)
    logo_path = str(v.get("logo") or "").strip()
    logo = None
    if logo_path and Path(logo_path).exists():
        logo = Image.open(logo_path).convert("RGBA")
    outputs: List[str] = []
    total = len(opened)
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        base = im.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        alpha = int(255 * float(v.get("opacity") or 0.55))
        position = str(v.get("position") or "br")
        margin = max(12, int(min(base.size) * 0.03))
        if logo is not None:
            target_w = int(base.width * 0.18)
            ratio = target_w / max(1, logo.width)
            mark = logo.resize((target_w, max(1, int(logo.height * ratio))), Image.LANCZOS)
            if alpha < 255:
                mark_alpha = mark.split()[-1].point(lambda px: int(px * alpha / 255))
                mark.putalpha(mark_alpha)
            box = mark.size
        else:
            text = str(v.get("text") or "")
            if not text:
                raise ToolError(ctx.M("متن واترمارک خالی است.", "Watermark text is empty."))
            try:
                font = ImageFont.truetype("arial.ttf", int(v.get("size") or 40))
            except Exception:
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", int(v.get("size") or 40))
                except Exception:
                    font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            box = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        if position == "br":
            xy = (base.width - box[0] - margin, base.height - box[1] - margin)
        elif position == "bl":
            xy = (margin, base.height - box[1] - margin)
        elif position == "tl":
            xy = (margin, margin)
        elif position == "tr":
            xy = (base.width - box[0] - margin, margin)
        else:
            xy = ((base.width - box[0]) // 2, (base.height - box[1]) // 2)
        if logo is not None:
            overlay.alpha_composite(mark, (max(0, int(xy[0])), max(0, int(xy[1]))))
        else:
            draw.text((max(0, xy[0]), max(0, xy[1])), str(v.get("text") or ""), font=font,
                      fill=color_tuple + (alpha,))
        combined = Image.alpha_composite(base, overlay)
        dest = _prepare_output(ctx, v, path, "_wm", path.suffix or ".png")
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            _flatten(combined).save(dest, quality=92, optimize=True)
        else:
            combined.save(dest)
        outputs.append(str(dest))
        im.close()
    if logo is not None:
        logo.close()
    return RunResult(
        message=ctx.M(f"واترمارک روی {len(outputs)} تصویر اعمال شد.", f"Watermark applied to {len(outputs)} image(s)."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# ------------------------------------------------------------------- rotate
@tool(
    id="image_rotate", category="image", name="چرخش و آینه تصویر", name_en="Rotate / flip",
    desc="تصاویر را می‌چرخاند یا به‌صورت افقی/عمودی آینه می‌کند.",
    desc_en="Rotate or mirror images.",
    icon="convert", order=50, keywords=("rotate image", "چرخش عکس", "آینه", "flip"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("angle", K_CHOICE, "زاویه", "Angle", default=90,
              choices=[(90, "۹۰ درجه", "90°"), (180, "۱۸۰ درجه", "180°"), (270, "۲۷۰ درجه", "270°")]),
        Field("flip", K_CHOICE, "آینه", "Mirror", default="none",
              choices=[("none", "بدون آینه", "None"), ("h", "افقی", "Horizontal"), ("v", "عمودی", "Vertical")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_rotate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    total = len(opened)
    angle = int(v.get("angle") or 90) % 360
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        result = im
        if angle in (90, 180, 270):
            result = result.rotate(-angle, expand=True)
        if v.get("flip") == "h":
            result = result.transpose(Image.FLIP_LEFT_RIGHT)
        elif v.get("flip") == "v":
            result = result.transpose(Image.FLIP_TOP_BOTTOM)
        dest = _prepare_output(ctx, v, path, "_rot", path.suffix or ".png")
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            _flatten(result).save(dest, quality=92, optimize=True)
        else:
            result.save(dest)
        outputs.append(str(dest))
        im.close()
    return RunResult(message=ctx.M(f"{len(outputs)} تصویر پردازش شد.", f"{len(outputs)} image(s) processed."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


# ----------------------------------------------------------------- grayscale
@tool(
    id="image_grayscale", category="image", name="سیاه‌وسفید کردن", name_en="Grayscale",
    desc="تصاویر را سیاه‌وسفید یا با کنتراست بالا ذخیره می‌کند.",
    desc_en="Convert images to grayscale (optionally high contrast).",
    icon="color", order=60, keywords=("grayscale", "سیاه سفید", "تک رنگ"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("contrast", K_FLOAT, "کنتراست", "Contrast", default=1.0, minimum=0.5, maximum=3.0,
              step=0.1, decimals=1),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_grayscale(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _image(ctx)
    ImageEnhance = require("PIL.ImageEnhance", "Pillow", ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    total = len(opened)
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        gray = im.convert("L")
        factor = float(v.get("contrast") or 1.0)
        if abs(factor - 1.0) > 0.01:
            gray = ImageEnhance.Contrast(gray).enhance(factor)
        dest = _prepare_output(ctx, v, path, "_gray", path.suffix or ".png")
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            gray.save(dest, quality=92, optimize=True)
        else:
            gray.save(dest)
        outputs.append(str(dest))
        im.close()
    return RunResult(message=ctx.M(f"{len(outputs)} تصویر سیاه‌وسفید شد.", f"{len(outputs)} image(s) converted."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


# ------------------------------------------------------------------ image → ico
@tool(
    id="image_to_ico", category="image", name="ساخت آیکون ICO", name_en="Image → ICO icon",
    desc="از یک تصویر، فایل آیکون چندسایز ویندوز (.ico) می‌سازد.",
    desc_en="Create a multi-size Windows .ico file from an image.",
    icon="window", order=70, keywords=("ico", "آیکون", "icon maker"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_to_ico(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    total = len(opened)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        dest = _prepare_output(ctx, v, path, "", ".ico")
        square = _square(im)
        square.save(dest, sizes=sizes)
        outputs.append(str(dest))
        im.close()
    return RunResult(message=ctx.M(f"{len(outputs)} آیکون ساخته شد.", f"{len(outputs)} icon(s) created."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


def _square(im):
    from PIL import Image as PILImage

    im = im.convert("RGBA")
    side = max(im.size)
    canvas = PILImage.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    return canvas


# ------------------------------------------------------------------- img info
@tool(
    id="image_info", category="image", name="اطلاعات تصویر", name_en="Image info",
    desc="ابعاد، حالت رنگ، حجم و متادیتای تصویر را نشان می‌دهد.",
    desc_en="Show dimensions, color mode, size and metadata.",
    icon="list", order=80, keywords=("image info", "ابعاد عکس", "exif"),
    fields=[Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True)],
)
def image_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _image(ctx)
    files = files_or_raise(v, "src", ctx)
    rows: List[str] = []
    for index, path in enumerate(files):
        ctx.progress(index, len(files), path.name)
        try:
            from PIL import Image

            with Image.open(path) as im:
                rows.append(f"{path.name}: {im.width}×{im.height} px | {im.mode} | {im.format} | {human_size(path.stat().st_size)}")
        except Exception as exc:
            rows.append(f"{path.name}: {ctx.M('خطا', 'error')} — {exc}")
    return RunResult(message=ctx.M(f"اطلاعات {len(files)} تصویر", f"Info for {len(files)} image(s)"),
                     details="\n".join(rows), outputs=[str(p) for p in files])


# ------------------------------------------------------------------ upscale
@tool(
    id="image_thumbnail", category="image", name="ساخت تامبنیل", name_en="Make thumbnails",
    desc="از تصاویر، نسخه‌های کوچک مربعی برای گالری یا شبکه‌های اجتماعی می‌سازد.",
    desc_en="Create square thumbnails for galleries or social media.",
    icon="scan", order=90, keywords=("thumbnail", "تامبنیل", "پیش نمایش"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("size", K_INT, "اندازه (px)", "Size (px)", default=400, minimum=32, maximum=4000),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_thumbnail(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    ImageOps = require("PIL.ImageOps", "Pillow", ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    outputs: List[str] = []
    side = max(16, int(v.get("size") or 400))
    total = len(opened)
    for index, (path, im) in enumerate(opened):
        ctx.progress(index, total, path.name)
        thumb = ImageOps.fit(im.convert("RGB"), (side, side), method=Image.LANCZOS)
        dest = _prepare_output(ctx, v, path, f"_thumb{side}", ".jpg")
        thumb.save(dest, quality=90, optimize=True)
        outputs.append(str(dest))
        im.close()
    return RunResult(message=ctx.M(f"{len(outputs)} تامبنیل ساخته شد.", f"{len(outputs)} thumbnail(s) created."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))
