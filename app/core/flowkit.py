"""Flow kit — everything around a flow that is not "run it".

A *flow* is a :class:`app.core.workflows.Workflow` that may use the v6 controls
(`when`, `foreach`, `retries`, `on_error`).  This module knows how to

* **validate** a flow before it wastes ten minutes of the user's time,
* **explain** a flow in plain Persian/English (with the conditions spelled out),
* **build** a flow automatically from the contents of a folder, and
* **export** a flow as a standalone, runnable Python script.

It deliberately imports Qt-dependent modules lazily so the core stays testable
head-less and `app/core/*` gains no new import-time widget dependency.
"""
from __future__ import annotations

import json
import re
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logger import get_logger
from .paths import data_dir
from .privacy import sanitize_values

log = get_logger("flow")

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

KNOWN_PLACEHOLDERS = {
    "prev", "prev_all", "prev_dir", "prev_name", "input", "input_all", "out_dir", "index",
    "item", "item_name", "item_dir", "item_ext", "item_index", "item_count",
}

# Extensions grouped by the flow a user would want for them.
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
OFFICE_EXT = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".rtf"}
MEDIA_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".srt", ".vtt"}

GOALS = (
    ("auto", "هوشمند (خودش تشخیص می‌دهد)", "Smart (detect automatically)"),
    ("share", "فرستادن به دیگران (کوچک + ZIP)", "Share (shrink + ZIP)"),
    ("archive", "آرشیو فشرده", "Compact archive"),
    ("documents", "سند‌سازی (آفیس/PDF)", "Documents (Office/PDF)"),
    ("ocr", "جست‌وجوپذیر کردن PDFها (OCR)", "Searchable PDFs (OCR)"),
    ("privacy", "پاک‌سازی داده‌ی خصوصی", "Privacy scrub"),
)


# --------------------------------------------------------------------- report
@dataclass
class FlowIssue:
    level: str                 # error | warning
    code: str
    message: str
    message_en: str
    step: int = 0              # 1-based; 0 = whole flow

    def text(self, lang: str = "fa") -> str:
        body = self.message_en if lang == "en" else self.message
        where = ""
        if self.step:
            where = (f"گام {self.step}: " if lang != "en" else f"step {self.step}: ")
        icon = "✖" if self.level == "error" else "!"
        return f"{icon} {where}{body}"


