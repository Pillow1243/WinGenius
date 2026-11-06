"""Unified tool execution service, dynamic risk classification, and caller policy gate.

Every execution surface — foreground GUI (`ToolPanel`), background queue (`JobCenter`),
workflows/flows (`workflows`, `flowkit`), macros (`macro`), assistant plans (`PlanPanel`),
CLI (`cli`), scheduler (`scheduler`), folder watcher (`watcher`), and the loopback HTTP
API (`api_server`) — routes tool invocations through `ExecutionService`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from .logger import get_logger
from .privacy import redact_known, sanitize_values, scrub_text, secret_strings
from .registry import get as get_tool
from .spec import K_DIR, K_FILE, K_FILES, Cancelled, RunContext, RunResult, ToolError, ToolSpec

log = get_logger("execution")


class OperationRisk(IntEnum):
    """Four-tier risk taxonomy for WinGenius operations."""

    SAFE = 0
    CONFIRM = 1
    DESTRUCTIVE = 2
    ELEVATED = 3

    @property
    def slug(self) -> str:
        return {
            OperationRisk.SAFE: "safe",
            OperationRisk.CONFIRM: "confirm",
            OperationRisk.DESTRUCTIVE: "destructive",
            OperationRisk.ELEVATED: "elevated",
        }[self]

    def label_for(self, lang: str = "fa") -> str:
        labels = {
            OperationRisk.SAFE: ("ایمن", "Safe"),
            OperationRisk.CONFIRM: ("نیازمند تأیید", "Confirmation required"),
            OperationRisk.DESTRUCTIVE: ("مخرب / غیرقابل بازگشت", "Destructive"),
            OperationRisk.ELEVATED: ("نیازمند دسترسی مدیر (UAC)", "Administrator / Elevated"),
        }[self]
        return labels[1] if lang == "en" else labels[0]


@dataclass(frozen=True)
class RiskAssessment:
    """Computed risk tier and human-readable rationale for a specific invocation."""

    tool_id: str
    risk: OperationRisk
    reason_fa: str = ""
    reason_en: str = ""
    nested_tools: Tuple[str, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        return self.risk >= OperationRisk.CONFIRM

    @property
    def requires_elevation(self) -> bool:
        return self.risk >= OperationRisk.ELEVATED

    def reason_for(self, lang: str = "fa") -> str:
        if lang == "en":
            return self.reason_en or self.reason_fa or self.risk.label_for("en")
        return self.reason_fa or self.reason_en or self.risk.label_for("fa")

    def as_dict(self, lang: str = "fa") -> Dict[str, Any]:
        return {
            "tool": self.tool_id,
            "risk": self.risk.slug,
            "level": int(self.risk),
            "label": self.risk.label_for(lang),
            "requires_confirmation": self.requires_confirmation,
            "requires_elevation": self.requires_elevation,
            "reason": self.reason_for(lang),
            "nested_tools": list(self.nested_tools),
        }


@dataclass(frozen=True)
class ExecutionPolicy:
    """Caller-specific execution gate and side-effect policy."""

    source: str = "direct"
    max_risk: OperationRisk = OperationRisk.SAFE
    confirmed: bool = False
    dry_run: bool = False
    record_history: bool = True

    def allows(self, risk: OperationRisk) -> bool:
        if self.dry_run or self.confirmed:
            return True
        return risk <= self.max_risk

    def merge_outer(self, outer: Optional["ExecutionPolicy"]) -> "ExecutionPolicy":
        """Propagate stricter outer bounds (e.g. API allow_dangerous=False into nested flows)."""
        if outer is None:
            return self
        if not outer.confirmed and outer.max_risk < OperationRisk.ELEVATED:
            allowed_max = min(self.max_risk, outer.max_risk) if not self.confirmed else outer.max_risk
            return ExecutionPolicy(
                source=f"{outer.source}>{self.source}",
                max_risk=allowed_max,
                confirmed=False,
                dry_run=self.dry_run or outer.dry_run,
                record_history=self.record_history and outer.record_history,
            )
        return ExecutionPolicy(
            source=f"{outer.source}>{self.source}",
            max_risk=self.max_risk,
            confirmed=self.confirmed and outer.confirmed,
            dry_run=self.dry_run or outer.dry_run,
            record_history=self.record_history and outer.record_history,
        )

    @classmethod
    def for_gui(cls, *, confirmed: bool = True, record_history: bool = True,
                source: str = "gui") -> "ExecutionPolicy":
        return cls(
            source=source,
            max_risk=OperationRisk.SAFE,
            confirmed=confirmed,
            dry_run=False,
            record_history=record_history,
        )

    @classmethod
    def for_cli(cls, *, yes: bool = False, dry_run: bool = False,
                record_history: bool = True) -> "ExecutionPolicy":
        return cls(
            source="cli",
            max_risk=OperationRisk.SAFE,
            confirmed=yes,
            dry_run=dry_run,
            record_history=record_history and not dry_run,
        )

    @classmethod
    def for_api(cls, *, allow_dangerous: bool = False, dry_run: bool = False) -> "ExecutionPolicy":
        return cls(
            source="api",
            max_risk=OperationRisk.SAFE,
            confirmed=allow_dangerous,
            dry_run=dry_run,
            record_history=not dry_run,
        )

    @classmethod
    def for_watcher(cls, *, allow_dangerous: bool = False) -> "ExecutionPolicy":
        return cls(
            source="watcher",
            max_risk=OperationRisk.SAFE,
            confirmed=allow_dangerous,
            dry_run=False,
            record_history=True,
        )

    @classmethod
    def for_scheduler(cls, *, allow_dangerous: bool = False) -> "ExecutionPolicy":
        return cls(
            source="scheduler",
            max_risk=OperationRisk.SAFE,
            confirmed=allow_dangerous,
            dry_run=False,
            record_history=True,
        )


class PolicyDeniedError(ToolError):
    """Raised when an invocation exceeds the caller's `ExecutionPolicy`."""

    def __init__(self, message: str, assessment: RiskAssessment) -> None:
        super().__init__(message)
        self.assessment = assessment


