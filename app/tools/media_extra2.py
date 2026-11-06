"""Media extras: subtitle rewriting, waveform images and metadata scrubbing."""
from __future__ import annotations

import re
import subprocess
import time
from array import array
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.registry import tool
from ..core.spec import (
    K_CHOICE, K_FILE, K_INT, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir, human_size, unique_path
from ._helpers import first_file, has_ffmpeg, out_dir, read_text_file, save_target

TIMESTAMP = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})")
SUBTITLE_FILTER = "Subtitles (*.srt *.vtt *.txt)"


# --------------------------------------------------------------------- subtitles
def _to_ms(stamp: str) -> Optional[int]:
    match = TIMESTAMP.search(stamp)
    if not match:
        return None
    parts = match.groupdict()
    milliseconds = int(parts["ms"].ljust(3, "0")[:3])
    return (int(parts["h"]) * 3600 + int(parts["m"]) * 60 + int(parts["s"])) * 1000 + milliseconds


def _stamp(ms: int, vtt: bool = False) -> str:
    ms = max(0, ms)
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def parse_subtitles(text: str) -> List[Tuple[int, int, str]]:
    """Parse SRT or WebVTT into (start_ms, end_ms, text) cues."""
    cues: List[Tuple[int, int, str]] = []
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", body):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        if "-->" in lines[0] and len(lines) > 1:
            timing, text_lines = lines[0], lines[1:]
        elif len(lines) > 2 and "-->" in lines[1]:
            timing, text_lines = lines[1], lines[2:]
        else:
            continue
        left, _, right = timing.partition("-->")
        start = _to_ms(left)
        end = _to_ms(right)
        if start is None or end is None:
            continue
        cues.append((start, end, "\n".join(text_lines).strip()))
    return cues


def render_subtitles(cues: List[Tuple[int, int, str]], fmt: str, strip_tags: bool = True) -> str:
    blocks: List[str] = []
    if fmt == "vtt":
        blocks.append("WEBVTT\n")
    for index, (start, end, text) in enumerate(cues, start=1):
        if strip_tags:
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\{\\[^}]*\}", "", text)
        if fmt == "txt":
            blocks.append(text)
            continue
        blocks.append(f"{index}\n{_stamp(start, fmt == 'vtt')} --> "
                      f"{_stamp(end, fmt == 'vtt')}\n{text}\n")
    return "\n".join(blocks) if fmt != "txt" else "\n".join(blocks) + "\n"


