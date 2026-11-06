# Architecture

WinGenius is a **declarative tool registry** with a PySide6 desktop shell. A tool
is one decorated function; the registry builds the form, the worker thread, the
CLI flags, the API route, the command-palette entry and the search keywords from
its `ToolSpec`.

```
main.py
  └─ app/bootstrap.py ── headless CLI fast-path first; QApplication, theme, logging, single instance
       ├─ app/core/cli.py         pure-Python headless flags (--run / --yes / --dry-run / --serve …)
       │    └─ app/core/execution.py  ExecutionService + 4-tier dynamic risk classifier & policy guard
       │         └─ app/core/registry.py   load_all() → 43 registered tool modules (45 files) / 292 ToolSpecs / 11 categories
       │              └─ app/core/spec.py    ToolSpec, Field, RunContext (+ policy), RunResult, ToolError
       └─ app/ui/main_window.py   shell + panels (Qt Widgets, offscreen-testable)
            ├─ app/core/worker.py     QThread execution via ExecutionService, cooperative cancel
            ├─ app/core/jobs.py       bounded parallel queue with per-job ExecutionPolicy
            ├─ app/core/journal.py    SQLite undo log + internal trash
            ├─ app/core/vault.py      WGV2 encrypted container (scrypt + AES-256-GCM, 0600 perms)
            ├─ app/core/indexer.py    SQLite FTS5 full-text index (escaped queries, secret exclusions)
            ├─ app/core/scheduler.py  cron expressions (tool, workflow & macro targets)
            ├─ app/core/macro.py      conditional DSL routed through ExecutionService
            ├─ app/core/workflows.py  flow engine: steps, {prev}, when/foreach/retries + policy guard
            ├─ app/core/flowkit.py    flow lint/explain/build/export (no Qt needed)
            ├─ app/core/api_server.py loopback JSON API (token, Origin CSRF guard, HTTP framing checks)
            └─ app/core/plugins.py    SHA-256 approved user .py plugins with trust/untrust lifecycle
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
* **Dynamic imports.** `registry.load_all()` imports the 43 modules listed in
  `app/tools/__init__.py`, guarded so a missing optional library degrades one
  module instead of crashing startup. Import failures are now reported in
  `--doctor`, in the GUI toast and as a doctor issue (previously silent).
* **Cancellation is cooperative.** `RunContext.progress()` raises `Cancelled`;
  long loops must call it (or `check_cancel()`). `ToolRunner.cancel()` only sets
  an `Event` — it cannot kill a blocking OS call.
* **Unified Execution & Dynamic Risk (`v7.1`).** Every caller (`ToolRunner`, `JobQueue`,
  `WorkflowRunner`, `MacroRunner`, `cli`, `api_server`, scheduler, and folder watcher) routes
  tool execution through `app/core/execution.py` (`ExecutionService`), which evaluates runtime
  parameters and nested `flow_run`/`macro_run` steps into four risk tiers (`safe`, `confirm`,
  `destructive`, `elevated`), enforces caller-specific `ExecutionPolicy` thresholds, prevents
  nested privilege escalation via `RunContext.policy`, and scrubs secrets from outputs, logs,
  and history.
* **Undo, not deletion.** Destructive tools journal their file operations;
  `vault_remove`, `pdf_delete_pages`, `trash_cleanup` and `services_manager` are
  additionally marked `danger=True` and ask for confirmation in the UI.

## Persian / RTL PDF engine (v7) — `app/core/persian_pdf.py`

One module owns everything that touches Persian text in PDFs; tools call it instead
of pypdf/ReportLab directly.

| Stage | What it does | Why |
|---|---|---|
| `glyph_map_from_font()` | Parses the embedded font (`post` names, reverse `cmap`, `GSUB` single/ligature lookups) into `gid → text` | Modern fonts shape letters into glyphs that are absent from `/ToUnicode`; readers then print them as Cyrillic (`ѫ ѱ ӳ`) |
| `repair_tounicode()` / `open_repaired()` | Merges the recovered mappings into each font's CMap in place (never replaces the stream) and reopens the document | Makes *any* reader — not only WinGenius — copy real letters |
| `extract_page()` | Line-first layout: rawdict chars → ZWNJ recovery from glyph overlap → legacy visual-order reorder by geometry → paragraphs/headings/tables/images by gap, size and alignment | MuPDF emits one block per line (or per bidi run) for Persian; pypdf returns visual order |
| `normalize_persian()` | Presentation forms → letters, `ي/ك → ی/ک`, mirrored brackets, smart ZWNJ (`می‌شود`, `کتاب‌ها`), digit policy | Consistent text for search, Word export and the index |
| `flow_html_pages()` / `insert_html()` / `stamp_text()` / `stamp_line()` | HarfBuzz-shaped HTML through MuPDF `Story`/`insert_htmlbox` with the bundled Vazirmatn family, then CMap repair on save | ReportLab's base fonts cannot shape Arabic script; diagonal stamps use `show_pdf_page(rotate=…)` because `insert_htmlbox` only rotates by 90° |
| DOCX helpers | `<w:bidi/>`, `<w:rtl/>`, `rFonts cs`, `bidiVisual` tables | Word needs explicit bidi flags; a Persian string in an LTR paragraph is still wrong |

Rules: never use pypdf to read Persian text; keep `normalize_forms=False` while
extracting (the geometry pass needs the shapes) and normalise only in outputs; every
PDF written with the bundled fonts must go through `save_pdf()`/`pdf_bytes()` so the
CMap repair runs. `tests/persian_pdf_test.py` pins all of this.

## Flow engine (v6)

A *flow* is a `Workflow` with `Step`s; nothing about it is UI-specific, so the
same object runs from the GUI, the CLI (`--run-flow`), the API and an exported
Python script.

| Control | Semantics |
|---|---|
| `foreach` | The step runs once per match of a path pattern resolved from the inputs. Inside the body, `{item}`, `{item_name}`, `{item_dir}`, `{item_ext}`, `{item_index}` and `{item_count}` are substituted. Brace patterns (`*.{jpg,png}`) and `**` recursion are supported. |
| `when` | A step-level guard. `{prev} endswith .pdf`, `{prev_all} not empty`, `2 < 10`. No `eval`: the expression is tokenised, both sides are substituted, numbers compare numerically, and `and`/`or` chains evaluate **left to right** (`a or b and c` is `(a or b) and c`). |
| `retries` | Re-runs a failed step N extra times. |
| `on_error` | `stop` (default) or `continue`, which marks the run `partial` instead of aborting. |

Step statuses are `done`, `failed`, `skipped` and the run status is `done`,
`partial` or `failed`; `WorkflowRunner` re-emits them as Qt signals so the panel
and the CLI report the same truth. `app/core/flowkit.py` is the stateless layer:
`validate()` (bilingual errors/warnings), `explain()`, `build_from_folder()`,
`export_script()`, `library()` (10 built-ins).

Two rules keep flows honest: a field whose value is *exactly* `{prev_all}` or
`{input_all}` becomes a list, not a string; and a flow never stores secrets —
password/secret fields are dropped when a flow is saved, so the user re-enters
them (see `core/privacy.py`).

## Data locations

`app/core/paths.py` is the only place that decides where data lives. Per-user by
default (`%APPDATA%\WinGenius` or `$XDG_CONFIG_HOME/WinGenius`); `--portable`,
`WINGENIUS_PORTABLE=1` or a `portable.flag` file switches to `./data`.
`app/core/upgrade.py` owns `DATA_VERSION = 3`, `schema.json` and idempotent,
backed-up migrations.

| File | Contents | Sensitivity |
|---|---|---|
| `history.json`, `workflows.json` | rerunnable values with sensitive inputs omitted | paths/prose may still be private |
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

`python scripts/stats.py --run` executes all seven suites, rewrites
`docs/stats.json` (the single source of truth for documented numbers) and
regenerates `docs/showcase.html`. `scripts/check_docs.py` and
`scripts/check_deps.py` run in CI so documentation and dependency drift fail the
build. Coverage is gated by `[tool.coverage.report] fail_under` in
`pyproject.toml`.

## Known limits (deliberate, not bugs)

* Plugins are arbitrary Python executed with `approved Python execution`; only load files you
  trust. Files require explicit SHA-256 approval; changed files are denied until re-approved. There is no sandbox or publisher-signature verification.
* The scheduler wakes on a `QTimer` and catches up **one** missed run per enabled
  schedule — it is not a Windows Service / Task Scheduler replacement.
* The FTS5 index stores plaintext excerpts; encrypting it would require a
  different storage design.
* The Windows build is unsigned (no code-signing certificate); SmartScreen may
  warn on first run.

## Post-audit execution fixes (now shipped in 6.0.0)

Each queued Job snapshots its language, output directory and deep-copied inputs.
The API refuses startup without a credential and denies destructive tools unless
explicitly enabled by its Python caller. GUI run/queue buttons share a safety gate.
`core/privacy.py` sanitises persistence/exports without changing runtime inputs;
replay requires credential re-entry. See `docs/hardening.md` for remaining limits.