def _truthy(value: Any) -> bool:
    # Tool implementations use Python truthiness. In particular, a nonempty
    # string such as "false" is truthy; classification must not understate it.
    return bool(value)


def _inspect_flow(values: Mapping[str, Any], depth: int) -> Optional[RiskAssessment]:
    if _truthy(values.get("check_only")):
        return RiskAssessment("flow_run", OperationRisk.SAFE,
                              "بررسی خشک جریان بدون اجرا", "Dry validation of flow")
    if depth > 4:
        return RiskAssessment("flow_run", OperationRisk.DESTRUCTIVE,
                              "جریان تودرتوی عمیق", "Deeply nested flow")
    ref = str(values.get("file") or values.get("flow") or "").strip()
    if not ref:
        return RiskAssessment("flow_run", OperationRisk.CONFIRM,
                              "اجرای جریان خودکار", "Automated flow execution")
    try:
        from . import flowkit
        from .workflows import Workflow

        snapshot = values.get("_flow_snapshot")
        workflow = Workflow.from_dict(snapshot) if isinstance(snapshot, dict) else flowkit.resolve(ref)
    except Exception:
        return RiskAssessment("flow_run", OperationRisk.CONFIRM,
                              "اجرای فایل جریان خارجی", "External flow file execution")
    highest = OperationRisk.SAFE
    reasons_fa: List[str] = []
    reasons_en: List[str] = []
    nested: List[str] = []
    for step in workflow.steps:
        nested.append(step.tool_id)
        sub = classify_invocation(step.tool_id, step.values, _depth=depth + 1)
        if sub.risk > highest:
            highest = sub.risk
            reasons_fa = [f"{step.tool_id}: {sub.reason_fa}"]
            reasons_en = [f"{step.tool_id}: {sub.reason_en}"]
    return RiskAssessment(
        "flow_run",
        highest,
        reasons_fa[0] if reasons_fa else "تمام گام‌های جریان ایمن هستند",
        reasons_en[0] if reasons_en else "All flow steps are safe",
        tuple(nested),
    )


