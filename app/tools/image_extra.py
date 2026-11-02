"""Advanced image tools: crop, borders, rounded corners, collage, EXIF, palette,
compare, base64."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_COLOR, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import ensure_dir, files_or_raise, first_file, out_dir, require, save_target
from .image_tools import IMG_FILTER, _flatten, _image, _open_images, _prepare_output


def _pil_ops(ctx: RunContext):
    require("PIL.Image", "Pillow", ctx)
    return require("PIL.ImageDraw", "Pillow", ctx)


# ---------------------------------------------------------------------- crop
@tool(
    id="image_crop", category="image", name="برش تصویر", name_en="Crop images",
    desc="حاشیه‌ها را به درصد یا پیکسل برش می‌دهد؛ حالت «مربع وسط» برای شبکه‌های اجتماعی.",
    desc_en="Crop margins by percent or pixels, or take a centred square.",
    icon="crop", order=101,
    keywords=("crop image", "trim photo", "برش عکس", "مربع کردن عکس", "اینستاگرام"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("mode", K_CHOICE, "حالت", "Mode", default="percent",
              choices=[("percent", "درصد از هر طرف", "Percent from each side"),
                       ("square", "مربع وسط", "Centred square"),
                       ("aspect", "نسبت ابعاد مشخص", "Specific aspect ratio")]),
        Field("top", K_FLOAT, "بالا (%)", "Top (%)", default=5, minimum=0, maximum=45, decimals=1),
        Field("bottom", K_FLOAT, "پایین (%)", "Bottom (%)", default=5, minimum=0, maximum=45, decimals=1),
        Field("left", K_FLOAT, "چپ (%)", "Left (%)", default=5, minimum=0, maximum=45, decimals=1),
        Field("right", K_FLOAT, "راست (%)", "Right (%)", default=5, minimum=0, maximum=45, decimals=1),
        Field("aspect", K_CHOICE, "نسبت", "Ratio", default="1:1",
              choices=[("1:1", "۱:۱ (مربع)", "1:1 (square)"), ("4:5", "۴:۵ (پست)", "4:5 (post)"),
                       ("16:9", "۱۶:۹ (ویدیو)", "16:9 (video)"), ("9:16", "۹:۱۶ (استوری)", "9:16 (story)"),
                       ("3:2", "۳:۲ (عکس)", "3:2 (photo)")],
              visible_when=("mode", "aspect")),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_crop(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    mode = str(v.get("mode") or "percent")
    outputs: List[str] = []
    for index, (path, im) in enumerate(opened, start=1):
        ctx.progress(index, len(opened), ctx.M(f"برش {path.name}", f"Cropping {path.name}"))
        width, height = im.size
        if mode == "percent":
            left = int(width * float(v.get("left") or 0) / 100)
            right = int(width * float(v.get("right") or 0) / 100)
            top = int(height * float(v.get("top") or 0) / 100)
            bottom = int(height * float(v.get("bottom") or 0) / 100)
        else:
            ratio_text = str(v.get("aspect") or "1:1") if mode == "aspect" else "1:1"
            try:
                ratio_w, ratio_h = (float(x) for x in ratio_text.split(":"))
            except Exception:
                ratio_w, ratio_h = 1.0, 1.0
            target_w = min(width, height * ratio_w / ratio_h)
            target_h = target_w * ratio_h / ratio_w
            left = int((width - target_w) / 2)
            top = int((height - target_h) / 2)
            right = left
            bottom = top
        box = (max(0, left), max(0, top), max(1, width - right), max(1, height - bottom))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            raise ToolError(ctx.M("مقدار برش بیش از حد بزرگ است.",
                                  "The crop is too aggressive for this image."))
        cropped = im.crop(box)
        target = _prepare_output(ctx, v, path, "_cropped", path.suffix or ".png")
        if target.suffix.lower() in (".jpg", ".jpeg") and cropped.mode != "RGB":
            cropped = _flatten(cropped)
        cropped.save(target, quality=95)
        outputs.append(str(target))
    return RunResult(
        message=ctx.M(f"{len(outputs)} تصویر برش خورد.", f"Cropped {len(outputs)} image(s)."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# -------------------------------------------------------------------- border
@tool(
    id="image_border", category="image", name="قاب و حاشیه", name_en="Add border / frame",
    desc="دور تصویر حاشیه‌ی رنگی یا قاب گرد اضافه می‌کند.",
    desc_en="Draw a coloured border or rounded frame around images.",
    icon="square", order=102,
    keywords=("border", "frame", "قاب عکس", "حاشیه تصویر"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("width", K_INT, "ضخامت (پیکسل)", "Thickness (px)", default=12, minimum=1, maximum=200),
        Field("color", K_COLOR, "رنگ", "Colour", default="#18C8F5"),
        Field("style", K_CHOICE, "سبک", "Style", default="solid",
              choices=[("solid", "ساده", "Solid"), ("rounded", "گوشه‌گرد", "Rounded"),
                       ("double", "دولایه", "Double line")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_border(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    ImageDraw = _pil_ops(ctx)
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    thickness = max(1, int(v.get("width") or 12))
    color = str(v.get("color") or "#18C8F5")
    style = str(v.get("style") or "solid")
    outputs: List[str] = []
    for index, (path, im) in enumerate(opened, start=1):
        ctx.progress(index, len(opened), ctx.M(f"قاب {path.name}", f"Framing {path.name}"))
        canvas_size = (im.width + thickness * 2, im.height + thickness * 2)
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        if style == "rounded":
            mask = Image.new("L", canvas_size, 0)
            radius = max(6, min(canvas_size) // 14)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, canvas_size[0] - 1, canvas_size[1] - 1], radius=radius, fill=255)
            canvas.paste(im.convert("RGBA"), (thickness, thickness))
            draw = ImageDraw.Draw(canvas)
            draw.rounded_rectangle([0, 0, canvas_size[0] - 1, canvas_size[1] - 1],
                                   radius=radius, outline=color, width=thickness)
            canvas.putalpha(mask)
        else:
            draw = ImageDraw.Draw(canvas)
            if style == "double":
                inner = max(1, thickness // 3)
                draw.rectangle([0, 0, canvas_size[0] - 1, canvas_size[1] - 1], outline=color, width=inner)
                gap = inner * 2
                draw.rectangle([gap, gap, canvas_size[0] - 1 - gap, canvas_size[1] - 1 - gap],
                               outline=color, width=inner)
                draw.rectangle([thickness, thickness, canvas_size[0] - 1 - thickness,
                                canvas_size[1] - 1 - thickness], fill=color)
            else:
                draw.rectangle([0, 0, canvas_size[0] - 1, canvas_size[1] - 1], fill=color)
            canvas.paste(im.convert("RGBA"), (thickness, thickness))
        target = _prepare_output(ctx, v, path, "_bordered", ".png")
        canvas.save(target)
        outputs.append(str(target))
    return RunResult(
        message=ctx.M(f"{len(outputs)} تصویر قاب شد.", f"Framed {len(outputs)} image(s)."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


@tool(
    id="image_rounded", category="image", name="گوشه‌های گرد", name_en="Rounded corners",
    desc="گوشه‌های تصویر را گرد می‌کند و پس‌زمینه‌ی بیرونی را شفاف می‌گذارد.",
    desc_en="Round the corners of images and make the outside transparent.",
    icon="circle", order=103,
    keywords=("rounded corners", "round image", "گوشه گرد", "شفاف"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("radius", K_INT, "شعاع گردی (%)", "Corner radius (%)", default=22, minimum=1, maximum=50),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def image_rounded(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    require("PIL.ImageDraw", "Pillow", ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    percent = max(1, min(50, int(v.get("radius") or 22))) / 100.0
    outputs: List[str] = []
    for index, (path, im) in enumerate(opened, start=1):
        ctx.progress(index, len(opened), ctx.M(f"گرد کردن {path.name}", f"Rounding {path.name}"))
        rgba = im.convert("RGBA")
        radius = int(min(rgba.size) * percent)
        mask = Image.new("L", rgba.size, 0)
        from PIL import ImageDraw

        ImageDraw.Draw(mask).rounded_rectangle([0, 0, rgba.width - 1, rgba.height - 1],
                                              radius=radius, fill=255)
        rgba.putalpha(mask)
        target = _prepare_output(ctx, v, path, "_rounded", ".png")
        rgba.save(target)
        outputs.append(str(target))
    return RunResult(
        message=ctx.M(f"{len(outputs)} تصویر گوشه‌گرد شد.", f"Rounded {len(outputs)} image(s)."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# -------------------------------------------------------------------- collage
@tool(
    id="image_collage", category="image", name="کلاژ تصاویر", name_en="Photo collage",
    desc="چند تصویر را در یک شبکه‌ی منظم با فاصله و پس‌زمینه‌ی دلخواه کنار هم می‌چیند.",
    desc_en="Arrange several images into a grid collage with spacing and background.",
    icon="grid", order=104,
    keywords=("collage", "grid", "کلاژ", "چند عکس کنار هم", "پازل عکس"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("columns", K_INT, "تعداد ستون", "Columns", default=3, minimum=1, maximum=8),
        Field("cell", K_INT, "اندازه‌ی هر خانه (پیکسل)", "Cell size (px)", default=600,
              minimum=120, maximum=2400, step=20),
        Field("gap", K_INT, "فاصله (پیکسل)", "Gap (px)", default=16, minimum=0, maximum=120),
        Field("background", K_COLOR, "پس‌زمینه", "Background", default="#0F1420"),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="تصویر (*.png *.jpg)"),
    ],
)
def image_collage(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    opened = _open_images(ctx, files)
    columns = max(1, min(8, int(v.get("columns") or 3)))
    cell = max(120, int(v.get("cell") or 600))
    gap = max(0, int(v.get("gap") or 16))
    rows = (len(opened) + columns - 1) // columns
    canvas_w = columns * cell + gap * (columns + 1)
    canvas_h = rows * cell + gap * (rows + 1)
    canvas = Image.new("RGB", (canvas_w, canvas_h), str(v.get("background") or "#0F1420"))
    for index, (path, im) in enumerate(opened):
        ctx.progress(index + 1, len(opened), ctx.M(f"چیدن {path.name}", f"Placing {path.name}"))
        row, column = divmod(index, columns)
        thumb = im.copy()
        thumb.thumbnail((cell, cell), Image.LANCZOS)
        x = gap + column * (cell + gap) + (cell - thumb.width) // 2
        y = gap + row * (cell + gap) + (cell - thumb.height) // 2
        if thumb.mode in ("RGBA", "LA", "P"):
            canvas.paste(thumb.convert("RGBA"), (x, y), thumb.convert("RGBA"))
        else:
            canvas.paste(thumb.convert("RGB"), (x, y))
    dest = save_target(ctx, str(v.get("out") or ""), "collage.png")
    if dest.suffix.lower() in (".jpg", ".jpeg"):
        canvas.save(dest, quality=92)
    else:
        canvas.save(dest)
    return RunResult(
        message=ctx.M(f"کلاژ {len(opened)} تصویر ساخته شد ({canvas_w}×{canvas_h}).\n{dest}",
                      f"Collage of {len(opened)} image(s) created ({canvas_w}×{canvas_h}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


# ----------------------------------------------------------------- exif / meta
@tool(
    id="image_exif", category="image", name="اطلاعات و حذف EXIF", name_en="EXIF: view or strip",
    desc="متادیتای عکس (دوربین، تاریخ، GPS) را نشان می‌دهد یا کامل پاک می‌کند.",
    desc_en="Show photo metadata (camera, date, GPS) or strip it completely.",
    icon="info", order=105,
    keywords=("exif", "metadata", "gps", "متادیتا", "حذف اطلاعات عکس"),
    fields=[
        Field("src", K_FILES, "تصاویر", "Images", required=True, filt=IMG_FILTER, multiple=True),
        Field("action", K_CHOICE, "عملیات", "Action", default="show",
              choices=[("show", "نمایش اطلاعات", "Show metadata"),
                       ("strip", "پاک کردن و ذخیره", "Strip and save")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder", visible_when=("action", "strip")),
    ],
)
def image_exif(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    files = files_or_raise(v, "src", ctx)
    action = str(v.get("action") or "show")
    if action == "show":
        lines: List[str] = []
        for index, path in enumerate(files, start=1):
            ctx.progress(index, len(files), path.name)
            try:
                im = Image.open(path)
                exif = im.getexif()
            except Exception as exc:
                lines.append(f"• {path.name}: {exc}")
                continue
            lines.append(f"■ {path.name} — {im.width}×{im.height}, {human_size(path.stat().st_size)}")
            if not exif:
                lines.append("   " + ctx.M("بدون اطلاعات EXIF", "no EXIF data"))
                continue
            for tag_id, value in list(exif.items())[:20]:
                from PIL.ExifTags import TAGS

                name = TAGS.get(tag_id, str(tag_id))
                text = str(value)
                lines.append(f"   {name}: {text[:80]}")
        return RunResult(
            message=ctx.M(f"اطلاعات {len(files)} تصویر خوانده شد.", f"Read metadata of {len(files)} image(s)."),
            details="\n".join(lines),
        )
    outputs: List[str] = []
    for index, path in enumerate(files, start=1):
        ctx.progress(index, len(files), ctx.M(f"پاک‌سازی {path.name}", f"Stripping {path.name}"))
        im = Image.open(path)
        data = list(im.getdata())
        clean = Image.new(im.mode, im.size)
        clean.putdata(data)
        target = _prepare_output(ctx, v, path, "_clean", path.suffix or ".png")
        if target.suffix.lower() in (".jpg", ".jpeg") and clean.mode != "RGB":
            clean = _flatten(clean)
        clean.save(target)
        outputs.append(str(target))
    return RunResult(
        message=ctx.M(f"اطلاعات {len(outputs)} تصویر پاک شد.", f"Stripped metadata from {len(outputs)} image(s)."),
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


@tool(
    id="image_palette", category="image", name="رنگ‌های اصلی تصویر", name_en="Colour palette",
    desc="پرجمعیت‌ترین رنگ‌های تصویر را با کد HEX و سهم هر رنگ استخراج می‌کند.",
    desc_en="Extract the dominant colours with HEX codes and their share.",
    icon="palette", order=106,
    keywords=("palette", "dominant colors", "رنگ اصلی", "کد رنگ", "heх"),
    fields=[
        Field("src", K_FILE, "تصویر", "Image", required=True, filt=IMG_FILTER),
        Field("count", K_INT, "تعداد رنگ", "Number of colours", default=8, minimum=2, maximum=24),
    ],
)
def image_palette(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    src = first_file(v, "src", ctx)
    count = max(2, min(24, int(v.get("count") or 8)))
    im = Image.open(src).convert("RGB")
    im.thumbnail((320, 320))
    ctx.progress(1, 3, ctx.M("تحلیل رنگ‌ها", "Analysing colours"))
    quantized = im.quantize(colors=count, method=Image.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    total = sum(number for number, _ in counts) or 1
    rows = sorted(counts, key=lambda item: -item[0])
    lines = []
    ctx.progress(2, 3, "")
    for number, palette_index in rows:
        red, green, blue = palette[palette_index * 3: palette_index * 3 + 3]
        share = number / total * 100
        lines.append(f"#{red:02X}{green:02X}{blue:02X}   {share:5.1f}%")
    ctx.progress(3, 3, "")
    return RunResult(
        message=ctx.M(f"{count} رنگ اصلی تصویر:", f"Top {count} colours:"),
        details="\n".join(lines),
    )


@tool(
    id="image_compare", category="image", name="مقایسه‌ی دو تصویر", name_en="Compare two images",
    desc="شباهت دو تصویر را درصدی می‌سنجد و تصویر تفاوت را ذخیره می‌کند.",
    desc_en="Measure how similar two images are and save a difference map.",
    icon="compare", order=107,
    keywords=("compare images", "diff image", "مقایسه عکس", "تفاوت تصویر"),
    fields=[
        Field("a", K_FILE, "تصویر اول", "First image", required=True, filt=IMG_FILTER),
        Field("b", K_FILE, "تصویر دوم", "Second image", required=True, filt=IMG_FILTER),
        Field("save_diff", K_BOOL, "ذخیره‌ی نقشه‌ی تفاوت", "Save difference map", default=True),
    ],
)
def image_compare(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    Image = _image(ctx)
    require("PIL.ImageChops", "Pillow", ctx)
    from PIL import ImageChops

    a_path = first_file(v, "a", ctx)
    b_path = first_file(v, "b", ctx)
    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    ctx.progress(1, 3, ctx.M("هم‌اندازه کردن", "Normalising sizes"))
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    diff = ImageChops.difference(a, b)
    histogram = diff.convert("L").histogram()
    total = sum(histogram) or 1
    changed = sum(histogram[12:])
    similarity = max(0.0, 100.0 * (1 - changed / total))
    bbox = diff.getbbox()
    ctx.progress(2, 3, "")
    outputs: List[str] = []
    if v.get("save_diff", True):
        amplified = diff.point(lambda value: min(255, value * 4))
        dest = unique_path(out_dir(ctx) / f"diff_{a_path.stem}_vs_{b_path.stem}.png")
        amplified.save(dest)
        outputs.append(str(dest))
    ctx.progress(3, 3, "")
    region = ctx.M("هیچ تفاوتی", "no difference") if bbox is None else f"{bbox}"
    return RunResult(
        message=ctx.M(f"شباهت: {similarity:.2f}%\nناحیه‌ی تفاوت: {region}",
                      f"Similarity: {similarity:.2f}%\nDifference region: {region}"),
        outputs=outputs, open_target=str(Path(outputs[0]).parent) if outputs else None,
    )


@tool(
    id="image_base64", category="image", name="تصویر ⇄ Base64", name_en="Image ⇄ Base64",
    desc="تصویر را به متن Base64 / Data-URI تبدیل می‌کند یا از متن، فایل تصویر می‌سازد.",
    desc_en="Turn an image into Base64 / a data URI, or rebuild an image from text.",
    icon="code", order=108,
    keywords=("base64", "data uri", "inline image", "بیس ۶۴"),
    fields=[
        Field("mode", K_CHOICE, "جهت", "Direction", default="encode",
              choices=[("encode", "تصویر → متن", "Image → text"),
                       ("decode", "متن → تصویر", "Text → image")]),
        Field("src", K_FILE, "تصویر", "Image", filt=IMG_FILTER, visible_when=("mode", "encode")),
        Field("text", K_TEXT, "متن Base64", "Base64 text", placeholder="data:image/png;base64,..."),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="تصویر (*.png *.jpg)"),
    ],
)
def image_base64(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    mode = str(v.get("mode") or "encode")
    if mode == "encode":
        src = first_file(v, "src", ctx)
        payload = base64.b64encode(src.read_bytes()).decode("ascii")
        suffix = src.suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if suffix == "jpg" else suffix
        prefix = f"data:image/{mime};base64,"
        dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_base64.txt")
        dest.write_text(prefix + payload, encoding="utf-8")
        return RunResult(
            message=ctx.M(f"Base64 ساخته شد ({len(payload):,} نویسه) و در فایل ذخیره شد.\n{dest}",
                          f"Base64 created ({len(payload):,} chars) and saved.\n{dest}"),
            outputs=[str(dest)], open_target=str(dest),
            details=prefix + payload[:400] + ("…" if len(payload) > 400 else ""),
        )
    raw = str(v.get("text") or "").strip()
    if not raw:
        raise ToolError(ctx.M("متن Base64 خالی است.", "The Base64 text is empty."))
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode("".join(raw.split()), validate=False)
    except Exception as exc:
        raise ToolError(ctx.M(f"Base64 نامعتبر است: {exc}", f"Invalid Base64: {exc}"))
    dest = save_target(ctx, str(v.get("out") or ""), "decoded.png")
    with open(dest, "wb") as fh:
        fh.write(data)
    return RunResult(
        message=ctx.M(f"تصویر بازسازی شد ({human_size(len(data))}).\n{dest}",
                      f"Image rebuilt ({human_size(len(data))}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )
