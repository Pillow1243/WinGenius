# Architecture

WinGenius is a **declarative tool registry** with a PySide6 desktop shell. A tool
is one decorated function; the registry builds the form, the worker thread, the
CLI flags, the API route, the command-palette entry and the search keywords from
its `ToolSpec`.

```
main.py
  └─ app/bootstrap.py ── QApplication, theme, logging, single instance
       ├─ app/core/cli.py         headless flags (--run / --list / --doctor / --serve …)
       │    └─ app/core/registry.py   load_all() → 32 tool modules / 254 ToolSpecs
       │         └─ app/core/spec.py    ToolSpec, Field, RunContext, RunResult, ToolError
       │              └─ _guarded():  language, logging, exception → ToolError
       └─ app/ui/main_window.py   shell + panels (Qt Widgets, offscreen-testable)
            ├─ app/core/worker.py     QThread execution, cooperative cancel via RunContext
            ├─ app/core/jobs.py       bounded parallel queue
            ├─ app/core/journal.py    SQLite undo log + internal trash
            ├─ app/core/vault.py      WGV2 encrypted container (scrypt + AES-256-GCM)
            ├─ app/core/indexer.py    SQLite FTS5 full-text index
            ├─ app/core/scheduler.py  cron expressions, QTimer + single catch-up run
            ├─ app/core/macro.py      small conditional DSL
            ├─ app/core/api_server.py loopback JSON API (token + rate limiter)
            └─ app/core/plugins.py    user .py files loaded with importlib
```

## Layers and rules

| Layer | Responsibility | Must not |
|---|---|---|
| `app/core/spec.py` | data model, `RunContext`, `ToolError` | import Qt or any tool |
| `app/core/registry.py` | discovery, registration, lookup | import tool modules eagerly |
| `app/tools/*.py` | one business operation each | touch the UI, or write outside `RunContext.output_dir` |
| `app/ui/*.py` | widgets, signals, settings | implement tool logic |
| `app/core/*.py` | services shared by UI/CLI/API | require a running `QApplication` |

Consequences worth remembering:

* **Tools never see a widget.** They receive a plain `dict` of values and a
  `RunContext(lang, output_dir, progress, cancel, log)`. Everything is testable
  headless with `QT_QPA_PLATFORM=offscreen`.
* **Dynamic imports.** `registry.load_all()` imports the 32 modules listed in
  `app/tools/__init__.py`, guarded so a missing optional library degrades one
  module instead of crashing startup. Import failures are now reported in
  `--doctor`, in the GUI toast and as a doctor issue (previously silent).
* **Cancellation is cooperative.** `RunContext.progress()` raises `Cancelled`;
  long loops must call it (or `check_cancel()`). `ToolRunner.cancel()` only sets
  an `Event` — it cannot kill a blocking OS call.
* **Undo, not deletion.** Destructive tools journal their file operations;
  `vault_remove`, `pdf_delete_pages`, `trash_cleanup` and `services_manager` are
  additionally marked `danger=True` and ask for confirmation in the UI.

## Data locations

`app/core/paths.py` is the only place that decides where data lives. Per-user by
default (`%APPDATA%\WinGenius` or `$XDG_CONFIG_HOME/WinGenius`); `--portable`,
`WINGENIUS_PORTABLE=1` or a `portable.flag` file switches to `./data`.
`app/core/upgrade.py` owns `DATA_VERSION = 2`, `schema.json` and idempotent,
backed-up migrations.

| File | Contents | Sensitivity |
|---|---|---|
| `settings.ini` | theme, language, history | **plaintext** — do not put secrets here |
| `journal.sqlite3` | undo operations, paths | plaintext file paths |
| `index.sqlite3` | FTS5 full text of indexed files | **plaintext excerpts** — use `index/exclude_folders` |
| `api_token.txt` | local API bearer token | mode `0600` since the hardening pass |
| `*.wgv` | vault | scrypt key, AES-256-GCM, authenticated header, AAD-bound payloads |

## Packaging

* `pyproject.toml` — PEP 621 metadata, setuptools build backend, `app*` only,
  SPDX licence, `wingenius` console script. `requirements.txt` mirrors
  `[project.dependencies]`; `scripts/check_deps.py` enforces it.
* `packaging/WinGenius.spec` — PyInstaller onefile; `collect_submodules` for the
  lazily imported engines (pyHanko, pytesseract) so the exe is not missing them.
* `packaging/installer.iss` — Inno Setup, real publisher/support URLs, licence page.
* `.github/workflows/build-windows.yml` — tag/manual builds, version sync, frozen
  exe smoke test, checksums, release upload with least-privilege permissions.

## Verification

`python scripts/stats.py --run` executes all six suites, rewrites
`docs/stats.json` (the single source of truth for documented numbers) and
regenerates `docs/showcase.html`. `scripts/check_docs.py` and
`scripts/check_deps.py` run in CI so documentation and dependency drift fail the
build. Coverage is gated by `[tool.coverage.report] fail_under` in
`pyproject.toml`.

## Known limits (deliberate, not bugs)

* Plugins are arbitrary Python executed with `exec_module`; only load files you
  trust. There is no signature verification.
* The scheduler wakes on a `QTimer` and catches up **one** missed run per enabled
  schedule — it is not a Windows Service / Task Scheduler replacement.
* The FTS5 index stores plaintext excerpts; encrypting it would require a
  different storage design.
* The Windows build is unsigned (no code-signing certificate); SmartScreen may
  warn on first run.