def _walk_macro_steps(steps: Any, depth: int, nested: List[str]) -> Tuple[OperationRisk, str, str]:
    highest = OperationRisk.SAFE
    reason_fa = "تمام گام‌های ماکرو ایمن هستند"
    reason_en = "All macro steps are safe"
    if not isinstance(steps, list):
        return highest, reason_fa, reason_en
    for step in steps:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("type") or "")
        if kind == "run":
            tool_id = str(step.get("tool") or "")
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            if tool_id:
                nested.append(tool_id)
                sub = classify_invocation(tool_id, args, _depth=depth + 1)
                if sub.risk > highest:
                    highest = sub.risk
                    reason_fa = f"{tool_id}: {sub.reason_fa}"
                    reason_en = f"{tool_id}: {sub.reason_en}"
        elif kind in ("delete", "move", "copy", "mkdir", "write"):
            nested.append(f"macro:{kind}")
            risk = OperationRisk.DESTRUCTIVE if kind == "write" else OperationRisk.CONFIRM
            if risk > highest:
                highest = risk
                reason_fa = "تغییر فایل یا پوشه توسط ماکرو"
                reason_en = f"Macro file {kind} step"
        for branch_key in ("steps", "then", "else"):
            if isinstance(step.get(branch_key), list):
                sub_risk, sub_fa, sub_en = _walk_macro_steps(step[branch_key], depth, nested)
                if sub_risk > highest:
                    highest = sub_risk
                    reason_fa = sub_fa
                    reason_en = sub_en
    return highest, reason_fa, reason_en


def snapshot_invocation(tool_id: str, values: Mapping[str, Any]) -> Dict[str, Any]:
    """Freeze external macro/flow content for assessment and execution."""
    from copy import deepcopy

    result = deepcopy(dict(values))
    if tool_id == "macro_run" and str(result.get("file") or "").strip():
        if "_macro_snapshot" not in result:
            from .macro import MacroError, load_macro

            try:
                result["_macro_snapshot"] = load_macro(Path(str(result["file"])))
            except MacroError as exc:
                raise ToolError(str(exc)) from exc
    if tool_id == "flow_run" and "_flow_snapshot" not in result:
        ref = str(result.get("file") or result.get("flow") or "").strip()
        if ref:
            from . import flowkit

            try:
                result["_flow_snapshot"] = flowkit.resolve(ref).as_dict()
            except (OSError, LookupError, ValueError) as exc:
                raise ToolError(f"Could not load flow: {exc}") from exc
    return result


def _inspect_macro(values: Mapping[str, Any], depth: int) -> Optional[RiskAssessment]:
    if _truthy(values.get("dry_run")):
        return RiskAssessment("macro_run", OperationRisk.SAFE,
                              "پیش‌نمایش خشک ماکرو", "Macro dry-run preview")
    if depth > 4:
        return RiskAssessment("macro_run", OperationRisk.DESTRUCTIVE,
                              "ماکروی تودرتوی عمیق", "Deeply nested macro")
    try:
        from . import macro as macro_mod

        macro_obj = None
        builtin_raw = str(values.get("builtin") or "").strip()
        file_raw = str(values.get("file") or "").strip()
        if file_raw and isinstance(values.get("_macro_snapshot"), dict):
            macro_obj = values["_macro_snapshot"]
        elif file_raw:
            path = Path(file_raw)
            if path.is_file():
                macro_obj = macro_mod.load_macro(path)
        elif builtin_raw != "":
            templates = macro_mod.builtin_macros("fa")
            idx = int(builtin_raw)
            if 0 <= idx < len(templates):
                macro_obj = templates[idx]
        if not isinstance(macro_obj, dict):
            return RiskAssessment("macro_run", OperationRisk.CONFIRM,
                                  "اجرای ماکرو", "Macro execution")
        nested: List[str] = []
        highest, reason_fa, reason_en = _walk_macro_steps(macro_obj.get("steps"), depth, nested)
        return RiskAssessment("macro_run", highest, reason_fa, reason_en, tuple(nested))
    except Exception:
        return RiskAssessment("macro_run", OperationRisk.CONFIRM,
                              "اجرای فایل ماکرو", "Macro file execution")


