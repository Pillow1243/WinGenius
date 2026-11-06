"""Flow tools — build, lint, explain, export and run multi-step automations.

A *flow* is the v6 workflow: a chain of tools that may loop over files, check a
condition, retry a flaky step and decide whether a failure is fatal.  These six
tools make flows usable without touching JSON by hand.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from ..core import flowkit
from ..core.registry import tool
from ..core.spec import (
    K_BOOL, K_CHOICE, K_DIR, K_FILE, K_FILES, K_SAVE, K_TEXT, Field, RunContext, RunResult, ToolError,
)
from ..core.utils import ensure_dir, unique_path


def _flow_ref(values: Dict[str, Any], ctx: RunContext):
    """Resolve the flow picking the 'file' field first, then the 'name' text."""
    raw_path = str(values.get("file") or "").strip()
    reference = str(values.get("flow") or "").strip()
    target = raw_path or reference
    if not target:
        raise ToolError(ctx.M(
            "یک جریان انتخاب کنید: یا فایل JSON آن را بدهید یا نام/شناسه‌اش را بنویسید.\n"
            "با ابزار «کتابخانه‌ی جریان‌ها» فهرست جریان‌های موجود را ببینید.",
            "Pick a flow: give its JSON file or type its name/id.\n"
            "Use 'Flow library' to list the available flows."))
    try:
        return flowkit.resolve(target)
    except FileNotFoundError:
        raise ToolError(ctx.M(f"فایل جریان پیدا نشد: {target}",
                              f"Flow file not found: {target}"))
    except LookupError:
        raise ToolError(ctx.M(
            f"جریانی با نام «{target}» پیدا نشد. با ابزار «کتابخانه‌ی جریان‌ها» فهرست را ببینید.",
            f"No flow named '{target}'. Run 'Flow library' to see the list."))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError(ctx.M(f"فایل جریان خوانده نشد: {exc}", f"Could not read the flow: {exc}"))


@tool(
    id="flow_run", category="flow", name="اجرای جریان چندمرحله‌ای",
    name_en="Run a multi-step flow",
    desc="یک جریان ساخته‌شده (حلقه‌دار، شرطی و با تلاش مجدد) را روی فایل‌ها یا یک پوشه اجرا می‌کند.",
    desc_en="Runs a stored flow — loops, conditions, retries included — over files or a folder.",
    icon="play", order=10,
    keywords=("flow", "run", "workflow", "pipeline", "جریان", "اجرا", "خط تولید", "زنجیره"),
    fields=[
        Field("flow", K_TEXT, "نام یا شناسه‌ی جریان", "Flow name or id",
              placeholder="photo_pdf_pack", help="خالی بگذارید و از فایل استفاده کنید.",
              help_en="Leave empty to use a JSON file instead."),
        Field("file", K_FILE, "یا فایل جریان", "or flow file", filt="Flow (*.json)"),
        Field("inputs", K_FILES, "ورودی‌ها", "Inputs",
              help="فایل‌ها یا پوشه‌ای که جریان روی آن کار می‌کند.",
              help_en="Files (or a folder) the flow works on."),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
        Field("check_only", K_BOOL, "فقط بررسی کن، اجرا نکن", "Check only, do not run"),
    ],
)
def flow_run(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    from ..core.workflows import run_sync

    snapshot = v.get("_flow_snapshot")
    if isinstance(snapshot, dict):
        from ..core.workflows import Workflow

        workflow = Workflow.from_dict(snapshot)
    else:
        workflow = _flow_ref(v, ctx)
    report = flowkit.validate(workflow, ctx.lang)
    if not report.ok:
        raise ToolError(ctx.M(
            "این جریان ایراد دارد و اجرا نشد:\n" + report.text(ctx.lang),
            "This flow has errors, so it was not run:\n" + report.text(ctx.lang)))
    inputs = [str(item) for item in (v.get("inputs") or []) if str(item).strip()]
    if not inputs and not v.get("check_only") and not any(step.foreach for step in workflow.steps):
        raise ToolError(ctx.M("حداقل یک فایل یا پوشه به‌عنوان ورودی بدهید.",
                              "Provide at least one file or folder as input."))
    out_dir = str(v.get("out_dir") or "").strip()
    if v.get("check_only"):
        return RunResult(
            message=ctx.M(f"جریان «{workflow.name_for(ctx.lang)}» سالم است "
                          f"({len(workflow.steps)} گام) و آماده‌ی اجراست.",
                          f"Flow '{workflow.name_for(ctx.lang)}' is valid "
                          f"({len(workflow.steps)} steps) and ready to run."),
            details=report.text(ctx.lang) + "\n\n" + flowkit.explain(workflow, ctx.lang))
    base = {"input": inputs[0] if inputs else "", "__paths__": inputs}
    result = run_sync(
        workflow,
        lang=ctx.lang,
        output_dir=out_dir,
        base_values=base,
        cancel_event=ctx._cancel,
        policy=getattr(ctx, "policy", None),
    )
    details = flowkit.explain(workflow, ctx.lang) + "\n\n"
    for step in result.steps:
        mark = {"done": "✓", "skipped": "–", "failed": "✖"}.get(step.status, "·")
        details += f"{mark} {step.tool_id}: {step.message}\n"
        for path in step.outputs:
            details += f"    → {path}\n"
    if result.status == "failed":
        raise ToolError(result.message or "flow failed")
    outputs = [path for step in result.steps for path in step.outputs]
    return RunResult(message=result.message, details=details, outputs=outputs)


@tool(
    id="flow_build", category="flow", name="ساخت خودکار جریان از یک پوشه",
    name_en="Auto-build a flow from a folder",
    desc="محتوای پوشه را تشخیص می‌دهد و یک جریان آماده (فشرده‌سازی، PDF، آرشیو، OCR…) برایش می‌سازد.",
    desc_en="Looks at what is inside a folder and writes a ready flow for it (shrink, PDF, archive, OCR…).",
    icon="sparkles", order=11,
    keywords=("build", "generate", "auto", "recipe", "ساخت", "خودکار", "دستور پخت", "جریان"),
    fields=[
        Field("folder", K_DIR, "پوشه", "Folder", required=True),
        Field("goal", K_CHOICE, "هدف", "Goal", default="auto",
              choices=[(key, fa, en) for key, fa, en in flowkit.GOALS]),
        Field("name", K_TEXT, "نام جریان", "Flow name",
              placeholder="جریان پوشه‌ی من", placeholder_en="My folder flow"),
        Field("save", K_SAVE, "ذخیره در", "Save to", filt="Flow (*.json)"),
    ],
)
def flow_build(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    folder = str(v.get("folder") or "").strip()
    if not folder or not Path(folder).is_dir():
        raise ToolError(ctx.M(f"پوشه پیدا نشد: {folder or '—'}",
                              f"Folder not found: {folder or '—'}"))
    goal = str(v.get("goal") or "auto")
    workflow = flowkit.build_from_folder(folder, goal, ctx.lang)
    name = str(v.get("name") or "").strip()
    if name:
        workflow.name = name
        workflow.name_en = name
    groups = flowkit.classify(folder)
    found = ", ".join(f"{key}: {len(items)}" for key, items in groups.items() if items)
    requested = str(v.get("save") or "").strip()
    target = Path(requested) if requested else flowkit.flows_dir() / f"{workflow.id}.json"
    if target.suffix.lower() != ".json":
        target = target / f"{workflow.id}.json"
    target = ensure_dir(target.parent) / target.name
    if target.exists():
        target = unique_path(target)
    flowkit.save_flow(workflow, target)
    return RunResult(
        message=ctx.M(
            f"جریان «{workflow.name_for(ctx.lang)}» با {len(workflow.steps)} گام ساخته شد.\n"
            f"پیدا شد → {found or '—'}",
            f"Flow '{workflow.name_for(ctx.lang)}' created with {len(workflow.steps)} steps.\n"
            f"Found → {found or '—'}"),
        details=flowkit.explain(workflow, ctx.lang),
        outputs=[str(target)])


@tool(
    id="flow_validate", category="flow", name="بررسی سلامت جریان",
    name_en="Lint a flow",
    desc="جریان را قبل از اجرا بازرسی می‌کند: ابزار ناشناخته، فیلد خالی، جای‌نگهدار اشتباه و شرط بی‌اثر.",
    desc_en="Checks a flow before it runs: unknown tools, empty required fields, bad placeholders, dead conditions.",
    icon="check", order=12,
    keywords=("validate", "lint", "check", "flow", "بررسی", "سلامت", "خطا", "جریان"),
    fields=[
        Field("flow", K_TEXT, "نام یا شناسه‌ی جریان", "Flow name or id"),
        Field("file", K_FILE, "یا فایل جریان", "or flow file", filt="Flow (*.json)"),
    ],
)
def flow_validate(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    workflow = _flow_ref(v, ctx)
    report = flowkit.validate(workflow, ctx.lang)
    if not report.ok:
        raise ToolError(report.text(ctx.lang))
    message = ctx.M(
        f"جریان سالم است: {len(workflow.steps)} گام، {len(report.warnings)} هشدار.",
        f"Flow is valid: {len(workflow.steps)} steps, {len(report.warnings)} warning(s).")
    return RunResult(message=message, details=report.text(ctx.lang))


@tool(
    id="flow_explain", category="flow", name="توضیح جریان به زبان آدم",
    name_en="Explain a flow in plain language",
    desc="گام‌به‌گام می‌گوید جریان چه می‌کند، روی چه فایل‌هایی، با چه شرطی و اگر خطا بیاید چه می‌شود.",
    desc_en="Says step by step what a flow does, on which files, under which condition and what happens on error.",
    icon="info", order=13,
    keywords=("explain", "describe", "flow", "توضیح", "شرح", "جریان", "چه کار می‌کند"),
    fields=[
        Field("flow", K_TEXT, "نام یا شناسه‌ی جریان", "Flow name or id"),
        Field("file", K_FILE, "یا فایل جریان", "or flow file", filt="Flow (*.json)"),
    ],
)
def flow_explain(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    workflow = _flow_ref(v, ctx)
    return RunResult(message=workflow.name_for(ctx.lang),
                     details=flowkit.explain(workflow, ctx.lang))


@tool(
    id="flow_export", category="flow", name="تبدیل جریان به اسکریپت پایتون",
    name_en="Export a flow as a Python script",
    desc="جریان را به یک فایل .py مستقل تبدیل می‌کند که از خط فرمان اجرا می‌شود (برای زمان‌بندی یا سرور).",
    desc_en="Turns a flow into a standalone .py runner you can call from cron/Task Scheduler or a server.",
    icon="code", order=14,
    keywords=("export", "script", "python", "flow", "خروجی", "اسکریپت", "پایتون", "جریان"),
    fields=[
        Field("flow", K_TEXT, "نام یا شناسه‌ی جریان", "Flow name or id"),
        Field("file", K_FILE, "یا فایل جریان", "or flow file", filt="Flow (*.json)"),
        Field("out_dir", K_DIR, "پوشه‌ی خروجی", "Output folder"),
    ],
)
def flow_export(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    workflow = _flow_ref(v, ctx)
    script = flowkit.export_script(workflow, ctx.lang, ctx.output_dir)
    folder = str(v.get("out_dir") or "").strip()
    base = ensure_dir(folder) if folder else ensure_dir(ctx.output_dir or Path.home() / "WinGenius")
    safe_id = "".join(char if char.isalnum() or char in "_-" else "_"
                      for char in (workflow.id or "flow"))
    target = unique_path(base / f"{safe_id}.py")
    target.write_text(script, encoding="utf-8")
    return RunResult(
        message=ctx.M(
            f"اسکریپت ساخته شد: {target.name} — با «python {target.name} <input>» اجرا می‌شود.",
            f"Script written: {target.name} — run it with 'python {target.name} <input>'."),
        details=script[:2000], outputs=[str(target)])


@tool(
    id="flow_library", category="flow", name="کتابخانه‌ی جریان‌ها",
    name_en="Flow library",
    desc="همه‌ی جریان‌های داخلی، کاربری و ساخته‌شده را با گام‌هایشان فهرست می‌کند.",
    desc_en="Lists every built-in, user and generated flow with its steps.",
    icon="layers", order=15,
    keywords=("library", "list", "flows", "workflows", "کتابخانه", "فهرست", "جریان"),
    fields=[Field("show_steps", K_BOOL, "گام‌ها هم نشان داده شود", "Also show the steps",
                  default=True)],
)
def flow_library(ctx: RunContext, v: Dict[str, Any]) -> RunResult:
    flows = flowkit.library()
    if not flows:
        raise ToolError(ctx.M("هیچ جریانی ثبت نشده است.", "No flow is registered yet."))
    show = bool(v.get("show_steps", True))
    lines: List[str] = []
    for flow in flows:
        advanced = sum(1 for step in flow.steps if step.advanced)
        badge = f" · {advanced} گام پیشرفته" if advanced and ctx.lang != "en" else (
            f" · {advanced} advanced step(s)" if advanced else "")
        lines.append(f"• {flow.name_for(ctx.lang)}  ({flow.id}, {len(flow.steps)} گام{badge})")
        if show:
            for index, step in enumerate(flow.steps, start=1):
                note = step.summary(ctx.lang)
                lines.append(f"    {index}. {step.title(ctx.lang)}" + (f"  — {note}" if note else ""))
    return RunResult(
        message=ctx.M(f"{len(flows)} جریان در دسترس است.", f"{len(flows)} flow(s) available."),
        details="\n".join(lines))
