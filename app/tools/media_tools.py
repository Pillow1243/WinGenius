"""Media toolkit powered by ffmpeg (system ffmpeg or bundled imageio-ffmpeg)."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILES, K_FILE, K_INT, K_SAVE, K_TEXT,
    Field, RunContext, RunResult, ToolError,
)
from ..core.utils import human_size, unique_path
from ._helpers import (
    as_path_list, ensure_dir, first_file, has_ffmpeg, has_ffprobe, out_dir, save_target,
)

MEDIA_FILTER = ("مدیا (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.mp3 *.wav *.m4a *.aac "
                "*.flac *.ogg *.opus);;همه فایل‌ها (*)")


def _ffmpeg(ctx: RunContext) -> str:
    exe = has_ffmpeg()
    if not exe:
        raise ToolError(ctx.M(
            "ffmpeg پیدا نشد.\nیا آن را نصب کنید (winget install Gyan.FFmpeg) یا بسته‌ی پایتونی را نصب کنید:\n"
            "pip install imageio-ffmpeg",
            "ffmpeg not found.\nInstall it (winget install Gyan.FFmpeg) or run: pip install imageio-ffmpeg",
        ))
    return exe


def _run_ffmpeg(ctx: RunContext, args: List[str], duration: float = 0.0, label: str = "") -> None:
    exe = _ffmpeg(ctx)
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", *args]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace",
            creationflags=(0x08000000 if sys.platform.startswith("win") else 0),
        )
    except FileNotFoundError:
        raise ToolError(ctx.M("ffmpeg اجرا نشد.", "Could not run ffmpeg."))
    errors: List[str] = []

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        try:
            while data := proc.stderr.read(4096):
                errors.append(data)
                if len(errors) > 2:
                    del errors[:-2]
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, name="wg-ffmpeg-stderr", daemon=True)
    stderr_thread.start()
    from ..core.utils import CancellableProcess

    cancellation = CancellableProcess(proc, ctx).start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    seconds = int(line.split("=")[1]) / 1_000_000
                    pct = max(0.0, min(1.0, seconds / duration))
                    ctx.progress(int(pct * 1000), 1000, label)
                except ValueError:
                    pass
            elif line.startswith("speed=") and label:
                ctx.log(f"{label} {line}")
            if ctx.cancelled:
                ctx.check_cancel()
        proc.wait(timeout=30)
        cancellation.close()
        cancellation.check_cancelled()
        stderr_thread.join(timeout=5)
    finally:
        cancellation.close()
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        stderr_thread.join(timeout=2)
    if proc.returncode != 0:
        detail = "".join(errors).strip()[-1200:]
        raise ToolError(ctx.M(f"ffmpeg ناموفق بود:\n{detail}", f"ffmpeg failed:\n{detail}"))


def _duration(path: Path) -> float:
    probe = has_ffprobe()
    if probe:
        try:
            out = subprocess.run(
                [probe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(out.stdout or "{}")
            return float(data.get("format", {}).get("duration") or 0)
        except Exception:
            return 0.0
    return 0.0


def _stream_kinds(path: Path) -> set:
    """Which stream kinds a container actually has ({"video", "audio"}).

    ffmpeg happily starts an extraction and *then* dies with
    "Output file does not contain any stream", which is a terrible message to show
    a user who simply picked a silent screen recording.  Asking first lets us say
    what is wrong in one line.
    """
    probe = has_ffprobe()
    if probe:
        try:
            out = subprocess.run([probe, "-v", "quiet", "-print_format", "json",
                                  "-show_streams", str(path)],
                                 capture_output=True, text=True, timeout=30)
            data = json.loads(out.stdout or "{}")
            kinds = {str(item.get("codec_type") or "") for item in data.get("streams", [])}
            if kinds:
                return kinds
        except Exception:
            pass
    exe = has_ffmpeg()
    if not exe:
        return set()
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=30,
                             encoding="utf-8", errors="replace")
        text = (out.stderr or "") + (out.stdout or "")
    except Exception:
        return set()
    kinds = set()
    if "Audio:" in text:
        kinds.add("audio")
    if "Video:" in text:
        kinds.add("video")
    return kinds


def _require_stream(ctx: RunContext, path: Path, need: str) -> None:
    kinds = _stream_kinds(path)
    if not kinds or need in kinds:
        return
    if need == "audio":
        raise ToolError(ctx.M("این فایل هیچ باند صوتی ندارد؛ چیزی برای استخراج نیست.",
                              "This file has no audio track — nothing to extract."))
    raise ToolError(ctx.M("این فایل هیچ باند تصویری ندارد؛ فقط صدا است.",
                          "This file has no video track — it is audio only."))


def _output(ctx: RunContext, v: Dict[str, Any], src: Path, suffix: str, ext: str) -> Path:
    folder = str(v.get("out_dir") or "").strip()
    target_dir = ensure_dir(folder) if folder else out_dir(ctx)
    return unique_path(target_dir / f"{src.stem}{suffix}.{ext.lstrip('.')}")


# ------------------------------------------------------------------ info
@tool(
    id="media_info", category="media", name="اطلاعات مدیا", name_en="Media info",
    desc="مدت، رزولوشن، کدک، بیت‌ریت و حجم فایل صوتی/تصویری را نشان می‌دهد.",
    desc_en="Show duration, resolution, codecs, bitrate and size.",
    icon="list", order=10, keywords=("media info", "ffprobe", "مشخصات ویدیو", "کدک"),
    fields=[Field("src", K_FILES, "فایل‌ها", "Files", required=True, filt=MEDIA_FILTER, multiple=True)],
)
def media_info(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = [Path(p) for p in as_path_list(v.get("src"))]
    rows: List[str] = []
    for index, path in enumerate(files):
        ctx.progress(index, len(files), path.name)
        probe = has_ffprobe()
        if probe:
            out = subprocess.run(
                [probe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            try:
                data = json.loads(out.stdout or "{}")
            except json.JSONDecodeError:
                data = {}
            fmt = data.get("format", {})
            rows.append(f"▶ {path.name}  —  {human_size(path.stat().st_size)}")
            duration = float(fmt.get("duration") or 0)
            rows.append(f"   {ctx.M('مدت', 'duration')}: {int(duration // 60)}:{int(duration % 60):02d}")
            for stream in data.get("streams", []):
                kind = stream.get("codec_type")
                if kind == "video":
                    rows.append(
                        f"   video: {stream.get('codec_name')} {stream.get('width')}×{stream.get('height')}"
                        f" @ {stream.get('avg_frame_rate')} fps"
                    )
                elif kind == "audio":
                    rows.append(
                        f"   audio: {stream.get('codec_name')} {stream.get('sample_rate')} Hz"
                        f" {stream.get('channels')} ch"
                    )
        else:
            rows.append(f"▶ {path.name} — {human_size(path.stat().st_size)} ({ctx.M('ffprobe موجود نیست', 'no ffprobe')})")
    return RunResult(message=ctx.M("اطلاعات مدیا", "Media info"), details="\n".join(rows))


# ---------------------------------------------------------------- video → audio
@tool(
    id="video_to_audio", category="media", name="ویدیو به صدا", name_en="Video → Audio",
    desc="صدای فیلم را به MP3/WAV/M4A/FLAC استخراج می‌کند.",
    desc_en="Extract the audio track of a video to MP3/WAV/M4A/FLAC.",
    icon="music", order=20, keywords=("extract audio", "mp4 to mp3", "استخراج صدا", "ویدیو به صدا"),
    fields=[
        Field("src", K_FILES, "فایل‌های ویدیو", "Video files", required=True, filt=MEDIA_FILTER, multiple=True),
        Field("fmt", K_CHOICE, "فرمت صوتی", "Audio format", default="mp3",
              choices=[("mp3", "MP3", "MP3"), ("wav", "WAV", "WAV"), ("m4a", "M4A", "M4A"),
                       ("flac", "FLAC", "FLAC"), ("ogg", "OGG", "OGG")]),
        Field("bitrate", K_CHOICE, "کیفیت", "Quality", default="192k",
              choices=[("128k", "128 kbps", "128 kbps"), ("192k", "192 kbps", "192 kbps"),
                       ("256k", "256 kbps", "256 kbps"), ("320k", "320 kbps", "320 kbps")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def video_to_audio(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    fmt = str(v.get("fmt") or "mp3")
    outputs: List[str] = []
    for index, path in enumerate(files):
        if not path.exists():
            raise ToolError(ctx.M(f"فایل پیدا نشد: {path}", f"File not found: {path}"))
        ctx.progress(index, len(files), path.name)
        _require_stream(ctx, path, "audio")
        dest = _output(ctx, v, path, "", fmt)
        args = ["-y", "-i", str(path), "-vn"]
        if fmt == "mp3":
            args += ["-c:a", "libmp3lame", "-b:a", str(v.get("bitrate") or "192k")]
        elif fmt == "wav":
            args += ["-c:a", "pcm_s16le"]
        elif fmt == "flac":
            args += ["-c:a", "flac"]
        elif fmt == "ogg":
            args += ["-c:a", "libvorbis", "-b:a", str(v.get("bitrate") or "192k")]
        else:
            args += ["-c:a", "aac", "-b:a", str(v.get("bitrate") or "192k")]
        args.append(str(dest))
        _run_ffmpeg(ctx, args, _duration(path), ctx.M("در حال استخراج صدا…", "Extracting audio…"))
        outputs.append(str(dest))
    return RunResult(message=ctx.M(f"{len(outputs)} فایل صوتی ساخته شد.", f"{len(outputs)} audio file(s) created."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


# ------------------------------------------------------------------ video → gif
@tool(
    id="video_to_gif", category="media", name="ویدیو به GIF", name_en="Video → GIF",
    desc="از یک بخش از فیلم، فایل GIF متحرک می‌سازد.",
    desc_en="Create an animated GIF from a video clip.",
    icon="film", order=30, keywords=("video to gif", "gif", "فیلم به گیف"),
    fields=[
        Field("src", K_FILE, "فایل ویدیو", "Video file", required=True, filt=MEDIA_FILTER),
        Field("start", K_TEXT, "شروع (ثانیه)", "Start (seconds)", default="0"),
        Field("duration", K_INT, "مدت (ثانیه)", "Duration (seconds)", default=5, minimum=1, maximum=120),
        Field("fps", K_INT, "فریم بر ثانیه", "Frames per second", default=12, minimum=5, maximum=30),
        Field("width", K_INT, "عرض (px)", "Width (px)", default=480, minimum=120, maximum=1280),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="GIF (*.gif)"),
    ],
)
def video_to_gif(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    _require_stream(ctx, src, "video")
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}.gif")
    start = str(v.get("start") or "0")
    duration = int(v.get("duration") or 5)
    fps = int(v.get("fps") or 12)
    width = int(v.get("width") or 480)
    vf = f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    args = ["-y", "-ss", start, "-t", str(duration), "-i", str(src),
            "-vf", vf, "-loop", "0", str(dest)]
    _run_ffmpeg(ctx, args, duration, ctx.M("ساخت GIF…", "Building GIF…"))
    if not dest.exists():
        raise ToolError(ctx.M("ساخت GIF ناموفق بود.", "GIF creation failed."))
    return RunResult(
        message=ctx.M(f"GIF ساخته شد ({human_size(dest.stat().st_size)}).",
                      f"GIF created ({human_size(dest.stat().st_size)})."),
        outputs=[str(dest)], open_target=str(dest),
    )


# ------------------------------------------------------------------- convert
@tool(
    id="media_convert", category="media", name="تبدیل مدیا", name_en="Convert media",
    desc="ویدیو/صدا را به فرمت دیگری تبدیل می‌کند (MP4، MKV، WEBM، MP3، WAV…).",
    desc_en="Convert video/audio to another format (MP4, MKV, WEBM, MP3, WAV…).",
    icon="convert", order=40, keywords=("convert video", "mp4 to mkv", "تبدیل ویدیو", "فرمت فیلم"),
    fields=[
        Field("src", K_FILES, "فایل‌ها", "Files", required=True, filt=MEDIA_FILTER, multiple=True),
        Field("fmt", K_CHOICE, "فرمت خروجی", "Output format", default="mp4",
              choices=[("mp4", "MP4 (H.264)", "MP4 (H.264)"), ("mkv", "MKV", "MKV"),
                       ("webm", "WEBM (VP9)", "WEBM (VP9)"), ("avi", "AVI", "AVI"),
                       ("mp3", "MP3 (صدا)", "MP3 (audio)"), ("wav", "WAV (صدا)", "WAV (audio)"),
                       ("m4a", "M4A (صدا)", "M4A (audio)")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def media_convert(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    fmt = str(v.get("fmt") or "mp4")
    outputs: List[str] = []
    for index, path in enumerate(files):
        if not path.exists():
            raise ToolError(ctx.M(f"فایل پیدا نشد: {path}", f"File not found: {path}"))
        ctx.progress(index, len(files), path.name)
        dest = _output(ctx, v, path, "", fmt)
        args = ["-y", "-i", str(path)]
        if fmt in ("mp3", "wav", "m4a"):
            args += ["-vn"]
            if fmt == "mp3":
                args += ["-c:a", "libmp3lame", "-b:a", "192k"]
            elif fmt == "wav":
                args += ["-c:a", "pcm_s16le"]
            else:
                args += ["-c:a", "aac", "-b:a", "192k"]
        elif fmt == "webm":
            args += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"]
        elif fmt == "mp4":
            args += ["-c:v", "libx264", "-preset", "medium", "-crf", "22", "-c:a", "aac", "-b:a", "160k",
                     "-movflags", "+faststart"]
        else:
            args += ["-c:v", "copy", "-c:a", "copy"]
        args.append(str(dest))
        _run_ffmpeg(ctx, args, _duration(path), ctx.M("در حال تبدیل…", "Converting…"))
        outputs.append(str(dest))
    return RunResult(message=ctx.M(f"{len(outputs)} فایل تبدیل شد.", f"{len(outputs)} file(s) converted."),
                     outputs=outputs, open_target=str(Path(outputs[0]).parent))


# ------------------------------------------------------------------ compress
@tool(
    id="video_compress", category="media", name="فشرده‌سازی ویدیو", name_en="Compress video",
    desc="حجم ویدیو را با تغییر CRF و رزولوشن کم می‌کند.",
    desc_en="Shrink a video by tuning CRF and resolution.",
    icon="zip", order=50, keywords=("compress video", "کم کردن حجم فیلم", "فشرده ویدیو"),
    fields=[
        Field("src", K_FILES, "ویدیوها", "Videos", required=True, filt=MEDIA_FILTER, multiple=True),
        Field("quality", K_CHOICE, "سطح فشرده‌سازی", "Compression level", default="balanced",
              choices=[("light", "سبک (کیفیت بالا)", "Light (high quality)"),
                       ("balanced", "متعادل", "Balanced"),
                       ("strong", "قوی (حجم کم)", "Strong (small)")]),
        Field("height", K_CHOICE, "رزولوشن", "Resolution", default="keep",
              choices=[("keep", "بدون تغییر", "Keep"), (1080, "1080p", "1080p"),
                       (720, "720p", "720p"), (480, "480p", "480p")]),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def video_compress(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    files = [Path(p) for p in as_path_list(v.get("src"))]
    if not files:
        raise ToolError(ctx.M("فایلی انتخاب نشد.", "No file selected."))
    crf_map = {"light": "20", "balanced": "26", "strong": "32"}
    crf = crf_map.get(str(v.get("quality") or "balanced"), "26")
    outputs: List[str] = []
    kept: List[str] = []
    before = 0
    for index, path in enumerate(files):
        if not path.exists():
            raise ToolError(ctx.M(f"فایل پیدا نشد: {path}", f"File not found: {path}"))
        before += path.stat().st_size
        ctx.progress(index, len(files), path.name)
        dest = _output(ctx, v, path, "_small", "mp4")
        args = ["-y", "-i", str(path), "-c:v", "libx264", "-preset", "medium", "-crf", crf,
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
        height = v.get("height")
        if str(height).isdigit() and int(height) > 0:
            args += ["-vf", f"scale=-2:{int(height)}"]
        args.append(str(dest))
        _run_ffmpeg(ctx, args, _duration(path), ctx.M("فشرده‌سازی…", "Compressing…"))
        # A short or already-optimised clip can come out *bigger* after a re-encode.
        # Handing that back as "compressed" is worse than useless, so keep the
        # original and say so.
        if dest.exists() and dest.stat().st_size >= path.stat().st_size:
            try:
                dest.unlink()
            except OSError:
                pass
            kept.append(path.name)
            outputs.append(str(path))
        else:
            outputs.append(str(dest))
    after = sum(Path(p).stat().st_size for p in outputs if Path(p).exists())
    saved = 100.0 * (1 - after / before) if before else 0
    message = ctx.M(
        f"حجم: {human_size(before)} → {human_size(after)} ({saved:.0f}٪ کاهش)",
        f"Size: {human_size(before)} → {human_size(after)} ({saved:.0f}% smaller)",
    )
    if kept:
        message += ctx.M(f" · {len(kept)} فایل از قبل کم‌حجم بود و دست‌نخورده ماند",
                         f" · {len(kept)} file(s) were already compact and left untouched")
    return RunResult(
        message=message,
        outputs=outputs, open_target=str(Path(outputs[0]).parent),
    )


# --------------------------------------------------------------------- trim
@tool(
    id="video_trim", category="media", name="برش ویدیو", name_en="Trim video",
    desc="بخشی از ویدیو را بدون افت کیفیت جدا می‌کند.",
    desc_en="Cut a section out of a video (stream copy, no quality loss).",
    icon="crop", order=60, keywords=("trim video", "برش فیلم", "کات کردن"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("start", K_TEXT, "شروع", "Start", default="00:00:00",
              help="مثال: 00:01:30 یا 90 (ثانیه)", help_en="e.g. 00:01:30 or 90"),
        Field("duration", K_TEXT, "مدت (خالی = تا پایان)", "Duration (empty = to end)", default="00:00:30"),
        Field("out", K_SAVE, "ذخیره در", "Save as", filt="Video (*.mp4);;همه فایل‌ها (*)"),
    ],
)
def video_trim(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    dest = save_target(ctx, v.get("out", ""), f"{src.stem}_trim.mp4")
    args = ["-y", "-ss", str(v.get("start") or "0"), "-i", str(src)]
    duration_text = str(v.get("duration") or "").strip()
    duration = 0.0
    if duration_text:
        args += ["-t", duration_text]
        duration = _duration(src)
    args += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(dest)]
    _run_ffmpeg(ctx, args, duration, ctx.M("در حال برش…", "Trimming…"))
    return RunResult(message=ctx.M("ویدیو برش خورد.", "Video trimmed."),
                     outputs=[str(dest)], open_target=str(dest))


# ------------------------------------------------------------- extract frames
@tool(
    id="video_frames", category="media", name="گرفتن فریم از ویدیو", name_en="Extract frames",
    desc="از ویدیو عکس می‌گیرد (هر N ثانیه یا یک فریم در زمان مشخص).",
    desc_en="Grab still frames from a video (every N seconds or at a timestamp).",
    icon="camera", order=70, keywords=("extract frames", "عکس از فیلم", "فریم", "thumbnail video"),
    fields=[
        Field("src", K_FILE, "ویدیو", "Video", required=True, filt=MEDIA_FILTER),
        Field("mode", K_CHOICE, "حالت", "Mode", default="every",
              choices=[("every", "هر N ثانیه", "Every N seconds"), ("single", "یک فریم در زمان مشخص", "Single frame at time")]),
        Field("interval", K_INT, "فاصله (ثانیه)", "Interval (seconds)", default=5, minimum=1, maximum=3600,
              visible_when=("mode", "every")),
        Field("at", K_TEXT, "زمان", "Timestamp", default="00:00:05",
              visible_when=("mode", "single")),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def video_frames(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    _ffmpeg(ctx)
    src = first_file(v, "src", ctx)
    _require_stream(ctx, src, "video")
    folder = str(v.get("out_dir") or "").strip()
    target_dir = ensure_dir(folder) if folder else ensure_dir(out_dir(ctx) / f"{src.stem}_frames")
    if str(v.get("mode") or "every") == "single":
        dest = unique_path(target_dir / f"{src.stem}_frame.jpg")
        _run_ffmpeg(ctx, ["-y", "-ss", str(v.get("at") or "0"), "-i", str(src), "-frames:v", "1",
                          "-q:v", "2", str(dest)], 0, ctx.M("گرفتن فریم…", "Grabbing frame…"))
        if not dest.exists():
            raise ToolError(ctx.M("فریمی گرفته نشد؛ زمان را بررسی کنید.", "No frame captured; check the timestamp."))
        return RunResult(message=ctx.M("فریم ذخیره شد.", "Frame saved."), outputs=[str(dest)], open_target=str(dest))
    interval = max(1, int(v.get("interval") or 5))
    pattern = target_dir / f"{src.stem}_%04d.jpg"
    _run_ffmpeg(ctx, ["-y", "-i", str(src), "-vf", f"fps=1/{interval}", "-q:v", "2", str(pattern)],
                _duration(src), ctx.M("در حال استخراج فریم‌ها…", "Extracting frames…"))
    files = sorted(target_dir.glob(f"{src.stem}_*.jpg"))
    return RunResult(message=ctx.M(f"{len(files)} فریم ذخیره شد.", f"{len(files)} frame(s) saved."),
                     outputs=[str(p) for p in files], open_target=str(target_dir))
