"""File-type intelligence: which tools make sense for a dropped file?

Used by the drag & drop overlay and by `--run auto`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

EXT_TOOLS: Dict[str, Tuple[str, ...]] = {
    ".pdf": ("pdf_to_images", "pdf_to_text", "pdf_compress", "pdf_merge", "pdf_to_docx",
             "pdf_split", "pdf_encrypt", "pdf_rotate", "pdf_watermark", "pdf_extract_pages"),
    ".png": ("image_compress", "image_convert", "image_resize", "images_to_pdf", "image_watermark",
             "image_to_ico", "image_ocr", "image_thumbnail"),
    ".jpg": ("image_compress", "image_convert", "image_resize", "images_to_pdf", "image_watermark",
             "image_ocr", "image_thumbnail"),
    ".jpeg": ("image_compress", "image_convert", "image_resize", "images_to_pdf", "image_ocr"),
    ".webp": ("image_convert", "image_compress", "image_resize", "images_to_pdf"),
    ".bmp": ("image_convert", "image_compress", "images_to_pdf"),
    ".tif": ("image_convert", "images_to_pdf", "image_compress"),
    ".tiff": ("image_convert", "images_to_pdf", "image_compress"),
    ".gif": ("image_convert", "images_to_pdf", "media_convert"),
    ".ico": ("image_convert", "images_to_pdf"),
    ".docx": ("docx_to_pdf", "word_to_text", "table_convert"),
    ".doc": ("docx_to_pdf", "word_to_text"),
    ".xlsx": ("table_convert", "docx_to_pdf", "sheet_to_csv"),
    ".xls": ("table_convert", "docx_to_pdf"),
    ".csv": ("table_convert", "table_split", "json_tool"),
    ".json": ("json_tool", "table_convert", "json_query"),
    ".pptx": ("docx_to_pdf",),
    ".ppt": ("docx_to_pdf",),
    ".mp4": ("video_to_audio", "video_compress", "video_to_gif", "media_convert", "video_trim",
             "video_frames", "video_merge", "video_watermark"),
    ".mkv": ("video_to_audio", "video_compress", "media_convert", "video_trim", "video_merge"),
    ".mov": ("video_to_audio", "video_compress", "media_convert", "video_to_gif"),
    ".avi": ("video_to_audio", "media_convert", "video_compress"),
    ".webm": ("video_to_audio", "media_convert", "video_compress"),
    ".mp3": ("audio_trim", "audio_normalize", "media_convert", "media_info"),
    ".wav": ("audio_trim", "audio_normalize", "media_convert", "media_info"),
    ".m4a": ("audio_trim", "media_convert", "audio_normalize"),
    ".flac": ("audio_trim", "media_convert", "audio_normalize"),
    ".ogg": ("audio_trim", "media_convert"),
    ".zip": ("zip_extract", "archive_list"),
    ".rar": ("archive_list",),
    ".7z": ("archive_list",),
    ".txt": ("text_transform", "text_stats", "hash_files", "base64_tool", "markdown_to_html"),
    ".md": ("markdown_to_html", "text_transform"),
    ".log": ("text_transform", "regex_tester", "hash_files"),
    ".py": ("regex_tester", "hash_files", "text_diff"),
    ".exe": ("hash_files", "file_encrypt", "file_lock_check"),
    ".iso": ("hash_files",),
    ".svg": ("image_convert",),
    ".heic": ("image_convert",),
}

ICON_CATEGORY = {
    "pdf": "pdf", "png": "image", "jpg": "image", "mp4": "media", "mp3": "media",
    "zip": "files", "txt": "convert",
}


def tools_for_files(paths: List[str], limit: int = 6) -> List[str]:
    """Ranked tool ids that make sense for a set of dropped files."""
    if not paths:
        return []
    suffixes = [Path(p).suffix.lower() for p in paths]
    scores: Dict[str, float] = {}
    for suffix in suffixes:
        candidates = EXT_TOOLS.get(suffix, ())
        for rank, tool_id in enumerate(candidates):
            scores[tool_id] = scores.get(tool_id, 0.0) + (len(candidates) - rank)
    if len(paths) > 1:
        for tool_id in ("images_to_pdf", "pdf_merge", "zip_create", "image_compress",
                        "video_merge", "find_duplicates", "hash_files"):
            scores[tool_id] = scores.get(tool_id, 0.0) + 3.5
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    return [tool_id for tool_id, _score in ranked[:limit]]


def describe_files(paths: List[str]) -> str:
    if not paths:
        return ""
    names = ", ".join(Path(p).name for p in paths[:3])
    if len(paths) > 3:
        names += f" +{len(paths) - 3}"
    return names