@tool(
    id="subtitle_convert", category="media", name="تبدیل و جابه‌جایی زیرنویس",
    name_en="Convert & shift subtitles",
    desc="SRT و VTT را به هم تبدیل می‌کند، زمان‌ها را جلو/عقب می‌برد و تگ‌های اضافه را پاک می‌کند.",
    desc_en="Converts between SRT and VTT, shifts every timestamp and strips styling tags.",
    icon="text", order=40,
    keywords=("subtitle", "srt", "vtt", "shift", "sync", "زیرنویس", "srt", "زمان", "همگام"),
    fields=[
        Field("src", K_FILE, "فایل زیرنویس", "Subtitle file", required=True, filt=SUBTITLE_FILTER),
        Field("target", K_CHOICE, "خروجی", "Output format", default="srt",
              choices=[("srt", "SRT", "SRT"), ("vtt", "WebVTT", "WebVTT"),
                       ("txt", "فقط متن", "Plain text")]),
        Field("shift", K_TEXT, "جابه‌جایی زمانی", "Time shift",
              placeholder="+2.5s یا -1200ms",
              help="مثبت یعنی زیرنویس دیرتر، منفی یعنی زودتر. واحد: ms یا s یا بدون واحد (ثانیه).",
              help_en="Positive delays the subtitles, negative advances them. Units: ms, s or none (seconds)."),
        Field("strip_tags", K_INT, "پاک‌کردن تگ‌های ایتالیک", "Strip style tags", default=1,
              minimum=0, maximum=1),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def subtitle_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    source = first_file(v, "src", ctx)
    text = read_text_file(source, ctx, encoding="utf-8")
    cues = parse_subtitles(text)
    if not cues:
        raise ToolError(ctx.M(
            "هیچ زیرنویس معتبری در این فایل پیدا نشد (SRT یا VTT باشد).",
            "No valid cue was found in this file (it must be SRT or WebVTT)."))
    shift_text = str(v.get("shift") or "").strip().replace(" ", "")
    shift_ms = 0
    if shift_text:
        try:
            number = shift_text.replace("+", "")
            if number.lower().endswith("ms"):
                shift_ms = int(float(number[:-2]))
            elif number.lower().endswith("s"):
                shift_ms = int(float(number[:-1]) * 1000)
            else:
                shift_ms = int(float(number) * 1000)
        except ValueError:
            raise ToolError(ctx.M(f"جابه‌جایی زمانی نامفهوم است: {shift_text}",
                                  f"Cannot understand the time shift: {shift_text}"))
    shifted = [(max(0, start + shift_ms), max(0, end + shift_ms), body) for start, end, body in cues]
    fmt = str(v.get("target") or "srt")
    rendered = render_subtitles(shifted, fmt, bool(int(v.get("strip_tags") or 0)))
    target = save_target(ctx, str(v.get("out") or "").strip(),
                         f"{source.stem}{shift_ms:+d}ms".replace("+0ms", "") + f".{fmt}")
    target.write_text(rendered, encoding="utf-8")
    longest = max((end - start for start, end, _ in shifted), default=0)
    return RunResult(
        message=ctx.M(
            f"{len(shifted)} زیرنویس به {fmt.upper()} تبدیل شد"
            + (f" و {shift_ms:+d} میلی‌ثانیه جابه‌جا شد." if shift_ms else "."),
            f"{len(shifted)} cue(s) written as {fmt.upper()}"
            + (f", shifted by {shift_ms:+d} ms." if shift_ms else ".")),
        details=ctx.M(f"بلندترین زیرنویس: {longest / 1000:.2f} ثانیه\n{target}",
                      f"Longest cue: {longest / 1000:.2f}s\n{target}"),
        outputs=[str(target)], open_target=str(target))


# --------------------------------------------------------------------- waveform
@tool(
    id="audio_waveform", category="media", name="تصویر موج صدا",
    name_en="Audio waveform image",
    desc="از هر فایل صوتی یا ویدیویی یک تصویر موج صدا (PNG) می‌سازد — مناسب کاور و پادکست.",
    desc_en="Draws a waveform PNG from any audio or video file — handy for covers and podcast art.",
    icon="music", order=41,
    keywords=("waveform", "audio", "wave", "cover", "صدا", "موج", "نمودار", "پادکست"),
    fields=[
        Field("src", K_FILE, "فایل صوتی/ویدیویی", "Audio/video file", required=True),
        Field("width", K_INT, "عرض", "Width", default=1200, minimum=200, maximum=4000),
        Field("height", K_INT, "ارتفاع", "Height", default=360, minimum=100, maximum=2000),
        Field("color", K_TEXT, "رنگ (کد هگز)", "Colour (hex)", default="#00D9A6"),
        Field("out", K_SAVE, "ذخیره‌ی PNG", "Save PNG", filt="PNG (*.png)"),
    ],
)
def audio_waveform(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    ffmpeg = has_ffmpeg()
    if not ffmpeg:
        raise ToolError(ctx.M("ffmpeg پیدا نشد؛ imageio-ffmpeg را نصب کنید.",
                              "ffmpeg was not found; install imageio-ffmpeg."))
    try:
        from PIL import Image, ImageDraw
    except Exception:
        raise ToolError(ctx.M("Pillow لازم است: pip install Pillow",
                              "Pillow is required: pip install Pillow"))
    source = first_file(v, "src", ctx)
    width = max(200, int(v.get("width") or 1200))
    height = max(100, int(v.get("height") or 360))
    color = str(v.get("color") or "#00D9A6").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#00D9A6"
    rate = 8000
    command = [ffmpeg, "-v", "error", "-nostdin", "-i", str(source), "-ac", "1", "-ar", str(rate),
               "-f", "s16le", "-"]
    ctx.progress(0, 2, "رمزیابی صدا")
    peaks: List[int] = []
    total_samples = 0
    leftover = b""
    stride = 1
    current_peak = 0
    in_bin = 0
    max_bins = max(4096, width * 2)
    err_chunks: List[bytes] = []
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=(0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
    except OSError as exc:
        raise ToolError(ctx.M(f"اجرای ffmpeg ناموفق بود: {exc}", f"Could not start ffmpeg: {exc}"))

    import threading

    def _drain_err() -> None:
        if proc.stderr is None:
            return
        try:
            while data := proc.stderr.read(4096):
                err_chunks.append(data)
                if len(err_chunks) > 2:
                    del err_chunks[:-2]
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain_err, daemon=True)
    err_thread.start()
    from ..core.utils import CancellableProcess

    cancellation = CancellableProcess(proc, ctx).start()
    started = time.monotonic() if "time" in globals() else 0.0
    try:
        assert proc.stdout is not None
        while True:
            ctx.check_cancel()
            raw = proc.stdout.read(65536)
            if not raw:
                break
            buf = leftover + raw
            even = len(buf) - (len(buf) % 2)
            leftover = buf[even:]
            if not even:
                continue
            chunk_samples = array("h")
            chunk_samples.frombytes(buf[:even])
            total_samples += len(chunk_samples)
            for s in chunk_samples:
                val = -s if s < 0 else s
                if val > current_peak:
                    current_peak = val
                in_bin += 1
                if in_bin >= stride:
                    peaks.append(current_peak)
                    current_peak = 0
                    in_bin = 0
                    if len(peaks) >= max_bins * 2:
                        peaks = [
                            max(peaks[i], peaks[i + 1])
                            for i in range(0, len(peaks) - 1, 2)
                        ]
                        stride *= 2
            if started and (time.monotonic() - started) > 180:
                proc.kill()
                raise ToolError(ctx.M("خواندن صدا طول کشید؛ فایل کوتاه‌تری امتحان کنید.",
                                      "Reading the audio took too long; try a shorter file."))
        cancellation.close()
        cancellation.check_cancelled()
        if in_bin > 0:
            peaks.append(current_peak)
        proc.wait(timeout=15)
        err_thread.join(timeout=2)
    finally:
        cancellation.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        err_thread.join(timeout=2)
    if proc.returncode != 0 or not peaks or total_samples == 0:
        message = b"".join(err_chunks).decode("utf-8", "replace").strip()[:300]
        if proc.returncode != 0:
            raise ToolError(ctx.M(f"صدا خوانده نشد: {message or 'بدون خروجی'}",
                                  f"Could not decode the audio: {message or 'no output'}"))
        raise ToolError(ctx.M("این فایل صدایی ندارد.", "This file contains no audio."))
    ctx.progress(1, 2, "رسم موج")
    image = Image.new("RGB", (width, height), "#0B1020")
    draw = ImageDraw.Draw(image)
    middle = height / 2
    n_peaks = len(peaks)
    for column in range(width):
        lo = (column * n_peaks) // width
        hi = max(lo + 1, ((column + 1) * n_peaks) // width)
        bucket = peaks[lo:hi]
        if not bucket:
            break
        peak = max(bucket) / 32768.0
        amplitude = min(1.0, peak) * (height / 2 - 4)
        draw.line([(column, middle - amplitude), (column, middle + amplitude)],
                  fill=color, width=1)
    draw.line([(0, middle), (width, middle)], fill="#33405c", width=1)
    seconds = total_samples / rate
    target = save_target(ctx, str(v.get("out") or "").strip(), f"{source.stem}_waveform.png")
    if target.suffix.lower() != ".png":
        target = target.with_suffix(".png")
    image.save(target, "PNG")
    ctx.progress(2, 2, "ذخیره شد")
    return RunResult(
        message=ctx.M(f"موج صدا ساخته شد ({seconds:.1f} ثانیه صدا).",
                      f"Waveform written ({seconds:.1f}s of audio)."),
        details=ctx.M(f"{width}×{height} پیکسل → {target}", f"{width}×{height} px → {target}"),
        outputs=[str(target)], open_target=str(target))


# ------------------------------------------------------------- metadata scrub
@tool(
    id="media_strip_metadata", category="media", name="پاک‌کردن فراداده‌ی ویدیو و صدا",
    name_en="Strip media metadata",
    desc="نام دستگاه، GPS، تاریخ و تگ‌های ویدیو/صدا را بدون افت کیفیت (بدون رمزگذاری دوباره) حذف می‌کند.",
    desc_en="Removes device names, GPS, dates and tags from video/audio without re-encoding anything.",
    icon="broom", order=42,
    keywords=("metadata", "privacy", "exif", "video", "audio", "فراداده", "حذف", "حریم خصوصی"),
    fields=[
        Field("src", K_FILE, "فایل ورودی", "Input file", required=True),
        Field("out", K_SAVE, "ذخیره در", "Save to"),
    ],
)
def media_strip_metadata(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    ffmpeg = has_ffmpeg()
    if not ffmpeg:
        raise ToolError(ctx.M("ffmpeg پیدا نشد؛ imageio-ffmpeg را نصب کنید.",
                              "ffmpeg was not found; install imageio-ffmpeg."))
    source = first_file(v, "src", ctx)
    requested = str(v.get("out") or "").strip()
    default_name = f"{source.stem}_clean{source.suffix or '.mp4'}"
    target = save_target(ctx, requested, default_name) if requested else unique_path(out_dir(ctx, "media") / default_name)
    ensure_dir(target.parent)
    command = [ffmpeg, "-v", "error", "-y", "-i", str(source), "-map_metadata", "-1",
               "-map_chapters", "-1", "-c", "copy", str(target)]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        raise ToolError(ctx.M("این فایل طول کشید؛ فایل کوچک‌تری امتحان کنید.",
                              "This file took too long; try a smaller one."))
    if proc.returncode != 0 or not target.exists():
        message = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        raise ToolError(ctx.M(f"حذف فراداده ناموفق بود: {message or 'بدون خروجی'}",
                              f"Stripping metadata failed: {message or 'no output'}"))
    before = source.stat().st_size
    after = target.stat().st_size
    return RunResult(
        message=ctx.M(
            f"فراداده پاک شد ({human_size(before)} → {human_size(after)}، بدون رمزگذاری دوباره).",
            f"Metadata removed ({human_size(before)} → {human_size(after)}, no re-encode)."),
        outputs=[str(target)], open_target=str(target))