def classify_invocation(
    spec_or_id: Union[ToolSpec, str],
    values: Optional[Mapping[str, Any]] = None,
    *,
    _depth: int = 0,
) -> RiskAssessment:
    """Classify `(tool, values)` into `SAFE`, `CONFIRM`, `DESTRUCTIVE`, or `ELEVATED`."""
    spec = get_tool(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
    if spec is None:
        tid = str(spec_or_id)
        return RiskAssessment(tid, OperationRisk.DESTRUCTIVE,
                              "ابزار ناشناخته", "Unknown tool")

    merged: Dict[str, Any] = spec.defaults()
    if values:
        merged.update(values)
    tid = spec.id
    action = str(merged.get("action") or "").strip().lower()
    mode = str(merged.get("mode") or "").strip().lower()

    # 1. Explicit dry-run / preview flags render read-only simulations SAFE.
    if tid in {"temp_clean", "folder_sync"} and _truthy(merged.get("dry_run")):
        return RiskAssessment(tid, OperationRisk.SAFE,
                              "حالت پیش‌نمایش بدون تغییر", "Dry-run preview without changes")

    # 2. Nested orchestrators & journal undo.
    if tid == "flow_run":
        flow_res = _inspect_flow(merged, _depth)
        if flow_res is not None:
            return flow_res
    if tid == "macro_run":
        macro_res = _inspect_macro(merged, _depth)
        if macro_res is not None:
            return macro_res
    if tid == "undo_last":
        # The implementation clamps even zero/negative input to at least one undo.
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "بازگردانی تغییرات فایل‌سیستم از ژورنال",
            "Reverts recorded filesystem changes from the journal",
        )

    # 3. Dynamic per-tool action overrides before static fallbacks.
    # Preview-capable file organizers mutate existing user files when applied.
    if tid in ("batch_rename", "organize_files", "rename_by_date"):
        if not _truthy(merged.get("preview", True)):
            return RiskAssessment(tid, OperationRisk.CONFIRM,
                                  "تغییر یا جابه‌جایی فایل‌های موجود",
                                  "Renames, moves or copies existing user files")
        return RiskAssessment(tid, OperationRisk.SAFE, "پیش‌نمایش فایل‌ها", "File preview")

    if tid in ("dark_mode", "set_wallpaper"):
        return RiskAssessment(tid, OperationRisk.CONFIRM,
                              "تغییر تنظیمات ظاهر ویندوز", "Changes Windows appearance settings")

    if tid == "ocr_setup":
        if action == "set_path":
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "تغییر مسیر فایل اجرایی Tesseract و ذخیره‌ی دائمی آن",
                "Persists and executes a custom Tesseract binary path",
            )
        if action == "download":
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "دانلود داده‌ی زبان OCR از شبکه در پوشه‌ی برنامه",
                "Downloads OCR language data into the application folder",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "بررسی وضعیت OCR", "OCR status check")

    if tid in ("junk_scan", "empty_file_finder", "empty_folders"):
        if _truthy(merged.get("delete")):
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "حذف مستقیم فایل‌ها یا پوشه‌ها از دیسک",
                "Permanently deletes matching files or folders",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "اسکن بدون حذف", "Read-only scan")

    if tid == "folder_sync":
        if mode == "mirror":
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "همگام‌سازی آینه‌ای فایل‌های اضافی مقصد را حذف می‌کند",
                "Mirror sync deletes extra files in the target directory",
            )
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "به‌روزرسانی و بازنویسی فایل‌ها در پوشه‌ی مقصد",
            "Copies and overwrites older files in the target directory",
        )

    if tid == "storage_report":
        if _truthy(merged.get("purge_snapshots")):
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "پاک‌سازی دائمی نسخه‌های پشتیبان بازگردانی (Snapshots)",
                "Permanently purges undo snapshot backups",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "گزارش فضای مصرفی", "Storage report")

    if tid == "installed_apps":
        if action == "uninstall":
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "حذف نصب برنامه‌ی ویندوز",
                "Uninstalls an installed Windows application",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "فهرست برنامه‌ها", "Application list")

    if tid == "scheduled_tasks":
        if action == "delete":
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "حذف وظیفه‌ی زمان‌بندی‌شده‌ی ویندوز",
                "Deletes a Windows Scheduled Task",
            )
        if action == "run":
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "اجرای فوری وظیفه‌ی زمان‌بندی‌شده‌ی ویندوز",
                "Triggers a Windows Scheduled Task immediately",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "فهرست وظایف", "Scheduled task list")

    if tid == "env_vars":
        if action == "remove":
            return RiskAssessment(
                tid, OperationRisk.DESTRUCTIVE,
                "حذف متغیر محیطی کاربر",
                "Removes a user environment variable",
            )
        if action == "set":
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "تغییر متغیر محیطی کاربر",
                "Modifies a user environment variable",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "مشاهده‌ی متغیر محیطی", "Read environment variables")

    if tid == "startup_add":
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "افزودن فرمان به اجرای خودکار هنگام ورود به ویندوز",
            "Registers a command to run automatically at Windows sign-in",
        )

    if tid == "create_shortcut":
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "ساخت میان‌بر در پوشه‌های ویندوز (از جمله اجرای خودکار)",
            "Creates a shortcut in Windows folders, potentially including Startup",
        )

    if tid == "sendto_install":
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "تغییر منوی SendTo اکسپلورر ویندوز",
            "Modifies Windows Explorer SendTo shortcuts",
        )

    if tid == "process_manager":
        if action == "kill":
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "بستن اجباری پردازش در حال اجرا",
                "Terminates a running OS process",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "مشاهده‌ی پردازش‌ها", "Process listing")

    if tid == "winget_tool":
        if action in ("install", "upgrade"):
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "نصب یا ارتقای بسته با winget",
                "Installs or upgrades software packages via winget",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "جست‌وجو/فهرست winget", "Read-only winget query")

    if tid == "wifi_passwords":
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "نمایش رمزهای ذخیره‌شده‌ی شبکه‌های Wi-Fi",
            "Reads saved cleartext Wi-Fi credentials from the OS",
        )

    if tid == "bitlocker_status":
        if action == "recovery":
            return RiskAssessment(
                tid, OperationRisk.ELEVATED,
                "خواندن کلیدهای بازیابی BitLocker نیازمند دسترسی مدیر است",
                "Reading BitLocker recovery keys requires Administrator privileges",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "وضعیت BitLocker", "BitLocker status check")

    if tid in ("restart_explorer", "font_install", "vault_password"):
        return RiskAssessment(
            tid, OperationRisk.CONFIRM,
            "تغییر وضعیت پوسته، فونت‌های سیستم یا کلید گاوصندوق",
            "Modifies Windows Explorer shell, installed fonts, or vault master key",
        )

    if tid == "flatten_folder":
        if not _truthy(merged.get("preview", True)):
            return RiskAssessment(
                tid, OperationRisk.CONFIRM,
                "انتقال تمام فایل‌های زیرپوشه‌ها به پوشه‌ی اصلی",
                "Moves all nested files into the root folder",
            )
        return RiskAssessment(tid, OperationRisk.SAFE, "پیش‌نمایش تخت‌سازی", "Flatten preview")

    # Multi-action admin tools where list/view is strictly read-only when spec.danger is False.
    if tid in ("windows_features", "power_plan", "startup_manager") and action == "list":
        return RiskAssessment(tid, OperationRisk.SAFE, "مشاهده‌ی فهرست بدون تغییر", "Read-only listing")
    if tid == "driver_problems":
        return RiskAssessment(tid, OperationRisk.SAFE, "بررسی وضعیت درایورها", "Read-only driver inspection")

    # 4. Static ToolSpec flags (`spec.danger`, `spec.admin`).
    if spec.danger and spec.admin:
        return RiskAssessment(
            tid, OperationRisk.ELEVATED,
            "عملیات حساس سیستمی نیازمند دسترسی مدیر (UAC) و تأیید کاربر",
            "Privileged destructive system operation requiring Administrator (UAC) access",
        )
    if spec.danger:
        return RiskAssessment(
            tid, OperationRisk.DESTRUCTIVE,
            "عملیات مخرب یا غیرقابل بازگشت روی فایل‌ها یا سیستم",
            "Destructive or irreversible operation on files or system state",
        )
    if spec.admin:
        return RiskAssessment(
            tid, OperationRisk.ELEVATED,
            "عملیات نیازمند دسترسی مدیر ویندوز (Administrator)",
            "Operation requires Windows Administrator privileges",
        )

    return RiskAssessment(tid, OperationRisk.SAFE, "عملیات ایمن", "Safe operation")


