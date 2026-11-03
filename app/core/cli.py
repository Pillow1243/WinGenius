"""Headless command-line interface — usable in scripts, Task Scheduler, CI.

    WinGenius.exe --list
    WinGenius.exe --list --json --category pdf
    WinGenius.exe --describe pdf_to_images
    WinGenius.exe --run pdf_to_images --file "C:\\in.pdf" --set fmt=jpg --set dpi=150
    WinGenius.exe --run images_to_pdf --file a.png --file b.png --out "C:\\out\\x.pdf"
    WinGenius.exe --doctor
    WinGenius.exe --check-update        (opt-in: ask GitHub about the newest release)
    WinGenius.exe --list-workflows
    WinGenius.exe --run-workflow pdf_to_images_zip --file in.pdf
    WinGenius.exe --plan "عکس‌ها را PDF کن" --file a.png --file b.png --run
    WinGenius.exe --list-schedules
    WinGenius.exe --run-schedule <id>
    WinGenius.exe --undo                 (reverse the last journaled operation)
    WinGenius.exe --macros               (list the macro library)
    WinGenius.exe --run-macro pack.json [--set folder=C:/in] [--dry-run]
    WinGenius.exe --serve [--port 8770]  (local JSON API)
    WinGenius.exe --install-sendto       (add "Send to -> WinGenius" to Explorer)
    WinGenius.exe --remove-sendto        (take it back out)
    WinGenius.exe --help                 (this text)

Exit codes: 0 success, 1 tool failure, 2 usage error.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import get_logger, setup_logging
from .registry import CATEGORIES, all_tools, get as get_tool, load_all, tools_in
from .settings import settings
from .spec import K_BOOL, K_DIR, K_FILE, K_FILES, K_FLOAT, K_INT, K_SAVE, RunContext, ToolError

log = get_logger("cli")

CLI_FLAGS = {"--list", "--run", "--describe", "--doctor", "--self-test", "--version",
             "--list-workflows", "--run-workflow", "--plan", "--list-schedules",
             "--run-schedule", "--undo", "--macros", "--run-macro", "--serve", "--port",
             "--help", "-h", "--install-sendto", "--remove-sendto", "--check-update"}


def wants_cli(argv: List[str]) -> bool:
    return any(flag in argv for flag in CLI_FLAGS) or "--cli" in argv


def _usage() -> str:
    return __doc__ or "WinGenius CLI"


def _print(text: str = "") -> None:
    """Write one line to stdout, tolerating a closed pipe (`... | head`)."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        # A frozen windowed exe may inherit a cp1252 console instead of UTF-8.
        # UnicodeEncodeError is a ValueError, so the handler below used to
        # swallow it and silently discard the whole `--doctor` report.
        safe = (text + "\n").encode("ascii", "backslashreplace").decode("ascii")
        try:
            sys.stdout.write(safe)
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
    except (BrokenPipeError, ValueError):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    options: Dict[str, Any] = {"files": [], "sets": [], "out": "", "category": "",
                               "json": False, "lang": "fa", "quiet": False, "title": ""}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--list":
            options["mode"] = "list"
        elif arg == "--describe":
            options["mode"] = "describe"
            index += 1
            options["tool"] = argv[index] if index < len(argv) else ""
        elif arg == "--doctor":
            options["mode"] = "doctor"
        elif arg == "--check-update":
            options["mode"] = "check-update"
        elif arg == "--version":
            options["mode"] = "version"
        elif arg == "--list-workflows":
            options["mode"] = "workflows"
        elif arg == "--run-workflow":
            options["mode"] = "run-workflow"
            index += 1
            options["workflow"] = argv[index] if index < len(argv) else ""
        elif arg == "--plan":
            options["mode"] = "plan"
            index += 1
            options["plan"] = argv[index] if index < len(argv) else ""
        elif arg == "--run":
            options["mode"] = "run"
            index += 1
            options["run"] = argv[index] if index < len(argv) else ""
        elif arg == "--list-schedules":
            options["mode"] = "schedules"
        elif arg == "--run-schedule":
            options["mode"] = "run-schedule"
            index += 1
            options["schedule"] = argv[index] if index < len(argv) else ""
        elif arg == "--undo":
            options["mode"] = "undo"
        elif arg == "--macros":
            options["mode"] = "macros"
        elif arg == "--run-macro":
            options["mode"] = "run-macro"
            index += 1
            options["macro"] = argv[index] if index < len(argv) else ""
        elif arg == "--install-sendto":
            options["mode"] = "sendto-install"
        elif arg == "--remove-sendto":
            options["mode"] = "sendto-remove"
        elif arg in ("--help", "-h"):
            options["mode"] = "help"
        elif arg == "--serve":
            options["mode"] = "serve"
        elif arg == "--port":
            index += 1
            try:
                options["port"] = int(argv[index]) if index < len(argv) else 8770
            except ValueError:
                options["port"] = 8770
        elif arg == "--self-test":
            options["mode"] = "self-test"
        elif arg == "--json":
            options["json"] = True
        elif arg == "--dry-run":
            # parsed like every other flag: peeking at sys.argv meant an embedded
            # caller (or a wrapper script) silently *executed* what it asked to
            # dry-run.
            options["dry_run"] = True
        elif arg == "--quiet":
            options["quiet"] = True
        elif arg == "--lang":
            index += 1
            options["lang"] = argv[index] if index < len(argv) else "fa"
        elif arg == "--category":
            index += 1
            options["category"] = argv[index] if index < len(argv) else ""
        elif arg == "--file":
            index += 1
            if index < len(argv):
                options["files"].append(argv[index])
        elif arg == "--out":
            index += 1
            options["out"] = argv[index] if index < len(argv) else ""
        elif arg == "--set":
            index += 1
            if index < len(argv):
                key, _, value = argv[index].partition("=")
                options["sets"].append((key.strip(), value.strip()))
        elif arg == "--title":
            index += 1
            options["title"] = argv[index] if index < len(argv) else ""
        index += 1
    options.setdefault("mode", "list")
    return options


