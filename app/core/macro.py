"""Macro engine — small declarative programs that drive WinGenius tools.

A macro is plain JSON (so it can be written, reviewed and version-controlled):

    {
      "name": "نمونه",
      "vars": {"src": "", "quality": 78},
      "steps": [
        {"type": "run", "tool": "image_compress",
         "args": {"src": ["{src}"], "quality": "{quality}"}},
        {"type": "foreach", "glob": "{src}/*.jpg", "as": "photo", "steps": [
            {"type": "if", "test": "size_gt", "path": "{photo}", "kb": 200,
             "then": [{"type": "log", "message": "big: {photo}"}]}
        ]},
        {"type": "copy", "src": "{photo}", "dst": "{out}"}
      ]
    }

Supported step types: `run`, `foreach`, `if`, `set`, `log`, `copy`, `move`,
`delete`, `mkdir`, `write`, `sleep`, `when_error`.

Placeholders use `{name}` and are resolved recursively inside args.  Every
file-changing step (`copy`, `move`, `delete`) is written to the undo journal,
so a macro run is as reversible as a single tool run.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .paths import data_dir
from .spec import Cancelled
from .utils import ensure_dir, unique_path

STEP_TYPES = ("run", "foreach", "if", "set", "log", "copy", "move", "delete",
              "mkdir", "write", "sleep", "when_error")

BUILTIN_MACROS: List[Dict[str, Any]] = [
    {
        "name": "بسته‌ی عکس‌ها برای ارسال",
        "name_en": "Photo pack for sharing",
        "description": "همه‌ی عکس‌های یک پوشه را کوچک می‌کند، زیر ۳۰۰ کیلوبایت، و در پوشه‌ی خروجی می‌گذارد.",
        "description_en": "Shrinks every image of a folder to under 300 KB into an output folder.",
        "vars": {"folder": "", "out": "", "quality": 72},
        "steps": [
            {"type": "mkdir", "path": "{out}"},
            {"type": "foreach", "glob": "{folder}/*.{jpg,jpeg,png,webp}", "as": "photo", "steps": [
                {"type": "run", "tool": "image_compress",
                 "args": {"src": ["{photo}"], "quality": "{quality}", "out_dir": "{out}"}},
            ]},
            {"type": "log", "message": "done: {out}"},
        ],
    },
    {
        "name": "پاک‌سازی پوشه‌ی دانلود",
        "name_en": "Tidy the downloads folder",
        "description": "فایل‌های موقت و پوشه‌های خالی قدیمی‌تر از ۷ روز را به سطل بازیافت داخلی می‌فرستد.",
        "description_en": "Moves stale temp files to the internal trash (undoable).",
        "vars": {"folder": "", "days": 7},
        "steps": [
            {"type": "foreach", "glob": "{folder}/*.{tmp,temp,part,crdownload}", "as": "junk", "steps": [
                {"type": "delete", "path": "{junk}"},
            ]},
            {"type": "log", "message": "cleanup finished"},
        ],
    },
    {
        "name": "PDF‌های اسکن‌شده را جست‌وجوپذیر کن",
        "name_en": "Make scanned PDFs searchable",
        "description": "برای همه‌ی PDFهای یک پوشه OCR می‌زند و نسخه‌ی متنی را کنارشان می‌سازد.",
        "description_en": "Runs OCR over every PDF in a folder and saves the text next to it.",
        "vars": {"folder": "", "lang": "fas+eng"},
        "steps": [
            {"type": "foreach", "glob": "{folder}/*.pdf", "as": "doc", "steps": [
                {"type": "run", "tool": "pdf_ocr",
                 "args": {"src": "{doc}", "lang": "{lang}", "out_text": True}},
            ]},
        ],
    },
]


class MacroError(Exception):
    """Raised when a macro cannot be understood."""


# ------------------------------------------------------------------ validation
def validate(macro: Dict[str, Any], known_tools: Optional[Iterable[str]] = None,
             lang: str = "fa") -> List[str]:
    """Return a list of human-readable problems (empty list = healthy)."""
    problems: List[str] = []
    if not isinstance(macro, dict):
        return ["ماکرو باید یک شیء JSON باشد." if lang != "en" else "macro must be a JSON object"]
    steps = macro.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["فیلد steps لازم است." if lang != "en" else "a non-empty 'steps' list is required"]
    known = set(known_tools or ())

    def walk(items: List[Any], path: str) -> None:
        for index, step in enumerate(items):
            where = f"{path}[{index}]"
            if not isinstance(step, dict):
                problems.append(f"{where}: " + ("مرحله باید شیء باشد" if lang != "en" else "step must be an object"))
                continue
            kind = step.get("type")
            if kind not in STEP_TYPES:
                problems.append(f"{where}: " + (f"نوع ناشناخته «{kind}»" if lang != "en" else f"unknown type '{kind}'"))
                continue
            if kind == "run":
                tool_id = step.get("tool")
                if not tool_id:
                    problems.append(f"{where}: " + ("فیلد tool لازم است" if lang != "en" else "field 'tool' is required"))
                elif known and tool_id not in known:
                    problems.append(f"{where}: " + (f"ابزار «{tool_id}» وجود ندارد" if lang != "en" else f"unknown tool '{tool_id}'"))
                if not isinstance(step.get("args", {}), dict):
                    problems.append(f"{where}: " + ("args باید شیء باشد" if lang != "en" else "args must be an object"))
            elif kind == "foreach":
                if not step.get("glob"):
                    problems.append(f"{where}: " + ("glob لازم است" if lang != "en" else "glob is required"))
                if not step.get("as"):
                    problems.append(f"{where}: " + ("نام متغیر (as) لازم است" if lang != "en" else "'as' variable is required"))
                walk(step.get("steps", []) or [], where + ".steps")
            elif kind == "if":
                if not step.get("test"):
                    problems.append(f"{where}: " + ("test لازم است" if lang != "en" else "test is required"))
                walk(step.get("then", []) or [], where + ".then")
                walk(step.get("else", []) or [], where + ".else")
            elif kind in ("copy", "move"):
                if not step.get("src") or not step.get("dst"):
                    problems.append(f"{where}: " + ("src و dst لازم‌اند" if lang != "en" else "src and dst are required"))
            elif kind in ("delete", "mkdir", "write"):
                if not step.get("path"):
                    problems.append(f"{where}: " + ("path لازم است" if lang != "en" else "path is required"))
            elif kind == "set":
                if not step.get("name"):
                    problems.append(f"{where}: " + ("name لازم است" if lang != "en" else "name is required"))

    walk(steps, "steps")
    return problems


# ---------------------------------------------------------------------- result
@dataclass
class MacroResult:
    status: str = "done"                      # done | failed | partial | dry-run
    name: str = ""
    log: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    executed: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("done", "dry-run") and not self.errors

    def message(self, lang: str = "fa") -> str:
        """One-line summary, ready for the CLI, the UI and the local API."""
        steps = self.executed
        if lang == "en":
            status = {"done": "finished", "dry-run": "dry-run finished",
                      "partial": "finished with problems", "failed": "failed"}.get(self.status,
                                                                                    self.status)
            return f"macro “{self.name}” — {status} · {steps} step(s)"
        status = {"done": "پایان یافت", "dry-run": "اجرای تمرینی تمام شد",
                  "partial": "با خطا تمام شد", "failed": "شکست خورد"}.get(self.status, self.status)
        return f"ماکرو «{self.name}» — {status} · {steps} گام"

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "log": self.log, "outputs": self.outputs,
                "errors": self.errors, "executed": self.executed}


# ---------------------------------------------------------------------- runner
def _substitute(value: Any, variables: Dict[str, Any]) -> Any:
    """Resolve `{name}` placeholders inside strings/lists/dicts."""
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}") and value.count("{") == 1:
            key = value[1:-1]
            if key in variables:
                return variables[key]
        out = value
        for key, replacement in variables.items():
            out = out.replace("{" + key + "}", str(replacement))
        return out
    if isinstance(value, list):
        return [_substitute(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, variables) for key, item in value.items()}
    return value


def _expand_glob(pattern: str) -> List[Path]:
    path = Path(pattern)
    if any(char in path.name for char in "*?["):
        try:
            return sorted(p for p in path.parent.glob(path.name) if p.exists())
        except (OSError, ValueError):
            return []
    return [path] if path.exists() else []


def _brace_glob(pattern: str) -> List[str]:
    """Support `*.{jpg,png}` by expanding braces into several patterns."""
    if "{" not in pattern or "}" not in pattern:
        return [pattern]
    head, rest = pattern.split("{", 1)
    body, tail = rest.split("}", 1)
    patterns: List[str] = []
    for option in body.split(","):
        patterns.extend(_brace_glob(head + option.strip() + tail))
    return patterns


class MacroRunner:
    """Executes a macro dict step by step."""

    def __init__(self, lang: str = "fa", output_dir: str = "", dry_run: bool = False,
                 run_tool: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_progress: Optional[Callable[[int, int], None]] = None,
                 journal_enabled: bool = True,
                 policy: Any = None, cancel_event: Optional[threading.Event] = None) -> None:
        self.lang = "en" if lang == "en" else "fa"
        self.output_dir = output_dir
        self.dry_run = dry_run
        self.policy = policy
        self._cancel = cancel_event or threading.Event()
        self._run_tool = run_tool or self._default_run_tool
        self._on_log = on_log
        self._on_progress = on_progress
        self.journal_enabled = journal_enabled
        self.result = MacroResult(status="dry-run" if dry_run else "done")
        self.variables: Dict[str, Any] = {}
        self.on_error = "stop"                # stop | continue
        self._moves: List[tuple] = []
        self._trash: List[tuple] = []

    # ------------------------------------------------------------------ helpers
    def M(self, fa: str, en: str) -> str:
        return en if self.lang == "en" else fa

    def _log(self, message: str) -> None:
        self.result.log.append(message)
        if self._on_log:
            try:
                self._on_log(message)
            except Exception:
                pass

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled(self.M("عملیات لغو شد.", "Operation cancelled."))

    def _progress(self, current: int, total: int = 0) -> None:
        self._check_cancel()
        if self._on_progress:
            try:
                self._on_progress(current, total)
            except Cancelled:
                raise
            except Exception:
                pass

    def _default_run_tool(self, tool_id: str, args: Dict[str, Any]) -> Any:
        from . import registry
        from .execution import execution_service
        from .spec import RunContext

        spec = registry.get(tool_id)
        if spec is None:
            raise MacroError(self.M(f"ابزار «{tool_id}» پیدا نشد.", f"tool '{tool_id}' not found"))
        ctx = RunContext(lang=self.lang, output_dir=self.output_dir,
                         on_message=lambda text: self._log(str(text)),
                         cancel_event=self._cancel, policy=self.policy)
        return execution_service.execute(spec, dict(args), ctx, policy=self.policy)

    @staticmethod
    def _result_outputs(result: Any) -> List[str]:
        outputs = getattr(result, "outputs", None) or []
        return [str(item) for item in outputs]

    # -------------------------------------------------------------------- files
    def _record_moves(self, moves: List[tuple]) -> None:
        if not moves:
            return
        self._moves.extend(moves)
        if self.journal_enabled:
            from .journal import journal
            journal().record_moves(moves, self.M("اجرای ماکرو", "macro run"),
                                   self.M("اجرای ماکرو", "macro run"), tool_id="macro_run")

    def _record_trash(self, pairs: List[tuple]) -> None:
        if not pairs:
            return
        self._trash.extend(pairs)
        if self.journal_enabled:
            from .journal import journal
            journal().record_trash(pairs, self.M("سطل ماکرو", "macro trash"),
                                   self.M("سطل ماکرو", "macro trash"), tool_id="macro_run")

    def _check_file_policy(self, action_name: str) -> None:
        if self.policy is None:
            return
        from .execution import OperationRisk, PolicyDeniedError, RiskAssessment

        risk = OperationRisk.DESTRUCTIVE if action_name == "write" else OperationRisk.CONFIRM
        if not self.policy.allows(risk):
            assessment = RiskAssessment(
                f"macro:{action_name}", risk,
                "حذف یا جابه‌جایی فایل توسط ماکرو نیازمند مجوز است",
                f"Macro file {action_name} requires policy confirmation",
            )
            raise PolicyDeniedError(
                self.M("عملیات تغییر فایل ماکرو توسط سیاست امنیتی مسدود شد.",
                       "Macro file mutation blocked by execution policy."),
                assessment,
            )

    def _delete(self, target: Path) -> None:
        if self.dry_run:
            self.result.executed += 1
            self._log(self.M(f"حذف (تمرینی): {target}", f"would trash: {target}"))
            return
        self._check_file_policy("delete")
        from .journal import send_to_trash
        self._record_trash(send_to_trash([target]))
        self.result.executed += 1
        self._log(self.M(f"به سطل رفت: {target}", f"trashed: {target}"))

    def _copy(self, src: Path, dst: Path) -> Optional[Path]:
        if self.dry_run:
            self.result.executed += 1
            self._log(self.M(f"کپی (تمرینی): {src} → {dst}", f"would copy: {src} → {dst}"))
            return None
        self._check_file_policy("copy")
        if src.is_dir():
            ensure_dir(dst)
            shutil.copytree(src, dst, dirs_exist_ok=True)
            self.result.executed += 1
            return dst
        target = unique_path(dst if dst.suffix else dst / src.name)
        ensure_dir(target.parent)
        shutil.copy2(src, target)
        self.result.executed += 1
        return target

    def _move(self, src: Path, dst: Path) -> Optional[Path]:
        if self.dry_run:
            self.result.executed += 1
            self._log(self.M(f"انتقال (تمرینی): {src} → {dst}", f"would move: {src} → {dst}"))
            return None
        self._check_file_policy("move")
        target = unique_path(dst if dst.suffix else dst / src.name)
        ensure_dir(target.parent)
        shutil.move(str(src), str(target))
        self._record_moves([(src, target)])
        self.result.executed += 1
        return target

    # --------------------------------------------------------------------- run
    def execute(self, macro: Dict[str, Any], variables: Optional[Dict[str, Any]] = None) -> MacroResult:
        problems = validate(macro, lang=self.lang)
        if problems:
            self.result.status = "failed"
            self.result.errors.extend(problems)
            return self.result

        self.variables = {}
        defaults = macro.get("vars", {}) or {}
        if isinstance(defaults, dict):
            self.variables.update(defaults)
        for key, value in (variables or {}).items():
            self.variables[key] = value
        self.variables.setdefault("out", self.output_dir or str(Path.home()))
        self.on_error = str(macro.get("on_error", "stop")).lower()
        if self.output_dir:
            self.variables.setdefault("out_dir", self.output_dir)

        steps = macro.get("steps", [])
        self.result.name = str(macro.get("name", "") or "")
        self._log(self.M(f"شروع ماکرو «{macro.get('name', '')}» در {len(steps)} مرحله",
                         f"starting macro '{macro.get('name', '')}' with {len(steps)} step(s)"))
        try:
            self._execute_steps(steps, depth=0)
        except MacroError as exc:
            self.result.errors.append(str(exc))
        except Exception as exc:                       # noqa: BLE001 - report, never crash
            from .execution import PolicyDeniedError

            if isinstance(exc, (PolicyDeniedError, Cancelled)):
                raise
            self.result.errors.append(f"{type(exc).__name__}: {exc}")

        if self.result.errors:
            self.result.status = "partial" if self.result.executed else "failed"
        elif self.dry_run:
            self.result.status = "dry-run"
        else:
            self.result.status = "done"
        self._log(self.M(f"پایان: {self.result.status} · {self.result.executed} مرحله",
                         f"finished: {self.result.status} · {self.result.executed} step(s)"))
        return self.result

    def _execute_steps(self, steps: List[Dict[str, Any]], depth: int) -> None:
        if depth > 12:
            raise MacroError(self.M("عمق تودرتویی بیش از حد است.", "nesting is too deep"))
        for index, step in enumerate(steps):
            self._progress(index + 1, len(steps))
            kind = str(step.get("type", "")).lower()
            if kind == "run":
                self._step_run(step)
            elif kind == "foreach":
                self._step_foreach(step, depth)
            elif kind == "if":
                self._step_if(step, depth)
            elif kind == "set":
                name = str(step.get("name"))
                self.variables[name] = _substitute(step.get("value", ""), self.variables)
            elif kind == "log":
                self._log(str(_substitute(step.get("message", ""), self.variables)))
            elif kind == "copy":
                self._step_copy(step)
            elif kind == "move":
                self._step_move(step)
            elif kind == "delete":
                target = Path(str(_substitute(step.get("path", ""), self.variables)))
                if target.exists():
                    self._delete(target)
                else:
                    self._log(self.M(f"پیدا نشد: {target}", f"missing: {target}"))
            elif kind == "mkdir":
                folder = Path(str(_substitute(step.get("path", ""), self.variables)))
                if self.dry_run:
                    self.result.executed += 1
                    self._log(self.M(f"پوشه (تمرینی): {folder}", f"would create: {folder}"))
                else:
                    self._check_file_policy("mkdir")
                    ensure_dir(folder)
                    self.result.executed += 1
            elif kind == "write":
                target = Path(str(_substitute(step.get("path", ""), self.variables)))
                text = str(_substitute(step.get("text", ""), self.variables))
                if self.dry_run:
                    self.result.executed += 1
                    self._log(self.M(f"نوشتن (تمرینی): {target}", f"would write: {target}"))
                else:
                    self._check_file_policy("write")
                    ensure_dir(target.parent)
                    target.write_text(text, encoding="utf-8")
                    self.result.outputs.append(str(target))
                    self.result.executed += 1
            elif kind == "sleep":
                seconds = float(step.get("seconds", 0) or 0)
                if not self.dry_run and seconds > 0:
                    self._cancel.wait(min(seconds, 30))
                    self._check_cancel()
            elif kind == "when_error":
                self.on_error = str(step.get("action", "stop")).lower()
            else:
                raise MacroError(self.M(f"نوع مرحله ناشناخته: {kind}", f"unknown step type: {kind}"))

    # --------------------------------------------------------------- step kinds
    def _step_run(self, step: Dict[str, Any]) -> None:
        tool_id = str(step.get("tool", ""))
        args = _substitute(step.get("args", {}) or {}, self.variables)
        if not isinstance(args, dict):
            args = {}
        if self.dry_run:
            self._log(self.M(f"اجرا (تمرینی): {tool_id} · {args}", f"would run: {tool_id} · {args}"))
            self.result.executed += 1
            return
        try:
            result = self._run_tool(tool_id, args)
        except Exception as exc:                       # tool errors should not kill the macro
            from .execution import PolicyDeniedError

            if isinstance(exc, (PolicyDeniedError, Cancelled)):
                raise
            message = f"{tool_id}: {exc}"
            self.result.errors.append(message)
            self._log(self.M(f"خطا در {message}", f"error in {message}"))
            if self.on_error != "continue":
                raise MacroError(message) from exc
            return
        self.result.executed += 1
        outputs = self._result_outputs(result)
        self.result.outputs.extend(outputs)
        # expose the previous step's output to later steps
        if outputs:
            self.variables["prev"] = outputs[0]
            self.variables["prev_all"] = outputs
        message = getattr(result, "message", "") or ""
        self._log(f"{tool_id}: {message}" if message else tool_id)

    def _step_foreach(self, step: Dict[str, Any], depth: int) -> None:
        pattern = str(_substitute(step.get("glob", ""), self.variables))
        name = str(step.get("as", "item"))
        limit = int(step.get("limit", 500) or 500)
        items: List[Path] = []
        for expanded in _brace_glob(pattern):
            items.extend(_expand_glob(expanded))
        seen, unique = set(), []
        for item in items:
            key = str(item)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        unique = unique[:limit]
        self._log(self.M(f"{len(unique)} مورد برای «{pattern}»", f"{len(unique)} item(s) for '{pattern}'"))
        for item in unique:
            self._check_cancel()
            self.variables[name] = str(item)
            self.variables["item"] = str(item)
            self._execute_steps(step.get("steps", []) or [], depth + 1)

    def _step_if(self, step: Dict[str, Any], depth: int) -> None:
        test = str(step.get("test", "")).lower()
        raw_path = str(_substitute(step.get("path", ""), self.variables))
        path = Path(raw_path) if raw_path else None
        outcome = False
        if test == "exists":
            outcome = bool(path and path.exists())
        elif test == "missing":
            outcome = bool(path and not path.exists())
        elif test == "is_dir":
            outcome = bool(path and path.is_dir())
        elif test == "is_file":
            outcome = bool(path and path.is_file())
        elif test == "empty":
            try:
                outcome = bool(path and path.is_file() and path.stat().st_size == 0)
            except OSError:
                outcome = False
        elif test == "size_gt":
            kb = float(step.get("kb", 0) or 0)
            try:
                outcome = bool(path and path.stat().st_size > kb * 1024)
            except OSError:
                outcome = False
        elif test == "ext":
            wanted = {str(item).lower().lstrip(".") for item in str(step.get("ext", "")).split(",") if item}
            outcome = bool(path and path.suffix.lower().lstrip(".") in wanted)
        elif test == "contains":
            needle = str(_substitute(step.get("text", ""), self.variables))
            try:
                outcome = bool(path and needle and needle in path.read_text(errors="ignore"))
            except OSError:
                outcome = False
        elif test == "equals":
            left = str(_substitute(step.get("left", ""), self.variables))
            right = str(_substitute(step.get("right", ""), self.variables))
            outcome = left == right
        else:
            raise MacroError(self.M(f"شرط ناشناخته: {test}", f"unknown test: {test}"))
        branch = step.get("then", []) if outcome else step.get("else", [])
        self._execute_steps(branch or [], depth + 1)

    def _step_copy(self, step: Dict[str, Any]) -> None:
        src = Path(str(_substitute(step.get("src", ""), self.variables)))
        dst = Path(str(_substitute(step.get("dst", ""), self.variables)))
        if not src.exists():
            self._log(self.M(f"پیدا نشد: {src}", f"missing: {src}"))
            return
        target = self._copy(src, dst)
        if target is not None:
            self.result.outputs.append(str(target))

    def _step_move(self, step: Dict[str, Any]) -> None:
        src = Path(str(_substitute(step.get("src", ""), self.variables)))
        dst = Path(str(_substitute(step.get("dst", ""), self.variables)))
        if not src.exists():
            self._log(self.M(f"پیدا نشد: {src}", f"missing: {src}"))
            return
        target = self._move(src, dst)
        if target is not None:
            self.result.outputs.append(str(target))


# --------------------------------------------------------------------- library
def macros_dir() -> Path:
    path = data_dir() / "macros"
    path.mkdir(parents=True, exist_ok=True)
    return path


def builtin_macros(lang: str = "fa") -> List[Dict[str, Any]]:
    result = []
    for template in BUILTIN_MACROS:
        item = dict(template)
        if lang == "en":
            item["name"] = template.get("name_en", template["name"])
            item["description"] = template.get("description_en", template.get("description", ""))
        result.append(item)
    return result


def save_macro(macro: Dict[str, Any], name: Optional[str] = None,
               overwrite: bool = True) -> Path:
    """Write a macro into the library; a macro with the same name is replaced."""
    safe = (name or macro.get("name") or "macro").strip()
    safe = "".join(char for char in safe if char not in '\\/:*?"<>|')[:60] or "macro"
    target = macros_dir() / f"{safe}.json"
    counter = 2
    while target.exists():
        if overwrite:
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict) and str(existing.get("name", "")).strip() == safe:
                break
        target = macros_dir() / f"{safe}-{counter}.json"
        counter += 1
    target.write_text(json.dumps(macro, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_macro(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MacroError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MacroError(f"{path}: " + ("ماکرو باید شیء باشد" if True else "macro must be an object"))
    return data


def library() -> List[Path]:
    """Macros in the library, one file per macro name (newest file wins).

    Older versions appended `-2`, `-3` … on every save, so a user who pressed
    "templates" three times ended up with duplicates. The extras are ignored here
    and the listing stays clean without touching anybody's files.
    """
    try:
        files = sorted(macros_dir().glob("*.json"))
    except OSError:
        return []
    chosen: Dict[str, Path] = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            key = str(data.get("name") or path.stem).strip() or path.stem
        except (OSError, json.JSONDecodeError):
            key = path.stem
        current = chosen.get(key)
        # prefer the file whose stem is exactly the macro name (no "-2" suffix)
        if current is None or (path.stem == key and current.stem != key):
            chosen[key] = path
    return sorted(chosen.values())
