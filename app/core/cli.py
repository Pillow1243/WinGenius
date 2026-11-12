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
    WinGenius.exe --flows                     (built-in + saved flows)
    WinGenius.exe --run-flow <id|name|file.json> --file in.pdf [--out DIR] [--lang en]
    WinGenius.exe --lint-flow <id|name|file.json>  (validate without running)
    WinGenius.exe --explain-flow <id|name|file.json>
    WinGenius.exe --plan "عکس‌ها را PDF کن" --file a.png --file b.png --run
    WinGenius.exe --list-schedules
    WinGenius.exe --run-schedule <id>
    WinGenius.exe --undo                 (reverse the last journaled operation)
    WinGenius.exe --macros               (list the macro library)
    WinGenius.exe --run-macro pack.json [--set folder=C:/in] [--dry-run]
    WinGenius.exe --serve [--port 8770]  (local JSON API)
    WinGenius.exe --install-sendto --yes (add the Explorer SendTo shortcut)
    WinGenius.exe --remove-sendto --yes  (remove the shortcut)
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
             "--help", "-h", "--install-sendto", "--remove-sendto", "--check-update",
             "--update", "--rollback-update",
             "--flows", "--run-flow", "--lint-flow", "--explain-flow"}


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
                               "json": False, "lang": "fa", "quiet": False, "title": "",
                               "yes": False, "dry_run": False, "download_only": False,
                               "allow_unsigned": False, "run_flag": False}
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
        elif arg == "--update":
            options["mode"] = "update"
        elif arg == "--rollback-update":
            options["mode"] = "rollback-update"
        elif arg == "--download-only":
            options["download_only"] = True
        elif arg == "--allow-unsigned":
            options["allow_unsigned"] = True
        elif arg == "--version":
            options["mode"] = "version"
        elif arg == "--list-workflows":
            options["mode"] = "workflows"
        elif arg == "--flows":
            options["mode"] = "flows"
        elif arg in ("--run-flow", "--lint-flow", "--explain-flow"):
            options["mode"] = {"--run-flow": "run-flow", "--lint-flow": "lint-flow",
                               "--explain-flow": "explain-flow"}[arg]
            index += 1
            options["flow"] = argv[index] if index < len(argv) else ""
        elif arg == "--run-workflow":
            options["mode"] = "run-workflow"
            index += 1
            options["workflow"] = argv[index] if index < len(argv) else ""
        elif arg == "--plan":
            if options.get("mode") == "run" and options.get("run") == "":
                options["run_flag"] = True
            options["mode"] = "plan"
            index += 1
            options["plan"] = argv[index] if index < len(argv) else ""
        elif arg == "--run":
            # In plan mode --run is a switch, not a tool ID.
            if options.get("mode") == "plan":
                options["run_flag"] = True
            else:
                options["mode"] = "run"
                if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                    index += 1
                    options["run"] = argv[index]
                else:
                    options["run"] = ""
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
            options["dry_run"] = True
        elif arg in ("--yes", "-y"):
            options["yes"] = True
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
    options.setdefault("warnings", [])
    return options


class CliUsageError(Exception):
    """A `--set`/`--file` combination the tool cannot use. Exit code 2, no traceback."""


def _coerce(field, raw: str) -> Any:
    """Turn one `--set key=value` string into the type the field declares.

    A typo used to escape as a raw `ValueError` traceback; now every conversion
    failure becomes one readable line that names the field, the value and the
    expected type.  Ranges and choice lists are checked here too, so a script
    fails loudly instead of silently running with a clamped/ignored value.
    """
    if field is None:
        return raw
    text = raw.strip()
    if field.kind == K_INT:
        try:
            value: Any = int(float(text))
        except (TypeError, ValueError):
            raise CliUsageError(f"{field.key}: '{raw}' is not a whole number") from None
    elif field.kind == K_FLOAT:
        try:
            value = float(text)
        except (TypeError, ValueError):
            raise CliUsageError(f"{field.key}: '{raw}' is not a number") from None
    elif field.kind == K_BOOL:
        value = text.lower() in ("1", "true", "yes", "on", "بله", "بلی")
    else:
        value = raw
    if field.kind in (K_INT, K_FLOAT):
        low, high = float(field.minimum or 0), float(field.maximum or 0)
        if high > low and not (low <= float(value) <= high):
            raise CliUsageError(
                f"{field.key}: {value} is outside {field.minimum:g}…{field.maximum:g}"
            )
    if field.kind == "choice" and field.choices:
        allowed = {str(item[0]) for item in field.choices}
        if str(value) not in allowed:
            raise CliUsageError(
                f"{field.key}: '{raw}' is not one of " + ", ".join(sorted(allowed))
            )
    return value