def _coerce(field, raw: str) -> Any:
    if field is None:
        return raw
    if field.kind in (K_INT,):
        return int(float(raw))
    if field.kind in (K_FLOAT,):
        return float(raw)
    if field.kind == K_BOOL:
        return raw.strip().lower() in ("1", "true", "yes", "on", "بله")
    return raw


def _build_values(spec, files: List[str], sets: List[tuple], out: str) -> Dict[str, Any]:
    values: Dict[str, Any] = spec.defaults()
    file_fields = [f for f in spec.fields if f.kind in (K_FILE, K_FILES)]
    if files and file_fields:
        target = file_fields[0]
        values[target.key] = files if target.kind == K_FILES else files[0]
    for key, raw in sets:
        field = spec.field(key)
        if field is None:
            continue
        if field.kind == K_FILES and "," in raw:
            values[key] = [part.strip() for part in raw.split(",") if part.strip()]
        else:
            values[key] = _coerce(field, raw)
    if out:
        for field in spec.fields:
            if field.kind in (K_SAVE, K_DIR) and not values.get(field.key):
                values[field.key] = out
                break
    return values


def run_cli(argv: List[str]) -> int:
    setup_logging()
    from . import upgrade

    try:
        upgrade.migrate()
    except Exception:                        # a broken migration must not block the CLI
        pass
    options = _parse_args(argv)
    mode = options["mode"]
    load_all()

    if mode == "version":
        from .paths import APP_NAME, APP_VERSION

        _print(f"{APP_NAME} {APP_VERSION}")
        return 0

    if mode == "list":
        rows = []
        for category in CATEGORIES:
            if category.key == "home":
                continue
            if options["category"] and category.key != options["category"]:
                continue
            for spec in tools_in(category.key):
                rows.append({
                    "id": spec.id,
                    "category": spec.category,
                    "name": spec.name_for(options["lang"]),
                    "desc": spec.desc_for(options["lang"]),
                    "danger": spec.danger,
                    "windows_only": spec.windows_only,
                })
        if options["json"]:
            _print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            width = max((len(row["id"]) for row in rows), default=10)
            current = ""
            for row in rows:
                if row["category"] != current:
                    current = row["category"]
                    _print(f"\n[{current}]")
                flag = " !" if row["danger"] else ""
                _print(f"  {row['id']:<{width}}  {row['name']}{flag}")
            _print(f"\n{len(rows)} tools")
        return 0

    if mode == "describe":
        spec = get_tool(options.get("tool", ""))
        if spec is None:
            _print(f"unknown tool: {options.get('tool')}")
            return 2
        payload = {
            "id": spec.id, "category": spec.category,
            "name": spec.name, "name_en": spec.name_en,
            "desc": spec.desc, "desc_en": spec.desc_en,
            "danger": spec.danger, "windows_only": spec.windows_only,
            "fields": [
                {"key": f.key, "kind": f.kind, "label": f.label, "label_en": f.label_en,
                 "required": f.required, "default": f.default,
                 "choices": [{"value": c[0], "fa": c[1], "en": c[2]} for c in f.choices]}
                for f in spec.fields
            ],
        }
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if mode == "doctor":
        from .diagnostics import diagnostics_text, doctor_issues

        _print(diagnostics_text())
        issues = doctor_issues()
        if issues:
            _print("\nIssues to fix:")
            for issue in issues:
                _print(f"  ! {issue}")
        return 0

    if mode == "check-update":
        # Explicitly opt-in: no background network traffic, ever.
        from .updates import check_latest

        result = check_latest()
        if options["json"]:
            _print(json.dumps(result, ensure_ascii=False, indent=2))
        elif not result.get("ok"):
            _print(f"could not check for updates: {result.get('error')}")
        elif result.get("update_available"):
            _print(f"a newer release is available: {result['latest']} "
                   f"(installed: {result['current']})\n{result['url']}")
        else:
            _print(f"WinGenius is up to date ({result['current']})")
        return 0 if result.get("ok") else 1

    if mode == "self-test":
        # handled by the GUI bootstrap
        return 0

    # ------------------------------------------------------- v4: schedules etc.
    if mode == "schedules":
        from .scheduler import describe_cron, store as schedule_store

        rows = []
        for item in schedule_store.all():
            nxt = item.next_at()
            rows.append({"id": item.id, "name": item.title(options["lang"]), "cron": item.cron,
                         "kind": item.kind, "target": item.target_id, "enabled": item.enabled,
                         "runs": item.runs, "last_status": item.last_status,
                         "next": nxt.isoformat(timespec="minutes") if nxt else ""})
        if options["json"]:
            _print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                mark = "✓" if row["enabled"] else "·"
                _print(f"{mark} {row['id']:<12} {row['cron']:<16} {row['name']}  →  "
                       f"{row['kind']}: {row['target']}  · next {row['next'] or '—'}")
            _print(f"\n{len(rows)} schedules")
        return 0

    if mode == "run-schedule":
        from .scheduler import store as schedule_store
        from .workflows import run_sync

        item = schedule_store.get(str(options.get("schedule") or ""))
        if item is None:
            _print(f"unknown schedule: {options.get('schedule')}")
            return 2
        if item.kind != "workflow":
            from .registry import get as _get

            spec = _get(item.target_id)
            if spec is None:
                _print(f"unknown tool: {item.target_id}")
                return 2
            ctx = RunContext(lang=options["lang"], output_dir=options["out"] or settings.output_dir)
            try:
                result = spec.run(ctx, dict(item.payload or {}))
            except Exception as exc:                    # noqa: BLE001
                schedule_store.mark_run(item.id, "failed")
                _print(f"failed: {exc}")
                return 1
            schedule_store.mark_run(item.id, "done")
            _print(getattr(result, "message", "") or "done")
            for output in (getattr(result, "outputs", []) or [])[:20]:
                _print(f"  → {output}")
            return 0
        from .workflows import store as workflow_store

        workflow = workflow_store.get(item.target_id)
        if workflow is None:
            _print(f"unknown workflow: {item.target_id}")
            return 2
        result = run_sync(workflow, options["lang"], options["out"] or settings.output_dir,
                          dict(item.payload or {}))
        schedule_store.mark_run(item.id, result.status)
        _print(result.message or result.status)
        for step in result.steps:
            _print(f"  {step.status:<8} {step.tool_id}")
        return 0 if result.ok else 1


    if mode == "help":
        _print(_usage())
        return 0

    if mode == "undo":
        from .journal import journal

        results = journal().undo_last(1)
        if not results:
            _print("nothing to undo")
            return 0
        item = results[0]
        _print(f"#{item['id']} {item['status']}: {item['operation'].title(options['lang'])}")
        for path in item["restored"][:20]:
            _print(f"  ↺ {path}")
        for problem in item["problems"][:10]:
            _print(f"  ! {problem}")
        return 0 if item["status"] != "failed" else 1

    if mode in ("sendto-install", "sendto-remove"):
        from .spec import RunContext as _RunContext

        sendto_tool = get_tool("sendto_install")
        if sendto_tool is None:
            _print("sendto_install tool is missing")
            return 2
        sendto_action = "install" if mode == "sendto-install" else "remove"
        try:
            result = sendto_tool.run(_RunContext(lang=options["lang"]), {"action": sendto_action})
        except ToolError as exc:
            _print(str(exc))
            return 1
        _print(result.message)
        if options.get("json"):
            _print(json.dumps({"ok": True, "action": sendto_action, "message": result.message},
                              ensure_ascii=False))
        return 0

    if mode == "macros":
        from .macro import library, load_macro

        paths = library()
        rows = []
        for path in paths:
            try:
                data = load_macro(path)
            except Exception:
                data = {}
            rows.append({"file": str(path), "name": data.get("name", path.stem),
                         "steps": len(data.get("steps", []) or [])})
        if options["json"]:
            _print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                _print(f"{row['name']:<40} {row['steps']} steps  ·  {row['file']}")
            _print(f"\n{len(rows)} macros")
        return 0


    if mode == "run-macro":
        from . import macro as macro_module

        target = str(options.get("macro") or "").strip()
        if not target:
            _print("usage: --run-macro <file.json> [--set name=value] [--dry-run]")
            return 2
        path = Path(target)
        if not path.exists():
            matches = [item for item in macro_module.library() if item.stem == target]
            if not matches:
                _print(f"macro not found: {target}")
                return 2
            path = matches[0]
        try:
            macro = macro_module.load_macro(path)
        except macro_module.MacroError as exc:
            _print(f"invalid macro: {exc}")
            return 2
        known = [spec.id for spec in all_tools()]
        problems = macro_module.validate(macro, known, options["lang"])
        if problems:
            for problem in problems:
                _print(f"! {problem}")
            return 2
        variables = {key: raw for key, raw in options["sets"]}
        dry_run = bool(options.get("dry_run"))
        output_dir = options["out"] or settings.output_dir
        runner = macro_module.MacroRunner(lang=options["lang"], output_dir=output_dir,
                                          dry_run=dry_run,
                                          on_log=(None if options["quiet"] else
                                                  lambda line: sys.stderr.write(f"  {line}\n")))
        result = runner.execute(macro, variables)
        if options["json"]:
            _print(json.dumps({"status": result.status, "executed": result.executed,
                               "outputs": result.outputs, "errors": result.errors,
                               "message": result.message(options["lang"])},
                              ensure_ascii=False))
        else:
            _print(result.message(options["lang"]))
            for output in result.outputs[:20]:
                _print(f"  → {output}")
            for problem in result.errors[:10]:
                _print(f"  ! {problem}")
        return 0 if result.status in ("done", "dry-run") else 1

    if mode == "serve":
        from .api_server import serve_blocking

        return serve_blocking(int(options.get("port") or 8770), lang=options["lang"])

    # -------------------------------------------------------------- workflows
    if mode == "workflows":
        from .workflows import store as workflow_store

        rows = [{"id": wf.id, "name": wf.name_for(options["lang"]), "source": wf.source,
                 "steps": [step.tool_id for step in wf.steps]} for wf in workflow_store.all()]
        if options["json"]:
            _print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                _print(f"{row['id']:<22} {row['name']}  →  " + " + ".join(row["steps"]))
            _print(f"\n{len(rows)} workflows")
        return 0

    if mode == "plan":
        from .planner import build_plan, describe

        workflow = build_plan(str(options.get("plan") or ""), options["files"], options["lang"])
        if workflow is None:
            _print("no plan matched that sentence")
            return 2
        if not options.get("run_flag", True) or options.get("dry_run"):
            _print(f"{workflow.name_for(options['lang'])} ({len(workflow.steps)} steps)")
            _print(describe(workflow, options["lang"]))
            return 0
        options["workflow"] = workflow.id
        mode = "run-workflow"
        plan_workflow = workflow

    if mode == "run-workflow":
        from .workflows import run_sync, store as workflow_store

        workflow = locals().get("plan_workflow") or workflow_store.get(str(options.get("workflow") or ""))
        if workflow is None:
            _print(f"unknown workflow: {options.get('workflow')}")
            return 2
        if options.get("dry_run"):
            _print(f"{workflow.name_for(options['lang'])} ({len(workflow.steps)} steps)")
            from .planner import describe

            _print(describe(workflow, options["lang"]))
            return 0
        overrides: Dict[str, Any] = {}
        for key, raw in options["sets"]:
            overrides[key] = raw
        base = {"__paths__": list(options["files"])}
        base.update(overrides)
        output_dir = options["out"] or settings.output_dir
        progress = (lambda index, step: (
            None if options["quiet"] else sys.stderr.write(
                f"  [{index + 1}] {step.tool_id}: {step.status}\n") or sys.stderr.flush()))
        result = run_sync(workflow, options["lang"], output_dir, base, on_step=progress)
        if options["json"]:
            _print(json.dumps({
                "workflow": workflow.id, "status": result.status, "message": result.message,
                "steps": [{"tool": step.tool_id, "status": step.status, "message": step.message}
                          for step in result.steps],
                "outputs": result.outputs,
            }, ensure_ascii=False, indent=2))
        else:
            _print(result.message or result.status)
            for step in result.steps:
                _print(f"  {step.status:<8} {step.tool_id}" + (f"  ({step.message[:60]})" if step.message else ""))
            for output in result.outputs[:20]:
                _print(f"  → {output}")
        return 0 if result.ok else 1

    # ---------------------------------------------------------------- run tool
    tool_id = options.get("run", "")
    from .i18n import set_language

    set_language(options["lang"])
    spec = get_tool(tool_id)
    if spec is None:
        _print(f"unknown tool: {tool_id}\n\n{_usage()}")
        return 2
    values = _build_values(spec, options["files"], options["sets"], options["out"])

    def on_progress(current: int, total: int, message: str) -> None:
        if options["quiet"]:
            return
        if total:
            sys.stderr.write(f"\r  {min(100, int(current * 100 / max(1, total))):3d}%  {message[:60]:<60}")
        else:
            sys.stderr.write(f"\r  {message[:70]:<70}")
        sys.stderr.flush()

    def on_message(message: str) -> None:
        if not options["quiet"]:
            sys.stderr.write(f"\r    · {message[:80]:<80}\n")

    ctx = RunContext(lang=options["lang"], output_dir=options["out"] or settings.output_dir,
                     on_progress=on_progress, on_message=on_message)
    try:
        result = spec.run(ctx, values) if spec.run else None
    except ToolError as exc:
        _print(f"FAILED: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("cli tool crashed")
        _print(f"CRASHED: {type(exc).__name__}: {exc}")
        return 1
    if not options["quiet"]:
        sys.stderr.write("\r" + " " * 90 + "\r")
    if options["json"]:
        _print(json.dumps({"message": result.message if result else "",
                           "outputs": result.outputs if result else [],
                           "details": result.details if result else ""},
                          ensure_ascii=False, indent=2))
    else:
        if result:
            _print(result.message or "done")
            for output in result.outputs:
                _print(f"  → {output}")
    return 0
