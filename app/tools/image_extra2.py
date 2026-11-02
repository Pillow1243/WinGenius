"""Advanced image work: grids, sheets, ASCII, adjustments, duotone, stacking, GIFs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_COLOR, K_DIR, K_FILES, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ._helpers import files_or_raise, fmt_out_message, out_dir, save_target

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"


def _require_pillow(ctx: RunContext):
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

        return Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
    except Exception:
        raise ToolError(ctx.M(
            "کتابخانه‌ی Pillow نصب نیست. با «pip install Pillow» نصبش کنید.",
            "Pillow is not installed — run “pip install Pillow”."))


def _open_rgb(path: Path):
    from PIL import Image, ImageOps

    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


# ------------------------------------------------------------------- split grid
@tool(
    id="image_split_grid", category="image", name="برش شبکه‌ای تصویر", name_en="Split image into a grid",
    desc="یک عکس را به ردیف‌ستون مساوی می‌شکند (مناسب پست اینستاگرام، اسپرایت و پازل).",
    desc_en="Splits an image into equal rows × columns (Instagram grids, sprites, puzzles).",
    icon="grid", order=40, keywords=("split", "grid", "tiles", "برش", "شبکه", "پازل", "اسپرایت"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("cols", K_INT, "ستون‌ها", "Columns", default=3, minimum=1, maximum=20),
        Field("rows", K_INT, "ردیف‌ها", "Rows", default=3, minimum=1, maximum=20),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_split_grid(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    cols = max(1, int(v.get("cols") or 3))
    rows = max(1, int(v.get("rows") or 3))
    target = out_dir(ctx, "split")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        width, height = image.size
        tile_w = width // cols
        tile_h = height // rows
        if tile_w < 8 or tile_h < 8:
            continue
        for row in range(rows):
            for col in range(cols):
                box = (col * tile_w, row * tile_h, (col + 1) * tile_w, (row + 1) * tile_h)
                piece = image.crop(box)
                dest = target / f"{path.stem}_r{row + 1}c{col + 1}.png"
                piece.save(dest)
                made.append(str(dest))
    if not made:
        raise ToolError(ctx.M("تصویری برای برش مناسب نبود.", "No image was big enough to split."))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# --------------------------------------------------------------- contact sheet
@tool(
    id="image_contact_sheet", category="image", name="کانتکت‌شیت تصاویر", name_en="Contact sheet",
    desc="همه‌ی عکس‌های یک پوشه را در یک برگه‌ی شبکه‌ای با نام فایل کنار هم می‌چیند.",
    desc_en="Lays every image of a folder onto one grid sheet with its file name.",
    icon="grid", order=41, keywords=("contact sheet", "sheet", "grid", "برگه", "کانتکت", "مرور"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("cols", K_INT, "ستون‌ها", "Columns", default=5, minimum=1, maximum=12),
        Field("cell", K_INT, "اندازه‌ی هر خانه", "Cell size", default=260, minimum=80, maximum=800),
        Field("gap", K_INT, "فاصله", "Gap", default=10, minimum=0, maximum=60),
        Field("background", K_COLOR, "پس‌زمینه", "Background", default="#101828"),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="PNG (*.png);;JPEG (*.jpg)"),
    ],
)
def image_contact_sheet(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, ImageDraw, _enh, _filt, ImageFont, _ops = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    cols = max(1, int(v.get("cols") or 5))
    cell = max(80, int(v.get("cell") or 260))
    gap = max(0, int(v.get("gap") or 10))
    caption_height = 22
    rows = (len(files) + cols - 1) // cols
    width = cols * cell + (cols + 1) * gap
    height = rows * (cell + caption_height) + (rows + 1) * gap
    sheet = Image.new("RGB", (width, height), str(v.get("background") or "#101828"))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        row = (index - 1) // cols
        col = (index - 1) % cols
        x = gap + col * (cell + gap)
        y = gap + row * (cell + caption_height + gap)
        try:
            thumb = _open_rgb(path)
        except Exception:
            continue
        thumb.thumbnail((cell, cell), Image.LANCZOS)
        sheet.paste(thumb, (x + (cell - thumb.width) // 2, y + (cell - thumb.height) // 2))
        name = path.name if len(path.name) < 34 else path.name[:31] + "…"
        draw.text((x + 4, y + cell + 4), name, fill="#C7D2E0", font=font)
    dest = save_target(ctx, str(v.get("out") or ""), "contact_sheet.png")
    sheet.save(dest)
    return RunResult(message=ctx.M(f"برگه با {len(files)} تصویر ساخته شد.",
                                   f"Sheet with {len(files)} images created."),
                     outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------------------------- ascii
@tool(
    id="image_ascii", category="image", name="تبدیل تصویر به ASCII", name_en="Image → ASCII art",
    desc="عکس را به هنر متنی ASCII یا نویسه‌های فارسی تبدیل می‌کند و ذخیره می‌کند.",
    desc_en="Converts an image to ASCII art and saves it as a text file.",
    icon="code", order=42, keywords=("ascii", "art", "text art", "متنی", "هنر", "اسکی"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("width", K_INT, "عرض (کاراکتر)", "Width (chars)", default=100, minimum=20, maximum=300),
        Field("charset", K_CHOICE, "نویسه‌ها", "Characters", default="classic",
              choices=[("classic", "کلاسیک  @%#*+=-:. ", "Classic"), ("blocks", "بلوک █▓▒░ ", "Blocks"),
                       ("persian", "فارسی  آبپتثجچ", "Persian")]),
    ],
)
def image_ascii(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    width = max(20, int(v.get("width") or 100))
    sets = {"classic": "@%#*+=-:. ", "blocks": "█▓▒░ ", "persian": "آبپتثجچ"}
    charset = sets.get(str(v.get("charset") or "classic"), sets["classic"])
    target = out_dir(ctx, "ascii")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        ratio = image.height / max(1, image.width)
        height = max(1, int(width * ratio * 0.5))
        small = image.resize((width, height), Image.LANCZOS).convert("L")
        pixels = list(small.getdata())
        lines = []
        for row in range(height):
            chunk = pixels[row * width:(row + 1) * width]
            lines.append("".join(charset[min(len(charset) - 1, value * len(charset) // 256)]
                                 for value in chunk))
        dest = target / f"{path.stem}.txt"
        dest.write_text("\n".join(lines), encoding="utf-8")
        made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ---------------------------------------------------------------- adjustments
@tool(
    id="image_adjust", category="image", name="تنظیم نور و رنگ", name_en="Adjust brightness & colour",
    desc="روشنایی، کنتراست، رنگ، وضوح و تاری تصویر را با یک عدد تنظیم می‌کند.",
    desc_en="Adjusts brightness, contrast, colour, sharpness and blur in one pass.",
    icon="sun", order=43, keywords=("brightness", "contrast", "sharpen", "blur", "نور", "رنگ", "وضوح"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("brightness", K_INT, "روشنایی ٪", "Brightness %", default=100, minimum=20, maximum=300),
        Field("contrast", K_INT, "کنتراست ٪", "Contrast %", default=100, minimum=20, maximum=300),
        Field("color", K_INT, "رنگ ٪", "Colour %", default=100, minimum=0, maximum=300),
        Field("sharpness", K_INT, "وضوح ٪", "Sharpness %", default=100, minimum=0, maximum=300),
        Field("blur", K_INT, "تاری (۰ تا ۲۰)", "Blur (0-20)", default=0, minimum=0, maximum=20),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_adjust(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _Image, _Draw, ImageEnhance, ImageFilter, _Font, _ops = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    target = out_dir(ctx, "adjusted")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        if int(v.get("blur") or 0) > 0:
            image = image.filter(ImageFilter.GaussianBlur(radius=int(v.get("blur")) / 4))
        image = ImageEnhance.Brightness(image).enhance(int(v.get("brightness") or 100) / 100)
        image = ImageEnhance.Contrast(image).enhance(int(v.get("contrast") or 100) / 100)
        image = ImageEnhance.Color(image).enhance(int(v.get("color") if v.get("color") is not None else 100) / 100)
        image = ImageEnhance.Sharpness(image).enhance(int(v.get("sharpness") or 100) / 100)
        dest = target / f"{path.stem}_adj.png"
        image.save(dest)
        made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ---------------------------------------------------------------------- duotone
def _hex_to_rgb(value: str, fallback=(0, 0, 0)) -> tuple:
    text = str(value or "").lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return fallback


@tool(
    id="image_duotone", category="image", name="دورنگ (Duotone)", name_en="Duotone",
    desc="تصویر را سیاه‌وسفید می‌کند و بین دو رنگ انتخابی، حالت هنری دورنگ می‌سازد.",
    desc_en="Turns an image into a two-colour duotone using the colours you pick.",
    icon="palette", order=44, keywords=("duotone", "gradient", "tint", "دورنگ", "دو رنگ", "هنری"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("dark", K_COLOR, "رنگ تیره", "Dark colour", default="#0B1B3A"),
        Field("light", K_COLOR, "رنگ روشن", "Light colour", default="#F5C15A"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_duotone(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    dark = _hex_to_rgb(v.get("dark"), (11, 27, 58))
    light = _hex_to_rgb(v.get("light"), (245, 193, 90))
    target = out_dir(ctx, "duotone")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        gray = _open_rgb(path).convert("L")
        image = Image.merge("RGB", [gray.point(lambda value: int(dark[0] + (light[0] - dark[0]) * value / 255)),
                                    gray.point(lambda value: int(dark[1] + (light[1] - dark[1]) * value / 255)),
                                    gray.point(lambda value: int(dark[2] + (light[2] - dark[2]) * value / 255))])
        dest = target / f"{path.stem}_duotone.png"
        image.save(dest)
        made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ---------------------------------------------------------------- autocontrast
@tool(
    id="image_autocontrast", category="image", name="اصلاح خودکار نور", name_en="Auto levels & white balance",
    desc="کنتراست و تعادل رنگ تصویر را خودکار اصلاح می‌کند (مناسب عکس‌های کم‌نور و اسکن).",
    desc_en="Automatically fixes contrast and white balance — great for dark photos and scans.",
    icon="sun", order=45, keywords=("autocontrast", "levels", "white balance", "اصلاح", "خودکار", "اسکن"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("cutoff", K_INT, "درصد برش لبه‌ها", "Cutoff %", default=1, minimum=0, maximum=20),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_autocontrast(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, _D, _E, _F, _Font, ImageOps = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    cutoff = float(v.get("cutoff") or 1)
    target = out_dir(ctx, "auto")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = ImageOps.autocontrast(_open_rgb(path), cutoff=cutoff, preserve_tone=True)
        dest = target / f"{path.stem}_auto.png"
        image.save(dest)
        made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ------------------------------------------------------------------- stacking
@tool(
    id="image_stack", category="image", name="ترکیب چند عکس (کاهش نویز)", name_en="Stack images",
    desc="چند عکس هم‌قاب را میانگین/میانه می‌گیرد تا نویز کم شود (عکاسی شب، اسکن تکراری).",
    desc_en="Averages or median-stacks identical frames to cut noise (night shots, repeated scans).",
    icon="layers", order=46, keywords=("stack", "average", "median", "noise", "ترکیب", "نویز"),
    fields=[
        Field("src", K_FILES, "تصاویر هم‌اندازه", "Same-size images", required=True, filt=IMAGE_FILTER),
        Field("mode", K_CHOICE, "روش", "Method", default="median",
              choices=[("median", "میانه (بهترین کاهش نویز)", "Median"), ("mean", "میانگین", "Mean")]),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="PNG (*.png)"),
    ],
)
def image_stack(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    if len(files) < 2:
        raise ToolError(ctx.M("حداقل دو تصویر لازم است.", "At least two images are required."))
    images = []
    size = None
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        if size is None:
            size = image.size
        elif image.size != size:
            image = image.resize(size, Image.LANCZOS)
        images.append(image)
    mode = str(v.get("mode") or "median")
    try:
        import numpy as np

        arrays = np.stack([np.asarray(image, dtype="float32") for image in images])
        stacked = np.median(arrays, axis=0) if mode == "median" else arrays.mean(axis=0)
        result = Image.fromarray(np.clip(stacked, 0, 255).astype("uint8"))
    except ImportError:  # pragma: no cover - numpy ships with Pillow extras usually
        blended = images[0].copy()
        for image in images[1:]:
            blended = Image.blend(blended, image, 0.5)
        result = blended
    dest = save_target(ctx, str(v.get("out") or ""), "stacked.png")
    result.save(dest)
    return RunResult(message=ctx.M(f"{len(files)} تصویر ترکیب شد.", f"{len(files)} images stacked."),
                     outputs=[str(dest)], open_target=str(dest))


# -------------------------------------------------------------------- pixelate
@tool(
    id="image_pixelate", category="image", name="پیکسلی کردن (محرمانگی)", name_en="Pixelate for privacy",
    desc="بخش‌های حساس عکس را با موزاییک/پیکسل محو می‌کند؛ کل تصویر یا یک کادر انتخابی.",
    desc_en="Blurs out sensitive parts with a mosaic — the whole image or a chosen rectangle.",
    icon="eye", order=47, keywords=("pixelate", "mosaic", "blur face", "محرمانگی", "موزاییک", "پیکسل"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("block", K_INT, "اندازه‌ی بلوک", "Block size", default=16, minimum=3, maximum=80),
        Field("region", K_TEXT, "کادر (x,y,w,h) — خالی = کل", "Region (x,y,w,h) — empty = all",
              placeholder="0,0,400,300"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_pixelate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    block = max(3, int(v.get("block") or 16))
    region_text = str(v.get("region") or "").strip()
    target = out_dir(ctx, "pixelated")
    made: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        box = (0, 0, image.width, image.height)
        if region_text:
            try:
                numbers = [int(part.strip()) for part in region_text.split(",")]
                if len(numbers) == 4:
                    box = (numbers[0], numbers[1], numbers[0] + numbers[2], numbers[1] + numbers[3])
            except ValueError:
                raise ToolError(ctx.M("کادر باید به شکل x,y,w,h باشد.", "Region must be x,y,w,h."))
        crop = image.crop(box)
        small = crop.resize((max(1, crop.width // block), max(1, crop.height // block)), Image.BILINEAR)
        image.paste(small.resize(crop.size, Image.NEAREST), (box[0], box[1]))
        dest = target / f"{path.stem}_pixel.png"
        image.save(dest)
        made.append(str(dest))
    return RunResult(message=fmt_out_message(ctx, len(made), str(target)), outputs=made,
                     open_target=str(target))


# ------------------------------------------------------------------------ gif
@tool(
    id="image_gif_make", category="image", name="ساخت GIF از عکس‌ها", name_en="Images → GIF",
    desc="از چند عکس، یک GIF متحرک با سرعت دلخواه می‌سازد.",
    desc_en="Builds an animated GIF from a list of images at the speed you choose.",
    icon="film", order=48, keywords=("gif", "animation", "frames", "انیمیشن", "گیف", "متحرک"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMAGE_FILTER),
        Field("fps", K_INT, "فریم در ثانیه", "Frames per second", default=8, minimum=1, maximum=30),
        Field("width", K_INT, "عرض (۰ = اصلی)", "Width (0 = keep)", default=640, minimum=0, maximum=2000),
        Field("out", K_SAVE, "ذخیره در", "Save to", filt="GIF (*.gif)"),
    ],
)
def image_gif_make(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    fps = max(1, int(v.get("fps") or 8))
    width = int(v.get("width") or 0)
    frames = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), path.name)
        image = _open_rgb(path)
        if width and image.width > width:
            image = image.resize((width, int(image.height * width / image.width)), Image.LANCZOS)
        frames.append(image.convert("P", palette=Image.ADAPTIVE))
    if not frames:
        raise ToolError(ctx.M("تصویری پیدا نشد.", "No images found."))
    size = frames[0].size
    frames = [frame if frame.size == size else frame.resize(size, Image.LANCZOS) for frame in frames]
    dest = save_target(ctx, str(v.get("out") or ""), "animation.gif")
    frames[0].save(dest, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0)
    return RunResult(message=ctx.M(f"GIF با {len(frames)} فریم ساخته شد.",
                                   f"GIF with {len(frames)} frames created."),
                     outputs=[str(dest)], open_target=str(dest))


# ---------------------------------------------------------------------- favicon
@tool(
    id="image_favicon_pack", category="image", name="بسته‌ی فاوآیکون سایت", name_en="Favicon pack",
    desc="از یک لوگو، بسته‌ی کامل فاوآیکون می‌سازد: ICO چندسایز + PNG برای موبایل و manifest آماده.",
    desc_en="Turns one logo into a full favicon pack: multi-size ICO, PNGs and a ready manifest.",
    icon="image", order=49, keywords=("favicon", "ico", "apple touch", "manifest", "لوگو", "سایت"),
    fields=[
        Field("src", K_FILES, "لوگو (PNG)", "Logo (PNG)", required=True, filt=IMAGE_FILTER),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_favicon_pack(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image, *_ = _require_pillow(ctx)
    files = files_or_raise(v, "src", ctx)
    source = files[0]
    target = out_dir(ctx, "favicons")
    image = _open_rgb(source).convert("RGBA")
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    made: List[str] = []
    sizes = [16, 32, 48, 64, 128, 180, 192, 256, 512]
    for size in sizes:
        resized = square.resize((size, size), Image.LANCZOS)
        name = f"icon-{size}.png"
        resized.save(target / name)
        made.append(str(target / name))
    square.resize((256, 256), Image.LANCZOS).save(
        target / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    made.append(str(target / "favicon.ico"))
    manifest = {
        "name": source.stem, "short_name": source.stem[:12], "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ], "theme_color": "#0F1420", "background_color": "#0F1420", "display": "standalone",
    }
    import json

    (target / "manifest.webmanifest").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    made.append(str(target / "manifest.webmanifest"))
    (target / "head-snippet.html").write_text(
        '<link rel="icon" href="/favicon.ico" sizes="any">\n'
        '<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">\n'
        '<link rel="apple-touch-icon" sizes="180x180" href="/icon-180.png">\n'
        '<link rel="manifest" href="/manifest.webmanifest">\n', encoding="utf-8")
    made.append(str(target / "head-snippet.html"))
    return RunResult(message=ctx.M(f"بسته‌ی فاوآیکون ({len(made)} فایل) آماده شد.",
                                   f"Favicon pack ready ({len(made)} files)."),
                     outputs=made, open_target=str(target))