@dataclass
class FlowReport:
    issues: List[FlowIssue] = field(default_factory=list)
    steps: int = 0

    @property
    def errors(self) -> List[FlowIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> List[FlowIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self, lang: str = "fa") -> str:
        if not self.issues:
            return ("بدون ایراد. جریان آماده‌ی اجراست." if lang != "en"
                    else "No issues found. This flow is ready to run.")
        lines = [issue.text(lang) for issue in self.issues]
        head = (f"{len(self.errors)} خطا · {len(self.warnings)} هشدار · {self.steps} گام"
                if lang != "en" else
                f"{len(self.errors)} error(s) · {len(self.warnings)} warning(s) · {self.steps} step(s)")
        return head + "\n" + "\n".join(lines)


def _flow_tool_names(workflow) -> List[str]:
    return [step.tool_id for step in workflow.steps]


def _iter_strings(values: Any):
    if isinstance(values, str):
        yield values
    elif isinstance(values, dict):
        for item in values.values():
            yield from _iter_strings(item)
    elif isinstance(values, (list, tuple)):
        for item in values:
            yield from _iter_strings(item)


def validate(workflow, lang: str = "fa") -> FlowReport:
    """Static analysis: dead links, unfilled fields, unusable conditions."""
    from .registry import get as get_tool

    report = FlowReport(steps=len(workflow.steps))
    if not workflow.steps:
        report.issues.append(FlowIssue("error", "empty", "جریانی بدون گام قابل اجرا نیست.",
                                       "A flow with no steps cannot run."))
        return report

    previous_ids: List[str] = []
    for index, step in enumerate(workflow.steps, start=1):
        spec = get_tool(step.tool_id)
        if spec is None:
            report.issues.append(FlowIssue(
                "error", "unknown_tool", f"ابزار «{step.tool_id}» وجود ندارد.",
                f"Unknown tool '{step.tool_id}'.", index))
            continue
        if step.tool_id in previous_ids:
            report.issues.append(FlowIssue(
                "warning", "duplicate", f"ابزار «{spec.name}» بیش از یک بار تکرار شده است.",
                f"'{spec.name_en}' appears more than once.", index))
        previous_ids.append(step.tool_id)

        for f in spec.fields:
            if not f.required:
                continue
            value = step.values.get(f.key)
            if value in (None, "", []):
                report.issues.append(FlowIssue(
                    "error", "required",
                    f"فیلد «{f.label or f.key}» در گام «{spec.name}» خالی است.",
                    f"Field '{f.label_en or f.key}' of '{spec.name_en}' is empty.", index))

        for text in _iter_strings(step.values):
            for name in _PLACEHOLDER_RE.findall(text):
                if name not in KNOWN_PLACEHOLDERS:
                    report.issues.append(FlowIssue(
                        "warning", "placeholder",
                        f"جای‌نگهدار «{{{name}}}» شناخته نیست؛ اگر با --set پر نمی‌شود، خالی می‌ماند.",
                        f"Unknown placeholder '{{{name}}}'; it stays empty unless --set provides it.",
                        index))

        if step.foreach:
            resolved = step.foreach
            uses_item = any(_PLACEHOLDER_RE.search(text) and
                            any(token in text for token in
                                ("{item}", "{item_name}", "{item_dir}", "{item_ext}"))
                            for text in _iter_strings(step.values))
            if not uses_item:
                report.issues.append(FlowIssue(
                    "warning", "foreach_unused",
                    "این گام روی هر فایل تکرار می‌شود ولی هیچ‌جا از {item} استفاده نمی‌کند؛ "
                    "ممکن است فقط کار تکراری انجام دهد.",
                    "The step loops over items but never uses {item}; it may repeat the same work.",
                    index))
            if not any(char in resolved for char in "*?[{") and resolved not in (
                    "{prev_all}", "{input_all}", "{prev}", "{input}"):
                report.issues.append(FlowIssue(
                    "warning", "foreach_single",
                    "الگوی تکرار هیچ نویسه‌ی عام (glob) ندارد؛ احتمالاً فقط یک مورد اجرا می‌شود.",
                    "The loop pattern has no glob characters; it probably runs on a single item.",
                    index))

        if step.when and not _PLACEHOLDER_RE.search(step.when):
            report.issues.append(FlowIssue(
                "warning", "when_static",
                "شرط هیچ جای‌نگهداری ندارد، پس همیشه یک نتیجه می‌دهد.",
                "The condition contains no placeholder, so it always yields the same answer.", index))

        from .execution import classify_invocation

        assessment = classify_invocation(spec, step.values)
        if assessment.requires_confirmation:
            tier = assessment.risk.label_for(lang)
            report.issues.append(FlowIssue(
                "warning", "danger",
                f"ابزار «{spec.name}» ({tier}) است و در رابط کاربری تأیید می‌خواهد.",
                f"'{spec.name_en}' is {tier.lower()} and asks for confirmation in the UI.", index))
            if step.on_error == "continue":
                report.issues.append(FlowIssue(
                    "warning", "danger_continue",
                    "ابزار حساس/مخرب با «ادامه در صورت خطا» ترکیب شده است.",
                    "A sensitive/destructive tool is combined with 'continue on error'.", index))

        if step.retries and assessment.requires_confirmation:
            report.issues.append(FlowIssue(
                "warning", "danger_retry",
                "تلاش مجدد روی یک ابزار حساس/مخرب می‌تواند همان تغییر را چند بار انجام دهد.",
                "Retrying a sensitive/destructive tool can repeat the same mutation.", index))

    # the first step must have something to work on
    first = workflow.steps[0]
    first_spec = get_tool(first.tool_id)
    if first_spec is not None and not first.foreach:
        from .workflows import input_field
        key, _takes_list = input_field(first_spec)
        if key and first.values.get(key) in (None, "", []):
            report.issues.append(FlowIssue(
                "error", "no_input",
                f"گام اول («{first_spec.name}») ورودی ندارد؛ با --file یا فیلد ورودی آن را بدهید.",
                f"The first step ('{first_spec.name_en}') has no input; pass one with --file.",
                1))
    return report


# ---------------------------------------------------------------------- explain
def explain(workflow, lang: str = "fa") -> str:
    """Plain-language description of a flow, step by step."""
    from .registry import get as get_tool
    from .workflows import input_field

    english = lang == "en"
    lines: List[str] = []
    title = workflow.name_for(lang)
    lines.append(f"«{title}»" if not english else f"'{title}'")
    desc = workflow.desc_for(lang)
    if desc:
        lines.append(desc)
    total = len(workflow.steps)
    lines.append(("شامل {} گام است:" if not english else "It has {} step(s):").format(total))
    runs_condition = 0
    runs_loop = 0
    for index, step in enumerate(workflow.steps, start=1):
        spec = get_tool(step.tool_id)
        name = spec.name_for(lang) if spec else step.tool_id
        key, takes_list = input_field(spec) if spec else ("", False)
        target = step.values.get(key) if key else ""
        target_text = ""
        if isinstance(target, (list, tuple)):
            target_text = "، ".join(str(item) for item in target[:3])
            if len(target) > 3:
                target_text += " …"
        elif target:
            target_text = str(target)
        sentence = f"{index}) {name}"
        if target_text:
            sentence += f" ← {target_text}"
        notes: List[str] = []
        if step.when:
            runs_condition += 1
            notes.append(("فقط اگر " if not english else "only if ") + step.when)
        if step.foreach:
            runs_loop += 1
            notes.append(("برای هر مورد مطابق " if not english else "for each match of ")
                         + step.foreach)
        if step.retries:
            notes.append((f"تا {step.retries} بار تلاش مجدد" if not english
                          else f"retried up to {step.retries}x"))
        if step.on_error == "continue":
            notes.append(("خطا اجرا را متوقف نمی‌کند" if not english
                          else "an error does not stop the flow"))
        if notes:
            sentence += " — " + (", ".join(notes) if english else "، ".join(notes))
        lines.append(sentence)
    summary = (f"{runs_condition} گام شرطی و {runs_loop} گام حلقه‌ای دارد."
               if not english else f"{runs_condition} conditional step(s), {runs_loop} looping step(s).")
    lines.append(summary)
    return "\n".join(lines)


def summarize(workflow, lang: str = "fa") -> str:
    english = lang == "en"
    names = [step.title(lang) for step in workflow.steps[:4]]
    more = len(workflow.steps) - len(names)
    body = " → ".join(names)
    if more > 0:
        body += (" → …" if not english else " → …") + f" (+{more})"
    return body


# ------------------------------------------------------------------------ build
def classify(folder: str | Path) -> Dict[str, List[Path]]:
    """Group the files of a folder by the flow that makes sense for them."""
    from .utils import walk_files

    groups: Dict[str, List[Path]] = {"image": [], "pdf": [], "office": [], "media": [],
                                     "audio": [], "text": [], "other": []}
    try:
        files = walk_files(folder)
    except Exception as exc:                      # noqa: BLE001 - unreadable folder
        log.warning("classify failed for %s: %s", folder, exc)
        return groups
    for path in files:
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXT:
            groups["image"].append(path)
        elif suffix in PDF_EXT:
            groups["pdf"].append(path)
        elif suffix in OFFICE_EXT:
            groups["office"].append(path)
        elif suffix in MEDIA_EXT:
            groups["media"].append(path)
        elif suffix in AUDIO_EXT:
            groups["audio"].append(path)
        elif suffix in TEXT_EXT:
            groups["text"].append(path)
        else:
            groups["other"].append(path)
    return groups


def detect_goal(groups: Dict[str, List[Path]]) -> str:
    """Pick the most useful goal for the folder's actual contents."""
    images = len(groups.get("image", []))
    pdfs = len(groups.get("pdf", []))
    office = len(groups.get("office", []))
    media = len(groups.get("media", []))
    if office and not images and not media:
        return "documents"
    if pdfs >= 2 and not images and not media:
        return "documents"
    if images >= 3 or (images and pdfs):
        return "share"
    if media:
        return "archive"
    if pdfs:
        return "ocr"
    return "archive"


def _glob_for(kind: str) -> str:
    patterns = {
        "image": "{input}/**/*.{jpg,jpeg,png,webp,bmp,tif,tiff}",
        "pdf": "{input}/**/*.pdf",
        "office": "{input}/**/*.{docx,xlsx,pptx,odt,rtf}",
        "media": "{input}/**/*.{mp4,mkv,mov,avi,webm,m4v}",
        "audio": "{input}/**/*.{mp3,wav,flac,m4a,aac,ogg,opus}",
    }
    return patterns[kind]


def _step(tool_id: str, values: Dict[str, Any], **kwargs) -> Any:
    from .workflows import Step

    return Step(tool_id=tool_id, values=values, **kwargs)


def build_from_folder(folder: str | Path, goal: str = "auto", lang: str = "fa") -> Any:
    """Generate a ready-to-run flow tailored to what is inside `folder`."""
    from .workflows import Workflow

    groups = classify(folder)
    chosen = detect_goal(groups) if goal in ("", "auto", None) else str(goal)
    counts = {key: len(value) for key, value in groups.items()}
    steps = []
    name_en = "Auto flow"
    desc_fa = ""
    desc_en = ""

    images = counts.get("image", 0)
    pdfs = counts.get("pdf", 0)
    office = counts.get("office", 0)
    media = counts.get("media", 0)

    if chosen == "share":
        name_fa, name_en = "بسته‌ی اشتراک‌گذاری", "Sharing pack"
        desc_fa = "عکس‌ها را کوچک می‌کند، سندها را PDF می‌کند و همه را در یک ZIP می‌گذارد."
        desc_en = "Shrinks images, converts documents to PDF and packs everything into one ZIP."
        if images:
            steps.append(_step("image_compress",
                               {"src": ["{item}"], "quality": 80, "max_side": 2000,
                                "out_dir": "{out_dir}"},
                               foreach=_glob_for("image"), on_error="continue"))
        if office:
            steps.append(_step("docx_to_pdf", {"src": "{item}", "out": "{out_dir}"},
                               foreach=_glob_for("office"), on_error="continue"))
        if pdfs:
            steps.append(_step("pdf_compress", {"src": "{item}", "mode": "balanced"},
                               foreach=_glob_for("pdf"), on_error="continue"))
        if media:
            steps.append(_step("video_compress",
                               {"src": ["{item}"], "quality": "balanced", "height": "720",
                                "out_dir": "{out_dir}"},
                               foreach=_glob_for("media"), on_error="continue"))
    elif chosen == "documents":
        name_fa, name_en = "سند‌سازی", "Document build"
        desc_fa = "سندهای آفیس را PDF می‌کند، PDFها را فشرده و شماره‌گذاری می‌کند."
        desc_en = "Converts Office documents to PDF, then compresses and numbers the PDFs."
        if office:
            steps.append(_step("docx_to_pdf", {"src": "{item}", "out": "{out_dir}"},
                               foreach=_glob_for("office"), on_error="continue"))
        if pdfs:
            steps.append(_step("pdf_compress", {"src": "{item}", "mode": "balanced"},
                               foreach=_glob_for("pdf"), on_error="continue"))
        steps.append(_step("zip_create", {"src": ["{prev_all}"], "level": "normal"}))
    elif chosen == "ocr":
        name_fa, name_en = "PDF جست‌وجوپذیر", "Searchable PDFs"
        desc_fa = "روی PDFها OCR می‌زند و متن و نسخه‌ی جست‌وجوپذیر می‌سازد."
        desc_en = "Runs OCR over each PDF and writes text plus a searchable PDF."
        steps.append(_step("pdf_ocr",
                           {"src": "{item}", "lang": "fas+eng", "out_text": True, "out_pdf": True},
                           foreach=_glob_for("pdf"), on_error="continue", retries=1))
        if images:
            steps.append(_step("image_ocr",
                               {"src": "{item}", "lang": "fas+eng"},
                               foreach=_glob_for("image"), on_error="continue"))
    elif chosen == "privacy":
        name_fa, name_en = "پاک‌سازی حریم خصوصی", "Privacy scrub"
        desc_fa = "فراداده و GPS عکس‌ها را حذف می‌کند و یادداشت‌های PDF را مسطح می‌کند."
        desc_en = "Strips EXIF/GPS from images and flattens PDF annotations."
        if images:
            steps.append(_step("image_metadata_strip",
                               {"src": "{item}", "out_dir": "{out_dir}"},
                               foreach=_glob_for("image"), on_error="continue"))
        if pdfs:
            steps.append(_step("pdf_flatten_annotations",
                               {"src": "{item}", "out_dir": "{out_dir}"},
                               foreach=_glob_for("pdf"), on_error="continue"))
        steps.append(_step("zip_create", {"src": ["{prev_all}"], "level": "normal"}))
    elif chosen == "archive":
        name_fa, name_en = "آرشیو فشرده", "Compact archive"
        desc_fa = "همه‌ی فایل‌های سنگین را فشرده می‌کند و در یک ZIP جمع می‌کند."
        desc_en = "Compresses every heavy file and collects the results in one ZIP."
        if images:
            steps.append(_step("image_compress",
                               {"src": ["{item}"], "quality": 82, "max_side": 0,
                                "out_dir": "{out_dir}"},
                               foreach=_glob_for("image"), on_error="continue"))
        if pdfs:
            steps.append(_step("pdf_compress", {"src": "{item}", "mode": "strong"},
                               foreach=_glob_for("pdf"), on_error="continue"))
        if media:
            steps.append(_step("video_compress",
                               {"src": ["{item}"], "quality": "small", "height": "720",
                                "out_dir": "{out_dir}"},
                               foreach=_glob_for("media"), on_error="continue"))
        if counts.get("audio"):
            steps.append(_step("audio_normalize", {"src": ["{item}"], "target": -16.0},
                               foreach=_glob_for("audio"), on_error="continue"))
        steps.append(_step("zip_create", {"src": ["{prev_all}"], "level": "max"}))
    else:                                          # pragma: no cover - defensive
        chosen = "archive"
        steps.append(_step("zip_create", {"src": ["{input_all}"], "level": "normal"}))

    if not steps:
        steps.append(_step("zip_create", {"src": ["{input_all}"], "level": "normal"}))
        desc_fa = "هیچ فایل شناخته‌شده‌ای پیدا نشد؛ جریان پیش‌فرض فقط یک ZIP می‌سازد."
        desc_en = "No known file type was found; the default flow just makes a ZIP."

    found = (f"{images} عکس · {pdfs} PDF · {office} سند · {media} ویدیو"
             if lang != "en" else f"{images} images · {pdfs} PDFs · {office} docs · {media} videos")
    return Workflow(
        id=f"auto_{uuid.uuid4().hex[:8]}",
        name=name_fa,
        name_en=name_en,
        desc=desc_fa + f"\nپیدا شد: {found}",
        desc_en=desc_en + f"\nFound: {found}",
        icon="sparkles",
        source="generated",
        created=time.time(),
        steps=steps,
    )


# ---------------------------------------------------------------------- export
SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Standalone runner generated by WinGenius {version}.

Flow: {title}

Usage:
    python {filename} <input-path> [more inputs...]

It calls WinGenius' own flow engine, so the tools stay identical to the app.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# WinGenius must be importable: either installed (`pip install wingenius`) or a
# checkout on PYTHONPATH.  Set WG_HOME to point at a checkout explicitly.
import os
if os.environ.get("WG_HOME"):
    sys.path.insert(0, os.environ["WG_HOME"])

from app.core.workflows import Workflow, run_sync

FLOW = json.loads({payload_literal})

LANG = {lang!r}
OUT_DIR = {out_dir!r}


def main(argv):
    inputs = [item for item in argv[1:] if item.strip()]
    if not inputs:
        print("usage: python {filename} <input-path> [more inputs...]")
        return 2
    flow = Workflow.from_dict(FLOW)
    result = run_sync(flow, lang=LANG, output_dir=OUT_DIR,
                      base_values={{"input": inputs[0], "__paths__": inputs}})
    print(result.message)
    for step in result.steps:
        mark = "ok" if step.status == "done" else ("skip" if step.status == "skipped" else step.status)
        print(f"  [{{mark:6}}] {{step.tool_id}}: {{step.message}}")
        for path in step.outputs:
            print(f"           -> {{path}}")
    return 0 if result.status in ("done", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


def export_script(workflow, lang: str = "fa", out_dir: str = "",
                  version: str = "") -> str:
    """Return a standalone Python script that runs this flow (secrets scrubbed)."""
    from .paths import APP_VERSION

    sanitized = sanitize_flow_values(workflow)
    payload_json = json.dumps(sanitized, ensure_ascii=False, indent=2)
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", workflow.id or "flow") + ".py"
    clean_title = workflow.name_for(lang).replace('"""', "'''").replace("\\", "/")
    return SCRIPT_TEMPLATE.format(
        version=version or APP_VERSION,
        title=clean_title,
        filename=filename,
        payload_literal=repr(payload_json),
        lang="en" if lang == "en" else "fa",
        out_dir=out_dir or "",
    )


# ------------------------------------------------------------------- file i/o
def load_flow(path: str | Path):
    """Load a flow from a JSON file (accepts a bare step list too)."""
    from .workflows import Workflow

    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    data = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"name": target.stem, "steps": data}
    if not isinstance(data, dict):
        raise ValueError("flow JSON must be an object with a 'steps' list")
    data.setdefault("id", f"file_{target.stem}")
    data.setdefault("name", target.stem)
    return Workflow.from_dict(data)


def save_flow(workflow, path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target.with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(workflow.as_dict(), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


def flows_dir() -> Path:
    folder = data_dir() / "flows"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def library() -> List[Any]:
    """Built-in flows + the user's stored flows + every .json in flows/."""
    from .workflows import store

    seen = {item.id: item for item in store.all()}
    for path in sorted(flows_dir().glob("*.json")):
        try:
            flow = load_flow(path)
        except Exception as exc:                   # noqa: BLE001 - broken file
            log.warning("cannot read flow %s: %s", path, exc)
            continue
        seen.setdefault(flow.id, flow)
    return list(seen.values())


def resolve(name_or_path: str) -> Any:
    """Find a flow by id, by name (fa/en) or by path to a .json file."""
    text = (name_or_path or "").strip().strip('"')
    if not text:
        raise ValueError("empty flow reference")
    candidate = Path(text)
    if candidate.suffix.lower() == ".json" and candidate.exists():
        return load_flow(candidate)
    lowered = text.lower()
    for flow in library():
        if flow.id.lower() == lowered:
            return flow
        if lowered in (flow.name.lower(), (flow.name_en or "").lower()):
            return flow
    for path in sorted(flows_dir().glob("*.json")):
        if path.stem.lower() == lowered:
            return load_flow(path)
    raise LookupError(text)


def export_text(workflow, lang: str = "fa") -> str:
    """The flow as pretty JSON, ready to share in an issue or a repo."""
    return json.dumps(workflow.as_dict(), ensure_ascii=False, indent=2)


def estimate_steps(workflow) -> Tuple[int, int]:
    """(steps, items that will actually run) — used by the UI/CLI summaries."""
    loops = sum(1 for step in workflow.steps if step.foreach)
    return len(workflow.steps), loops


def describe_library(lang: str = "fa") -> str:
    lines = []
    for flow in library():
        source = {"builtin": "داخلی" if lang != "en" else "builtin",
                  "user": "کاربر" if lang != "en" else "user",
                  "generated": "ساخته‌شده" if lang != "en" else "generated",
                  "plan": "دستیار" if lang != "en" else "assistant"}.get(flow.source, flow.source)
        lines.append(f"• {flow.name_for(lang)}  [{source}, {len(flow.steps)} گام]")
    return "\n".join(lines) if lines else "—"


def sanitize_flow_values(workflow) -> Any:
    """Return a copy with secrets removed (used before writing a flow to disk)."""
    data = workflow.as_dict()
    for step in data.get("steps", []):
        step["values"] = sanitize_values(step.get("tool_id", ""), step.get("values") or {})
    return data


def wrap(text: str, width: int = 88) -> str:
    return "\n".join(textwrap.wrap(text, width=width)) if text else text