def _suggest_key(spec, key: str) -> str:
    """A cheap 'did you mean' for `--set` keys (typos are the common case)."""
    keys = [f.key for f in spec.fields]
    lowered = key.lower()
    for candidate in keys:
        if candidate.lower() == lowered:
            return candidate
    for candidate in keys:
        if lowered in candidate.lower() or candidate.lower() in lowered:
            return candidate
    return ""


def _build_values(spec, files: List[str], sets: List[tuple], out: str,
                  warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Merge defaults, `--file`, `--out` and `--set` into the tool's value dict.

    Anything the tool cannot use is reported in `warnings` instead of being
    dropped silently: `--set out_dir=…` used to be accepted (and ignored) for a
    field whose real key was `out`, which looked like the tool ignoring the flag.
    """
    warnings = warnings if warnings is not None else []
    values: Dict[str, Any] = spec.defaults()
    file_fields = [f for f in spec.fields if f.kind in (K_FILE, K_FILES)]
    if files and file_fields:
        target = file_fields[0]
        values[target.key] = files if target.kind == K_FILES else files[0]
    elif files:
        warnings.append(
            f"--file ignored: '{spec.id}' has no file field "
            f"(fields: {', '.join(f.key for f in spec.fields) or 'none'})"
        )
    for key, raw in sets:
        field = spec.field(key)
        if field is None:
            hint = _suggest_key(spec, key)
            warnings.append(
                f"--set {key} ignored: no such field"
                + (f" — did you mean '{hint}'?" if hint else "")
            )
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
        else:
            if not any(f.kind in (K_SAVE, K_DIR) for f in spec.fields):
                warnings.append(f"--out ignored: '{spec.id}' writes into its own output folder")
    return values


def run_cli(argv: List[str]) -> int:
    options = _parse_args(argv)
    if not options["dry_run"]:
        setup_logging()
        from . import upgrade

        try:
            upgrade.migrate()
        except Exception:                    # a broken migration must not block the CLI
            pass
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
            if result.get("body"):
                _print("\nRelease notes:\n" + str(result["body"])[:1500])
        else:
            _print(f"WinGenius is up to date ({result['current']})")
        return 0 if result.get("ok") else 1

    if mode == "update":
        from .updates import (
            UpdateError,
            apply_verified_update,
            download_and_verify_update,
            fetch_latest_release,
            version_key,
        )
        from .paths import APP_VERSION

        try:
            release = fetch_latest_release()
            if version_key(release.version) <= version_key(APP_VERSION):
                payload = {"ok": True, "updated": False, "current": APP_VERSION, "latest": release.version}
                _print(json.dumps(payload, indent=2) if options["json"]
                       else f"WinGenius is already up to date ({APP_VERSION}).")
                return 0
            if options["dry_run"]:
                payload = {
                    "ok": True, "dry_run": True, "current": APP_VERSION,
                    "latest": release.version, "url": release.html_url,
                }
                _print(json.dumps(payload, indent=2) if options["json"]
                       else f"[DRY-RUN] Update available: {APP_VERSION} -> {release.version}")
                return 0
            pkg = download_and_verify_update(
                release,
                require_signature=not bool(options["allow_unsigned"]),
            )
            if options["download_only"]:
                payload = {
                    "ok": True, "downloaded": str(pkg.file_path), "version": pkg.version,
                    "sha256": pkg.verification.sha256,
                    "cryptographically_verified": pkg.verification.cryptographically_verified,
                }
                _print(json.dumps(payload, indent=2) if options["json"]
                       else f"Verified update saved: {pkg.file_path} (SHA-256: {pkg.verification.sha256})")
                return 0
            if not options["yes"]:
                _print(
                    f"Verified WinGenius {pkg.version} ({pkg.file_path.name}, "
                    f"SHA-256: {pkg.verification.sha256[:16]}…). "
                    "Re-run with --yes to confirm installation or --download-only to stage."
                )
                return 2
            applied = apply_verified_update(pkg, silent_installer=True)
            _print(json.dumps(applied, indent=2) if options["json"]
                   else f"Update {pkg.version} launched ({applied.get('mode')}).")
            return 0
        except UpdateError as exc:
            if options["json"]:
                _print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            else:
                _print(f"update failed: {exc}")
            return 1

    if mode == "rollback-update":
        from .updates import UpdateError, rollback_previous_version

        if not options["yes"] and not options["dry_run"]:
            _print("Pass --yes to confirm rolling back to the previous backed-up executable.")
            return 2
        if options["dry_run"]:
            from .updates import get_rollback_state

            st = get_rollback_state() or {}
            _print(json.dumps({"ok": bool(st), "dry_run": True, "state": st}, indent=2))
            return 0 if st else 1
        try:
            res = rollback_previous_version()
            _print(json.dumps(res, indent=2) if options["json"]
                   else f"Rolled back to version {res.get('previous_version')}: {res.get('restored_exe')}")
            return 0
        except UpdateError as exc:
            _print(json.dumps({"ok": False, "error": str(exc)}) if options["json"]
                   else f"rollback failed: {exc}")
            return 1

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
        from .execution import ExecutionPolicy, execution_service
        from .scheduler import store as schedule_store
        from .workflows import run_sync

        item = schedule_store.get(str(options.get("schedule") or ""))
        if item is None:
            _print(f"unknown schedule: {options.get('schedule')}")
            return 2
        dry_run = bool(options.get("dry_run"))
        # An explicit CLI invocation must obey CLI confirmation and preview policy.
        sched_policy = ExecutionPolicy.for_cli(yes=bool(options.get("yes")), dry_run=dry_run)

        def mark_schedule(status: str) -> None:
            if not dry_run:
                schedule_store.mark_run(item.id, status)

        if item.kind == "macro":
            from . import macro as macro_module

            target = str(item.target_id or "").strip()
            path = Path(target)
            if not path.exists():
                matches = [m for m in macro_module.library() if m.stem == target or m.name == target]
                if matches:
                    path = matches[0]
            if not path.exists():
                mark_schedule("failed")
                _print(f"unknown macro: {target}")
                return 2
            try:
                macro_doc = macro_module.load_macro(path)
                runner = macro_module.MacroRunner(
                    lang=options["lang"],
                    output_dir=options["out"] or settings.output_dir,
                    dry_run=bool(options.get("dry_run")),
                    policy=sched_policy,
                )
                mres = runner.execute(macro_doc, dict(item.payload or {}))
            except Exception as exc:                    # noqa: BLE001
                mark_schedule("failed")
                _print(f"failed: {exc}")
                return 1
            ok = mres.status in ("done", "dry-run")
            mark_schedule("done" if ok else "failed")
            _print(mres.message(options["lang"]))
            return 0 if ok else 1
        if item.kind != "workflow":
            from .registry import get as _get

            spec = _get(item.target_id)
            if spec is None:
                _print(f"unknown tool: {item.target_id}")
                return 2
            ctx = RunContext(lang=options["lang"], output_dir=options["out"] or settings.output_dir,
                             policy=sched_policy)
            try:
                result = execution_service.execute(spec, dict(item.payload or {}), ctx,
                                                   policy=sched_policy)
            except Exception as exc:                    # noqa: BLE001
                mark_schedule("failed")
                _print(f"failed: {exc}")
                return 1
            mark_schedule("done")
            _print(getattr(result, "message", "") or "done")
            for output in (getattr(result, "outputs", []) or [])[:20]:
                _print(f"  → {output}")
            return 0
        from .workflows import store as workflow_store

        workflow = workflow_store.get(item.target_id)
        if workflow is None:
            _print(f"unknown workflow: {item.target_id}")
            return 2
        try:
            result = run_sync(workflow, options["lang"], options["out"] or settings.output_dir,
                              dict(item.payload or {}), policy=sched_policy)
        except Exception as exc:  # report policy denials and unexpected workflow failures
            mark_schedule("failed")
            _print(f"failed: {exc}")
            return 1
        mark_schedule(result.status)
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
        from .execution import ExecutionPolicy, PolicyDeniedError, execution_service
        from .spec import RunContext as _RunContext

        sendto_tool = get_tool("sendto_install")
        if sendto_tool is None:
            _print("sendto_install tool is missing")
            return 2
        sendto_action = "install" if mode == "sendto-install" else "remove"
        policy = ExecutionPolicy.for_cli(yes=bool(options.get("yes")))
        try:
            result = execution_service.execute(
                sendto_tool, {"action": sendto_action}, _RunContext(lang=options["lang"]),
                policy=policy,
            )
        except PolicyDeniedError as exc:
            _print(str(exc))
            return 2
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
        from .execution import ExecutionPolicy, PolicyDeniedError

        cli_policy = ExecutionPolicy.for_cli(yes=bool(options.get("yes")), dry_run=dry_run)
        runner = macro_module.MacroRunner(lang=options["lang"], output_dir=output_dir,
                                          dry_run=dry_run,
                                          policy=cli_policy,
                                          on_log=(None if options["quiet"] else
                                                  lambda line: sys.stderr.write(f"  {line}\n")))
        try:
            result = runner.execute(macro, variables)
        except PolicyDeniedError as exc:
            _print(f"DENIED: {exc} (pass --yes to confirm)")
            return 1
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
        if not options.get("run_flag", False) or options.get("dry_run"):
            if options["json"]:
                _print(json.dumps({
                    "id": workflow.id,
                    "name": workflow.name_for(options["lang"]),
                    "dry_run": True,
                    "steps": [{"tool": s.tool_id, "values": dict(s.values)} for s in workflow.steps],
                }, ensure_ascii=False, indent=2))
            else:
                _print(f"{workflow.name_for(options['lang'])} ({len(workflow.steps)} steps)")
                _print(describe(workflow, options["lang"]))
            return 0
        options["workflow"] = workflow.id
        mode = "run-workflow"
        plan_workflow = workflow

    if mode in ("flows", "run-flow", "lint-flow", "explain-flow"):
        from . import flowkit
        from .execution import ExecutionPolicy, PolicyDeniedError
        from .workflows import run_sync

        if mode == "flows":
            flows = flowkit.library()
            if options["json"]:
                _print(json.dumps([{"id": flow.id, "name": flow.name, "name_en": flow.name_en,
                                    "steps": len(flow.steps), "source": flow.source,
                                    "advanced": sum(1 for step in flow.steps if step.advanced)}
                                   for flow in flows], ensure_ascii=False, indent=2))
            else:
                _print(flowkit.describe_library(options["lang"]))
            return 0
        reference = str(options.get("flow") or "").strip()
        try:
            flow = flowkit.resolve(reference)
        except (LookupError, FileNotFoundError, ValueError) as exc:
            _print(f"unknown flow: {reference or '—'} ({type(exc).__name__})")
            return 2
        report = flowkit.validate(flow, options["lang"])
        if mode == "lint-flow":
            _print(report.text(options["lang"]))
            return 0 if report.ok else 1
        if mode == "explain-flow":
            _print(flowkit.explain(flow, options["lang"]))
            return 0
        if not report.ok:
            _print(report.text(options["lang"]))
            return 2
        base = {"__paths__": list(options["files"])}
        for key, raw in options["sets"]:
            base[key] = raw
        output_dir = options["out"] or settings.output_dir
        cli_policy = ExecutionPolicy.for_cli(
            yes=bool(options.get("yes")), dry_run=bool(options.get("dry_run"))
        )
        progress = (lambda index, step: (
            None if options["quiet"] else sys.stderr.write(
                f"  [{index + 1}] {step.tool_id}: {step.status}\n") or sys.stderr.flush()))
        try:
            result = run_sync(flow, options["lang"], output_dir, base,
                              on_step=progress, policy=cli_policy)
        except PolicyDeniedError as exc:
            _print(f"DENIED: {exc} (pass --yes to confirm)")
            return 1
        if options["json"]:
            _print(json.dumps({
                "flow": flow.id, "status": result.status, "message": result.message,
                "steps": [{"tool": step.tool_id, "status": step.status, "message": step.message,
                           "outputs": step.outputs} for step in result.steps],
                "outputs": result.outputs,
            }, ensure_ascii=False, indent=2))
        else:
            _print(result.message or result.status)
            for step in result.steps:
                _print(f"  {step.status:<8} {step.tool_id}"
                       + (f"  ({step.message[:60]})" if step.message else ""))
            for output in result.outputs[:20]:
                _print(f"  → {output}")
        return 0 if result.status in ("done", "partial") else 1

    if mode == "run-workflow":
        from .execution import ExecutionPolicy, PolicyDeniedError
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
        cli_policy = ExecutionPolicy.for_cli(yes=bool(options.get("yes")), dry_run=False)
        progress = (lambda index, step: (
            None if options["quiet"] else sys.stderr.write(
                f"  [{index + 1}] {step.tool_id}: {step.status}\n") or sys.stderr.flush()))
        try:
            result = run_sync(workflow, options["lang"], output_dir, base,
                              on_step=progress, policy=cli_policy)
        except PolicyDeniedError as exc:
            _print(f"DENIED: {exc} (pass --yes to confirm)")
            return 1
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
    from .execution import ExecutionPolicy, PolicyDeniedError, execution_service
    from .i18n import set_language

    set_language(options["lang"])
    spec = get_tool(tool_id)
    if spec is None:
        _print(f"unknown tool: {tool_id}\n\n{_usage()}")
        return 2
    try:
        values = _build_values(spec, options["files"], options["sets"], options["out"],
                               options["warnings"])
    except CliUsageError as exc:
        _print(f"bad value for --set: {exc}\n\n"
               f"fields of {spec.id}: " + ", ".join(f.key for f in spec.fields))
        return 2
    for warning in options["warnings"]:
        sys.stderr.write(f"warning: {warning}\n")

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

    cli_policy = ExecutionPolicy.for_cli(
        yes=bool(options.get("yes")), dry_run=bool(options.get("dry_run"))
    )
    ctx = RunContext(lang=options["lang"], output_dir=options["out"] or settings.output_dir,
                     on_progress=on_progress, on_message=on_message, policy=cli_policy)
    try:
        result = execution_service.execute(spec, values, ctx, policy=cli_policy)
    except PolicyDeniedError as exc:
        _print(f"DENIED: {exc} (pass --yes to confirm)")
        return 1
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