def classify_steps(
    steps: Iterable[Tuple[str, Optional[Mapping[str, Any]]]],
) -> Tuple[OperationRisk, List[RiskAssessment]]:
    """Evaluate a sequence of `(tool_id, values)` pairs and return `(max_risk, non_safe_items)`."""
    highest = OperationRisk.SAFE
    flagged: List[RiskAssessment] = []
    for tool_id, step_values in steps:
        assessment = classify_invocation(tool_id, step_values)
        if assessment.risk > highest:
            highest = assessment.risk
        if assessment.requires_confirmation:
            flagged.append(assessment)
    return highest, flagged


class ExecutionService:
    """Centralized execution coordinator for all WinGenius surfaces."""

    def execute(
        self,
        spec_or_id: Union[ToolSpec, str],
        values: Optional[Dict[str, Any]] = None,
        ctx: Optional[RunContext] = None,
        *,
        policy: Optional[ExecutionPolicy] = None,
        title: str = "",
    ) -> RunResult:
        spec = get_tool(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
        lang = ctx.lang if ctx is not None else "fa"
        if spec is None:
            tid = str(spec_or_id)
            raise ToolError(f"unknown tool: {tid}" if lang == "en" else f"ابزار پیدا نشد: {tid}")
        if spec.run is None:
            raise ToolError(
                f"Tool '{spec.id}' has no implementation."
                if lang == "en"
                else f"ابزار «{spec.name}» پیاده‌سازی اجرایی ندارد."
            )

        run_ctx = ctx or RunContext(lang=lang)
        merged: Dict[str, Any] = spec.defaults()
        if values:
            merged.update(values)

        # Register every file/dir input of this invocation as protected: output
        # helpers (save_target, …) refuse to write onto them, so a tool can
        # never overwrite its own source by accident (src == out).
        protected: List[str] = []
        for f in spec.fields:
            if f.kind in (K_FILE, K_FILES):
                raw = merged.get(f.key)
                items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split("|")
                protected.extend(str(p).strip() for p in items if str(p).strip())
        if protected:
            run_ctx.protected_inputs = sorted(set(protected) | set(getattr(run_ctx, "protected_inputs", [])))

        outer_policy = getattr(run_ctx, "policy", None)
        if policy is None:
            effective = (
                outer_policy
                if isinstance(outer_policy, ExecutionPolicy)
                else ExecutionPolicy(source="direct", max_risk=OperationRisk.ELEVATED,
                                     confirmed=True, record_history=False)
            )
        else:
            effective = policy.merge_outer(
                outer_policy if isinstance(outer_policy, ExecutionPolicy) else None
            )

        merged = snapshot_invocation(spec.id, merged)
        assessment = classify_invocation(spec, merged)
        if not effective.allows(assessment.risk):
            reason = assessment.reason_for(run_ctx.lang)
            tier = assessment.risk.label_for(run_ctx.lang)
            msg = run_ctx.M(
                f"اجرای «{spec.name}» مسدود شد ({tier}): {reason}",
                f"Execution of '{spec.name_en}' blocked by policy ({tier}): {reason}",
            )
            log.warning(
                "policy denied tool=%s source=%s risk=%s",
                spec.id, effective.source, assessment.risk.slug,
            )
            raise PolicyDeniedError(msg, assessment)

        if effective.dry_run:
            safe_preview = sanitize_values(spec.id, merged)
            tier = assessment.risk.label_for(run_ctx.lang)
            summary = run_ctx.M(
                f"[پیش‌نمایش خشک] {spec.name} — سطح ریسک: {tier}",
                f"[DRY-RUN] {spec.name_en} — risk tier: {tier}",
            )
            details = ", ".join(f"{k}={v!r}" for k, v in safe_preview.items() if v not in ("", None, []))
            return RunResult(message=summary, details=details)

        secrets = secret_strings(spec.id, merged)
        previous_policy = getattr(run_ctx, "policy", None)
        run_ctx.policy = effective
        started = time.perf_counter()
        status = "done"
        err_text = ""
        result: Optional[RunResult] = None

        try:
            run_ctx.check_cancel()
            result = spec.run(run_ctx, merged)
            if not isinstance(result, RunResult):
                result = RunResult(message=str(result or ""))
            return result
        except Cancelled:
            status = "cancelled"
            raise
        except PolicyDeniedError:
            status = "denied"
            raise
        except ToolError as exc:
            status = "failed"
            cleaned = scrub_text(redact_known(str(exc), secrets))
            err_text = cleaned
            if cleaned != str(exc):
                raise ToolError(cleaned) from exc
            raise
        except Exception as exc:
            status = "failed"
            cleaned = scrub_text(redact_known(str(exc), secrets))
            err_text = f"{type(exc).__name__}: {cleaned}"
            log.error(
                "tool crashed tool=%s source=%s err=%s",
                spec.id, effective.source, err_text,
            )
            raise
        finally:
            run_ctx.policy = previous_policy
            elapsed = max(0.0, time.perf_counter() - started)
            log.info(
                "exec tool=%s source=%s risk=%s status=%s elapsed=%.3fs",
                spec.id, effective.source, assessment.risk.slug, status, elapsed,
            )
            if effective.record_history and status in ("done", "failed"):
                try:
                    from .history import history

                    msg = (result.message if result is not None else err_text) or ""
                    msg = scrub_text(redact_known(msg, secrets))
                    history.add(SimpleNamespace(
                        tool_id=spec.id,
                        title=title or spec.name_for(run_ctx.lang),
                        status=status,
                        elapsed=elapsed,
                        outputs=list(result.outputs) if result is not None else [],
                        message=msg[:400],
                        error=err_text[:400],
                        values=merged,
                    ))
                except Exception as hist_exc:  # pragma: no cover
                    log.debug("history record skipped: %s", hist_exc)


execution_service = ExecutionService()


def execute_tool(
    spec_or_id: Union[ToolSpec, str],
    values: Optional[Dict[str, Any]] = None,
    ctx: Optional[RunContext] = None,
    *,
    policy: Optional[ExecutionPolicy] = None,
    title: str = "",
) -> RunResult:
    """Module-level helper delegating to `execution_service.execute`."""
    return execution_service.execute(spec_or_id, values, ctx, policy=policy, title=title)
