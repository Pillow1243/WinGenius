"""Extra media tools: join clips, speed, contact sheets, audio merge, subtitles."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILES, K_FILE, K_FLOAT, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import ensure_dir, files_or_raise, first_file, out_dir, require, save_target
from .media_tools import MEDIA_FILTER, _duration, _ffmpeg, _output, _run_ffmpeg

AUDIO_FILTER = "صدا (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus);;همه فایل‌ها (*)"
SUB_FILTER = "زیرنویس (*.srt *.ass *.vtt);;همه فایل‌ها (*)"


def _quote(path: Path) -> str:
    return str(path).replace("'", "'\\''")


@tool(
    id="video_merge", category="media", name="چسباندن ویدیوها", name_en="Join video clips",
    desc="چند ویدیوی هم‌فرمت را پشت سر هم به یک فایل تبدیل می‌کند.",
    desc_en="Concatenate several same-format video clips into one file.",
    icon="film", order=101,
    keywords=("merge video", "join video", "concat", "چسباندن فیلم", "ترکیب ویدیو"),
    fields=[
        Field("src", K_FILES, "ویدیوها (به ترتیب)", "Videos (in order)", required=True,
              filt=MEDIA_FILTER, multiple=True),
        Field("reencode", K_BOOL, "تبدیل مجدد (برای فایل‌های ناهمگون)", "Re-encode (mixed sources)",
              default=False),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="ویدیو (*.mp4 *.mkv)"),
    ],
)
def video_merge(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = files_or_raise(v, "src", ctx)
    if len(files) < 2:
        raise ToolError(ctx.M("حداقل دو ویدیو انتخاب کنید.", "Choose at least two videos."))
    dest = save_target(ctx, str(v.get("out") or ""), f"{files[0].stem}_merged.mp4")
    listing = Path(tempfile.mkdtemp(prefix="wg_concat_")) / "list.txt"
    listing.write_text("\n".join(f"file '{_quote(p)}'" for p in files), encoding="utf-8")
    total = sum(_duration(p) for p in files)
    if v.get("reencode"):
        args: List[str] = ["-y", "-f", "concat", "-safe", "0", "-i", str(listing)]
        for _ in files:
            args += ["-i", "null"]
        # simplest robust path: concat filter is complex for N inputs, use filter_complex
        filter_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(files)))
        args = ["-y"]
        for path in files:
            args += ["-i", str(path)]
        args += ["-filter_complex",
                 f"{filter_inputs}concat=n={len(files)}:v=1:a=1[v][a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
                 "-c:a", "aac", "-b:a", "160k", str(dest)]
    else:
        args = ["-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)]
    _run_ffmpeg(ctx, args, total, ctx.M("چسباندن ویدیوها…", "Joining clips…"))
    try:
        listing.unlink()
        listing.parent.rmdir()
    except Exception:
        pass
    if not dest.exists():
        raise ToolError(ctx.M("خروجی ساخته نشد.", "No output was produced."))
    return RunResult(
        message=ctx.M(f"{len(files)} ویدیو به هم چسبید.\n{dest}", f"Joined {len(files)} clips.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="video_speed", category="media", name="تغییر سرعت ویدیو", name_en="Change video speed",
    desc="ویدیو را تندتر یا کندتر می‌کند و صدا را هم هم‌گام نگه می‌دارد.",
    desc_en="Make a video faster or slower while keeping audio in sync.",
    icon="fast", order=102,
    keywords=("speed up video", "slow motion", "تند کردن فیلم", "اسلوموشن"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("speed", K_FLOAT, "ضریب سرعت", "Speed factor", default=1.5, minimum=0.25, maximum=4.0,
              decimals=2, step=0.25, help="مثلاً ۲ = دو برابر سریع‌تر، ۰٫۵ = نصف سرعت.",
              help_en="e.g. 2 = twice as fast, 0.5 = half speed."),
        Field("keep_audio", K_BOOL, "نگه داشتن صدا", "Keep audio", default=True),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="ویدیو (*.mp4)"),
    ],
)
def video_speed(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    speed = max(0.25, min(4.0, float(v.get("speed") or 1.5)))
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_x{speed:g}.mp4")
    args = ["-y", "-i", str(src), "-filter_complex", f"[0:v]setpts={1 / speed:.6f}*PTS[v]"]
    if v.get("keep_audio", True):
        # atempo only accepts 0.5–2.0, chain it for bigger factors
        remaining = speed
        chain = []
        while remaining > 2.0:
            chain.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            chain.append("atempo=0.5")
            remaining /= 0.5
        chain.append(f"atempo={remaining:.6f}")
        args += ["-filter_complex",
                 f"[0:v]setpts={1 / speed:.6f}*PTS[v];[0:a]{','.join(chain)}[a]",
                 "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    else:
        args += ["-map", "[v]", "-an"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(dest)]
    _run_ffmpeg(ctx, args, _duration(src), ctx.M("تغییر سرعت…", "Changing speed…"))
    return RunResult(
        message=ctx.M(f"سرعت ×{speed:g} اعمال شد.\n{dest}", f"Speed ×{speed:g} applied.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="video_contact_sheet", category="media", name="پوستر فریم‌های ویدیو",
    name_en="Video contact sheet",
    desc="یک تصویر شبکه‌ای از فریم‌های ویدیو می‌سازد (پیش‌نمایش سریع محتوا).",
    desc_en="Build a grid image of frames — a quick visual overview of the video.",
    icon="grid", order=103,
    keywords=("contact sheet", "thumbnails", "پیش نمایش ویدیو", "شبکه فریم"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("count", K_INT, "تعداد فریم", "Frames", default=12, minimum=4, maximum=36),
        Field("columns", K_INT, "ستون‌ها", "Columns", default=4, minimum=2, maximum=8),
        Field("cell", K_INT, "اندازه‌ی خانه", "Cell size", default=360, minimum=120, maximum=900, step=20),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="تصویر (*.png *.jpg)"),
    ],
)
def video_contact_sheet(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    Image = require("PIL.Image", "Pillow", ctx)
    src = first_file(v, "src", ctx)
    count = max(4, min(36, int(v.get("count") or 12)))
    columns = max(2, min(8, int(v.get("columns") or 4)))
    cell = max(120, int(v.get("cell") or 360))
    duration = _duration(src)
    if duration <= 0:
        raise ToolError(ctx.M("مدت ویدیو مشخص نشد.", "Could not read the video duration."))
    work = ensure_dir(Path(tempfile.mkdtemp(prefix="wg_sheet_")))
    rows = (count + columns - 1) // columns
    canvas = Image.new("RGB", (columns * cell, rows * cell), "#0F1420")
    for index in range(count):
        ctx.progress(index + 1, count, ctx.M(f"فریم {index + 1} از {count}", f"Frame {index + 1} of {count}"))
        moment = duration * (index + 0.5) / count
        frame = work / f"f{index:03d}.jpg"
        _run_ffmpeg(ctx, ["-y", "-ss", f"{moment:.3f}", "-i", str(src), "-frames:v", "1",
                          "-vf", f"scale={cell}:{cell}:force_original_aspect_ratio=decrease,"
                                 f"pad={cell}:{cell}:(ow-iw)/2:(oh-ih)/2:color=#0F1420",
                          "-q:v", "3", str(frame)], 0, "")
        if frame.exists():
            thumb = Image.open(frame).convert("RGB")
            column, row = index % columns, index // columns
            canvas.paste(thumb, (column * cell, row * cell))
            frame.unlink(missing_ok=True)
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_sheet.png")
    if dest.suffix.lower() in (".jpg", ".jpeg"):
        canvas.save(dest, quality=90)
    else:
        canvas.save(dest)
    try:
        work.rmdir()
    except Exception:
        pass
    return RunResult(
        message=ctx.M(f"پوستر {count} فریم ساخته شد.\n{dest}", f"Contact sheet with {count} frames created.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="audio_join", category="media", name="چسباندن فایل‌های صوتی", name_en="Join audio files",
    desc="چند فایل صوتی را پشت سر هم به یک فایل تبدیل می‌کند.",
    desc_en="Concatenate several audio files into one.",
    icon="music", order=104,
    keywords=("merge audio", "join mp3", "چسباندن صدا", "ترکیب موسیقی"),
    fields=[
        Field("src", K_FILES, "فایل‌های صوتی (به ترتیب)", "Audio files (in order)", required=True,
              filt=AUDIO_FILTER, multiple=True),
        Field("fmt", K_CHOICE, "فرمت خروجی", "Output format", default="mp3",
              choices=[("mp3", "MP3", "MP3"), ("m4a", "M4A (AAC)", "M4A"), ("wav", "WAV", "WAV"),
                       ("flac", "FLAC", "FLAC")]),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="صدا (*.mp3 *.m4a *.wav *.flac)"),
    ],
)
def audio_join(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = files_or_raise(v, "src", ctx)
    if len(files) < 2:
        raise ToolError(ctx.M("حداقل دو فایل صوتی انتخاب کنید.", "Choose at least two audio files."))
    fmt = str(v.get("fmt") or "mp3")
    dest = save_target(ctx, str(v.get("out") or ""), f"{files[0].stem}_joined.{fmt}")
    args: List[str] = ["-y"]
    for path in files:
        args += ["-i", str(path)]
    layout = "".join(f"[{i}:a:0]" for i in range(len(files)))
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
             "m4a": ["-c:a", "aac", "-b:a", "192k"],
             "wav": ["-c:a", "pcm_s16le"],
             "flac": ["-c:a", "flac"]}.get(fmt, ["-c:a", "copy"])
    args += ["-filter_complex", f"{layout}concat=n={len(files)}:v=0:a=1[out]",
             "-map", "[out]", *codec, str(dest)]
    _run_ffmpeg(ctx, args, sum(_duration(p) for p in files),
                ctx.M("چسباندن فایل‌های صوتی…", "Joining audio…"))
    return RunResult(
        message=ctx.M(f"{len(files)} فایل صوتی به هم چسبید.\n{dest}", f"Joined {len(files)} audio files.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="audio_tempo", category="media", name="تغییر سرعت صدا (بدون تغییر لحن)",
    name_en="Change audio tempo",
    desc="سرعت پخش صدا را کم یا زیاد می‌کند بدون اینکه زیر و بمی صدا تغییر کند.",
    desc_en="Speed audio up or down without changing its pitch.",
    icon="fast", order=105,
    keywords=("tempo", "speed audio", "سرعت پادکست", "تند کردن صدا"),
    fields=[
        Field("src", K_FILE, "فایل صوتی", "Audio file", required=True, filt=AUDIO_FILTER),
        Field("tempo", K_FLOAT, "ضریب سرعت", "Tempo factor", default=1.25, minimum=0.5, maximum=3.0,
              decimals=2, step=0.05),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="صدا (*.mp3 *.m4a *.wav)"),
    ],
)
def audio_tempo(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    tempo = max(0.5, min(3.0, float(v.get("tempo") or 1.25)))
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_tempo{tempo:g}.mp3")
    chain = []
    remaining = tempo
    while remaining > 2.0:
        chain.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        chain.append("atempo=0.5")
        remaining /= 0.5
    chain.append(f"atempo={remaining:.6f}")
    _run_ffmpeg(ctx, ["-y", "-i", str(src), "-filter:a", ",".join(chain),
                      "-c:a", "libmp3lame", "-b:a", "192k", str(dest)],
                _duration(src), ctx.M("تغییر سرعت صدا…", "Changing tempo…"))
    return RunResult(
        message=ctx.M(f"سرعت صدا ×{tempo:g} شد.\n{dest}", f"Audio tempo ×{tempo:g}.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for an ffmpeg lavfi `subtitles='...'` option."""
    raw = str(path).replace("\\", "/")
    out: List[str] = []
    for ch in raw:
        if ch in ("\\", "'", ":", ",", ";", "[", "]"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


@tool(
    id="video_burn_subtitles", category="media", name="چسباندن زیرنویس روی ویدیو",
    name_en="Burn subtitles into video",
    desc="زیرنویس SRT/ASS را روی تصویر ویدیو حک می‌کند تا همه‌جا دیده شود.",
    desc_en="Burn an SRT/ASS subtitle track into the video picture.",
    icon="text", order=106,
    keywords=("burn subtitles", "hardcode subtitle", "زیرنویس چسبیده", "srt روی فیلم"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("subs", K_FILE, "فایل زیرنویس", "Subtitle file", required=True, filt=SUB_FILTER),
        Field("font_size", K_INT, "اندازه‌ی قلم", "Font size", default=24, minimum=10, maximum=72),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="ویدیو (*.mp4 *.mkv)"),
    ],
)
def video_burn_subtitles(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    subs = first_file(v, "subs", ctx)
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_subbed.mp4")
    style = f"FontSize={max(10, int(v.get('font_size') or 24))},Outline=1,Shadow=0"
    path_for_filter = _escape_ffmpeg_filter_path(subs)
    args = ["-y", "-i", str(src), "-vf", f"subtitles='{path_for_filter}':force_style='{style}'",
            "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(dest)]
    _run_ffmpeg(ctx, args, _duration(src), ctx.M("حک کردن زیرنویس…", "Burning subtitles…"))
    return RunResult(
        message=ctx.M(f"زیرنویس روی ویدیو حک شد.\n{dest}", f"Subtitles burned in.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="video_gif_optimize", category="media", name="بهینه‌سازی GIF", name_en="Optimise GIF",
    desc="GIF را با پالت بهینه و ابعاد هوشمند کوچک می‌کند.",
    desc_en="Shrink a GIF with an optimised palette and balanced size.",
    icon="film", order=107,
    keywords=("optimize gif", "smaller gif", "کم کردن حجم گیف"),
    fields=[
        Field("src", K_FILE, "فایل GIF", "GIF file", required=True, filt="GIF (*.gif);;همه فایل‌ها (*)"),
        Field("width", K_INT, "عرض (پیکسل)", "Width (px)", default=480, minimum=80, maximum=1280, step=20),
        Field("fps", K_INT, "فریم در ثانیه", "Frames per second", default=12, minimum=5, maximum=30),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="GIF (*.gif)"),
    ],
)
def video_gif_optimize(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_opt.gif")
    width = max(80, int(v.get("width") or 480))
    fps = max(5, min(30, int(v.get("fps") or 12)))
    filters = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
               f"[a]palettegen=max_colors=192:stats_mode=diff[p];"
               f"[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle")
    _run_ffmpeg(ctx, ["-y", "-i", str(src), "-filter_complex", filters, "-loop", "0", str(dest)],
                _duration(src), ctx.M("بهینه‌سازی GIF…", "Optimising GIF…"))
    before = src.stat().st_size
    after = dest.stat().st_size if dest.exists() else 0
    from ..core.utils import human_size

    return RunResult(
        message=ctx.M(f"GIF بهینه شد: {human_size(before)} → {human_size(after)}\n{dest}",
                      f"GIF optimised: {human_size(before)} → {human_size(after)}\n{dest}"),
        outputs=[str(dest)], open_target=str(dest),
    )


@tool(
    id="audio_trim", category="media", name="برش صدا", name_en="Trim audio",
    desc="از یک فایل صوتی فقط بخش بین دو زمان را نگه می‌دارد (مثلاً 00:10 تا 01:30).",
    desc_en="Keep only the part of an audio file between two timestamps.",
    icon="scissors", order=108,
    keywords=("trim audio", "cut mp3", "برش صدا", "کوتاه کردن فایل صوتی"),
    fields=[
        Field("src", K_FILE, "فایل صوتی", "Audio file", required=True, filt=AUDIO_FILTER),
        Field("start", K_TEXT, "شروع", "Start", default="00:00:00", placeholder="00:00:00"),
        Field("end", K_TEXT, "پایان", "End", default="", placeholder="00:01:00"),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="صدا (*.mp3 *.m4a *.wav)"),
    ],
)
def audio_trim(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    start = str(v.get("start") or "0").strip() or "0"
    end = str(v.get("end") or "").strip()
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_trim.mp3")
    args = ["-y", "-ss", start, "-i", str(src)]
    if end:
        args += ["-to", end]
    args += ["-c:a", "libmp3lame", "-b:a", "192k", str(dest)]
    _run_ffmpeg(ctx, args, _duration(src), ctx.M("برش صدا…", "Trimming audio…"))
    if not dest.exists() or dest.stat().st_size == 0:
        raise ToolError(ctx.M("برش انجام نشد؛ زمان‌ها را بررسی کنید.", "Nothing was produced; check the times."))
    return RunResult(
        message=ctx.M(f"فایل صوتی برش خورد ({human_size(dest.stat().st_size)}).\n{dest}",
                      f"Audio trimmed ({human_size(dest.stat().st_size)}).\n{dest}"),
        outputs=[str(dest)], open_target=str(dest))


@tool(
    id="audio_normalize", category="media", name="یکسان‌سازی بلندی صدا",
    name_en="Normalise loudness",
    desc="بلندی صدای فایل را با استاندارد EBU R128 یکسان می‌کند تا آرام و بلند نداشته باشد.",
    desc_en="Normalise loudness with EBU R128 so quiet and loud parts match.",
    icon="music", order=109,
    keywords=("normalize audio", "loudness", "هم سطح کردن صدا", "بلندی صدا", "podcast"),
    fields=[
        Field("src", K_FILE, "فایل صوتی", "Audio file", required=True, filt=AUDIO_FILTER),
        Field("target", K_FLOAT, "بلندی هدف (LUFS)", "Target loudness (LUFS)", default=-16.0,
              minimum=-30, maximum=-8, decimals=1),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="صدا (*.mp3 *.m4a *.wav)"),
    ],
)
def audio_normalize(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    target = float(v.get("target") if v.get("target") is not None else -16.0)
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_norm.mp3")
    filters = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    _run_ffmpeg(ctx, ["-y", "-i", str(src), "-af", filters, "-c:a", "libmp3lame",
                      "-b:a", "192k", str(dest)], _duration(src),
                ctx.M("یکسان‌سازی بلندی…", "Normalising loudness…"))
    return RunResult(
        message=ctx.M(f"بلندی صدا روی {target:g} LUFS تنظیم شد.\n{dest}",
                      f"Loudness normalised to {target:g} LUFS.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest))


@tool(
    id="video_watermark", category="media", name="واترمارک ویدیو", name_en="Video watermark",
    desc="لوگو یا متن را با شفافیت و موقعیت دلخواه روی ویدیو می‌گذارد.",
    desc_en="Overlay a logo or text on a video with custom opacity and position.",
    icon="stamp", order=110,
    keywords=("video watermark", "logo on video", "واترمارک فیلم", "لوگو روی ویدیو"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("mode", K_CHOICE, "نوع", "Type", default="image",
              choices=[("image", "تصویر/لوگو", "Image/logo"), ("text", "متن", "Text")]),
        Field("image", K_FILE, "تصویر", "Image", filt="تصاویر (*.png *.jpg *.jpeg);;همه فایل‌ها (*)",
              visible_when=("mode", "image")),
        Field("text", K_TEXT, "متن", "Text", default="WinGenius", visible_when=("mode", "text")),
        Field("position", K_CHOICE, "موقعیت", "Position", default="br",
              choices=[("br", "پایین راست", "Bottom right"), ("bl", "پایین چپ", "Bottom left"),
                       ("tr", "بالا راست", "Top right"), ("tl", "بالا چپ", "Top left")]),
        Field("opacity", K_FLOAT, "شفافیت", "Opacity", default=0.75, minimum=0.1, maximum=1.0, decimals=2),
        Field("out", K_SAVE, "فایل خروجی", "Output file", filt="ویدیو (*.mp4)"),
    ],
)
def video_watermark(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    opacity = max(0.1, min(1.0, float(v.get("opacity") or 0.75)))
    position = {"tl": "20:20", "tr": "W-w-20:20", "bl": "20:H-h-20",
                "br": "W-w-20:H-h-20"}.get(str(v.get("position") or "br"), "W-w-20:H-h-20")
    dest = save_target(ctx, str(v.get("out") or ""), f"{src.stem}_wm.mp4")
    if str(v.get("mode") or "image") == "image":
        image_path = first_file(v, "image", ctx)
        args = ["-y", "-i", str(src), "-i", str(image_path), "-filter_complex",
                f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.2f}[wm];"
                f"[0:v][wm]overlay={position}[v]",
                "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "22", "-c:a", "copy", str(dest)]
    else:
        text = str(v.get("text") or "WinGenius").replace(":", chr(92) + ":").replace("'", "")
        draw = (f"drawtext=text='{text}':x={position.split(':')[0]}:y={position.split(':')[1]}:"
                f"fontsize=h/22:fontcolor=white@{opacity:.2f}:borderw=2:bordercolor=black@0.6")
        args = ["-y", "-i", str(src), "-vf", draw, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "22", "-c:a", "copy", str(dest)]
    _run_ffmpeg(ctx, args, _duration(src), ctx.M("افزودن واترمارک…", "Adding the watermark…"))
    return RunResult(
        message=ctx.M(f"واترمارک اعمال شد.\n{dest}", f"Watermark applied.\n{dest}"),
        outputs=[str(dest)], open_target=str(dest))


# --------------------------------------------------------------- transcription
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts"}


def _srt_time(seconds: float) -> str:
    """01:02:03,456 — the timestamp format SRT players expect."""
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, tail = divmod(rest, 60)
    whole = int(tail)
    millis = int(round((tail - whole) * 1000))
    if millis == 1000:                       # rounding can overflow into the next second
        whole += 1
        millis = 0
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"


def _load_whisper(ctx: RunContext):
    """faster-whisper first (CTranslate2, much quicker), then plain openai-whisper."""
    try:
        from faster_whisper import WhisperModel            # noqa: F401
        return "faster"
    except Exception:
        pass
    try:
        import whisper                                      # noqa: F401
        return "openai"
    except Exception:
        raise ToolError(ctx.M(
            "برای تبدیل گفتار به متن یک موتور آفلاین لازم است (هیچ‌چیز به اینترنت فرستاده نمی‌شود):\n"
            "  pip install faster-whisper        ← سبک‌تر و سریع‌تر (پیشنهادی)\n"
            "  pip install openai-whisper        ← نسخه‌ی رسمی\n\n"
            "بار اول، مدل انتخابی دانلود می‌شود (tiny ≈ ۷۵ مگابایت، small ≈ ۵۰۰ مگابایت)؛ "
            "بعد از آن کامل آفلاین کار می‌کند.",
            "Speech-to-text needs a local engine (nothing is uploaded):\n"
            "  pip install faster-whisper        <- lighter and faster (recommended)\n"
            "  pip install openai-whisper        <- the official package\n\n"
            "The model is downloaded once (tiny ~75 MB, small ~500 MB) and then runs fully offline.",
        ))


_ALLOWED_WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
_ALLOWED_WHISPER_LANGS = {"", "auto", "fa", "en", "ar", "tr", "de", "fr", "es", "ru", "zh", "ja"}


@tool(
    id="audio_transcribe", category="media", name="گفتار به متن (زیرنویس خودکار)",
    name_en="Speech to text (auto subtitles)",
    desc=("صدا یا ویدیو را به متن تبدیل می‌کند و می‌تواند هم‌زمان فایل زیرنویس `.srt` بسازد. "
          "کاملاً محلی با Whisper؛ چیزی آپلود نمی‌شود."),
    desc_en=("Turn audio or video into text and optionally a ready-to-play `.srt` subtitle file. "
             "Runs locally with Whisper — nothing is uploaded."),
    icon="music", order=175,
    keywords=("speech to text", "transcribe", "subtitle", "زیرنویس", "تبدیل صدا به متن",
              "whisper", "متن ویدیو", "نویسه‌برداری"),
    fields=[
        Field("src", K_FILE, "فایل صدا یا ویدیو", "Audio or video file", required=True,
              filt="صدا/ویدیو (*.mp3 *.wav *.m4a *.ogg *.flac *.mp4 *.mkv *.mov *.webm *.avi);;همه فایل‌ها (*)"),
        Field("model", K_CHOICE, "دقت مدل", "Model size", default="small",
              choices=[("tiny", "tiny — سریع‌ترین", "tiny — fastest"),
                       ("base", "base", "base"), ("small", "small — متعادل", "small — balanced"),
                       ("medium", "medium — دقیق‌تر", "medium — more accurate"),
                       ("large-v3", "large-v3 — بهترین", "large-v3 — best")],
              help="مدل بزرگ‌تر دقیق‌تر ولی کندتر است؛ برای فارسی «small» یا بالاتر بهتر جواب می‌دهد.",
              help_en="Bigger models are slower but more accurate; for Persian, small or above."),
        Field("language", K_CHOICE, "زبان گفتار", "Spoken language", default="auto",
              choices=[("auto", "تشخیص خودکار", "Detect automatically"),
                       ("fa", "فارسی", "Persian"), ("en", "انگلیسی", "English"),
                       ("ar", "عربی", "Arabic"), ("tr", "ترکی", "Turkish")]),
        Field("fmt", K_CHOICE, "قالب خروجی", "Output format", default="srt",
              choices=[("srt", "زیرنویس (SRT)", "Subtitles (SRT)"),
                       ("txt", "متن ساده (TXT)", "Plain text (TXT)"),
                       ("both", "هر دو", "Both")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def audio_transcribe(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    src = first_file(v, "src", ctx)
    model_name = str(v.get("model") or "small").strip()
    if model_name not in _ALLOWED_WHISPER_MODELS:
        raise ToolError(ctx.M(
            f"مدل «{model_name}» مجاز نیست (یکی از tiny/base/small/medium/large-v3 را انتخاب کنید).",
            f"Unsupported Whisper model '{model_name}' (choose tiny, base, small, medium, or large-v3).",
        ))
    language = str(v.get("language") or "auto").strip().lower()
    if language not in _ALLOWED_WHISPER_LANGS:
        raise ToolError(ctx.M("کد زبان گفتار نامعتبر است.", "Invalid spoken language code."))
    engine = _load_whisper(ctx)
    fmt = str(v.get("fmt") or "srt")
    language = None if language in ("", "auto") else language

    work_dir = out_dir(ctx, v.get("out_dir"))
    audio_path = src
    temporary: List[Path] = []
    if src.suffix.lower() in VIDEO_SUFFIXES:
        ctx.progress(1, 100, ctx.M("جدا کردن صدا از ویدیو", "Extracting the audio track"))
        audio_path = work_dir / f"{src.stem}_audio.wav"
        _run_ffmpeg(ctx, ["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                          str(audio_path)], _duration(src),
                    ctx.M("استخراج صدا…", "Extracting audio…"))
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            raise ToolError(ctx.M("این فایل صدا ندارد یا صدا خوانده نشد.",
                                  "This file has no audio track, or it could not be read."))
        temporary.append(audio_path)

    ctx.progress(5, 100, ctx.M(f"آماده‌سازی مدل {model_name}", f"Loading the {model_name} model"))
    segments: List[Tuple[float, float, str]] = []
    detected = language or "?"
    try:
        if engine == "faster":
            from faster_whisper import WhisperModel

            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            iterator, info = model.transcribe(str(audio_path), language=language,
                                              vad_filter=True, beam_size=5)
            detected = getattr(info, "language", detected) or detected
            for item in iterator:
                segments.append((float(item.start), float(item.end), (item.text or "").strip()))
                if item.end:
                    ctx.progress(min(95, 5 + int(90 * float(item.end) /
                                                max(1.0, getattr(info, "duration", 1.0) or 1.0))),
                                 100, ctx.M("رونویسی…", "Transcribing…"))
        else:
            import whisper

            model = whisper.load_model(model_name)
            result = model.transcribe(str(audio_path), language=language, fp16=False)
            detected = result.get("language", detected) or detected
            for item in result.get("segments") or []:
                segments.append((float(item.get("start") or 0), float(item.get("end") or 0),
                                 (item.get("text") or "").strip()))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(ctx.M(
            f"رونویسی انجام نشد: {exc}\n"
            "اگر مدل دانلود نشده یا فضای کافی نیست، مدل کوچک‌تری (tiny/base) را امتحان کنید.",
            f"Transcription failed: {exc}\n"
            "If the model could not be downloaded or there is no space, try a smaller one (tiny/base).",
        ))
    finally:
        for path in temporary:
            try:
                path.unlink()
            except OSError:
                pass

    segments = [item for item in segments if item[2]]
    if not segments:
        raise ToolError(ctx.M("در این فایل گفتاری تشخیص داده نشد.",
                              "No speech was detected in this file."))

    plain = " ".join(item[2] for item in segments).strip()
    outputs: List[str] = []
    if fmt in ("srt", "both"):
        srt_path = unique_path(work_dir / f"{src.stem}.srt")
        lines: List[str] = []
        for index, (start, end, text) in enumerate(segments, start=1):
            lines.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
        srt_path.write_text("\n".join(lines), encoding="utf-8")
        outputs.append(str(srt_path))
    if fmt in ("txt", "both"):
        txt_path = unique_path(work_dir / f"{src.stem}.txt")
        txt_path.write_text(plain + "\n", encoding="utf-8")
        outputs.append(str(txt_path))

    minutes = sum(max(0.0, end - start) for start, end, _text in segments) / 60.0
    message = ctx.M(
        f"{len(segments)} قطعه · {len(plain)} نویسه · زبان {detected} · مدل {model_name}",
        f"{len(segments)} segment(s) · {len(plain)} characters · language {detected} · model {model_name}",
    )
    return RunResult(message=message, details=plain[:3000], outputs=outputs,
                     open_target=str(work_dir))
