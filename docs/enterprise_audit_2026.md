# WinGenius engineering audit — 2026-11-13

## Scope and baseline

This is a **partial, evidence-based audit**, not certification or a completed
individual review of every tool. Recovery and HTTP framing were prioritized;
other areas below remain a roadmap. The source of truth is this repository.

- Initial HEAD/recovery point: `1308458a755bfa1e81d291a2e9d38df7d5874540`
  (merge of PR #16), on `arena/2569d44b-wingenius`; working tree clean. GitHub
  default branch was `main` at the same SHA. The checkout is shallow. No branch
  was switched or merged as part of the baseline.
- Version 7.1.0; AGPL-3.0-or-later; Qt desktop, Python, SQLite and local services.
  PR #16 introduced shared process supervision and hardened execution/storage.
- GitHub had one open PR (#14, dependency updates with failing checks), no open
  issues, and tags through v7.1.0. Historical Arena branch heads were enumerated,
  not treated as safe to merge. Baseline CI run 43308715022 reported successful
  lint, type, package, statistics and Linux/Windows 3.12/3.13 test jobs; CodeQL
  run 43308714857 was successful. This is CI evidence, not real-device testing.
- Tree inventory: 104 Python files in `app`, 27 Python test files before this
  change; 43 registered tool modules. `scripts/stats.py --check` reports 292
  static decorators, 11 documented categories and 2,339 **historical checks
  reached** (including skips), not a current pytest pass count. Live independent
  inventory is blocked: without Qt, two modules fail to import and CLI JSON lists
  275 unique tools while exiting successfully. The CLI's `--list --json` is an
  existing machine-readable partial inventory, not proof of completeness here.
- Local environment: Linux, Python 3.11.2 (project supports 3.12/3.13), no Qt or
  runtime dependency set; initial repository approximately 30 MiB, 20 GiB free.
  An external test venv consumed about 65 MiB; generated caches were not tracked.
  Full pytest collection failed on missing PySide6. The initial commit is the
  recovery point; no changes were present to stash or overwrite.

## Confirmed findings and changes

| ID / priority | Root cause and impact | Reproduction and fix | Regression evidence |
|---|---|---|---|
| EA-01 / P0 | `app/core/upgrade.py`: SQLite backup errors fell back to copying only the main database, omitting committed WAL pages and potentially producing a falsely successful recovery snapshot. | Use a live WAL database or a corrupt DB. `safe_copy_sqlite` now uses a read-only source and closes both connections; failures propagate instead of raw-copying. | `test_snapshot_includes_committed_wal`, `test_snapshot_failure_does_not_fall_back_to_raw_copy`, `test_failed_backup_is_not_published_or_used_for_migration`. |
| EA-02 / P0 | `app/core/upgrade.py`, `app/core/updates.py`: failed backups could leave partial directories; malformed JSON was represented as an empty backup; updates could proceed without a profile snapshot. | Place malformed history or corrupt SQLite in a synthetic profile. Backups now stage privately and publish only after copying succeeds, ignore nested symlinks, preserve malformed JSON privately and use unique names. Migration and update preparation fail closed when backup fails. | `test_malformed_profile_is_preserved_in_backup_and_rescue`, `test_failed_backup_is_not_published_or_used_for_migration`, `test_consecutive_backups_have_distinct_names`, `test_update_preparation_aborts_if_backup_fails`. |
| EA-03 / P1 | `app/core/upgrade.py`, `app/bootstrap.py`: reported migration problems or a failed, non-atomic stamp write could still be reported as success; a no-step failure was summarized as up to date. | Inject a migration problem or a disk-full stamp failure. Stop without advancing the stamp; write the stamp atomically and report failures to the caller/log. | `test_migration_step_problem_does_not_advance_stamp`, `test_migration_stamp_failure_is_reported`, `test_failed_backup_is_not_published_or_used_for_migration`. |
| EA-04 / P1 | `app/core/api_server.py`: denied/rate-limited requests and GET requests could leave a body unread on an HTTP/1.1 persistent connection; duplicate lengths were accepted. The remaining bytes could be parsed as another request. | Send a rejected POST followed by a request encoded as its body, or duplicate/malformed lengths. Close on rejected requests, reject ambiguous lengths and nonempty GET bodies. Normal keep-alive is retained. | `test_rejected_request_cannot_pipeline_its_unread_body`, `test_ambiguous_or_unconsumed_framing_closes_connection`, `test_normal_keep_alive_still_works`. |
| EA-05 / P1, open | `app/core/cli.py`: `load_all()` failures are ignored. On this environment `--list --json` exits 0 with 275 tools after `automation_tools` and `auto_tools` fail because PySide6 is missing. Consumers may mistake a partial catalog for the complete registry. | Run `python main.py --list --json` without PySide6 and compare to `load_all()` failures and `scripts/stats.py --check`. Proposed: explicitly report import failures without corrupting JSON and define CLI exit semantics; test missing and broken modules. | Reproduced locally; not fixed here. |
| EA-06 / P0 release blocker, open | `app/core/updates.py`: pinned Ed25519 release key is a placeholder and no Authenticode publisher thumbprint is pinned. Release authorization requires owner-controlled keys. | `python packaging/verify_package.py` reports two warnings; `keyring_status()` reports `ready=False`. Pin a real owner public key and verify the matching secret in protected release infrastructure; do not generate a substitute identity. | Local packaging verification and baseline CI; no signed binary was tested. |

The snapshot stages use directory rename to distinguish completed from incomplete
backups, but do **not** form one transaction across files or databases. A crash
can leave a `.incomplete-*` directory that is not used or pruned automatically.
Known sensitive fields in valid profile JSON are intentionally removed and API tokens excluded; malformed JSON is copied without sanitization and can contain secrets;
backup is therefore not a byte-for-byte credential recovery mechanism. Symlinks
are intentionally omitted. Windows junction/reparse-point behavior and hostile
races are not proven safe. No profile-wide concurrent-writer lock was added.
Migrations do not automatically restore a backup after partial steps. The app may
continue startup after migration failure. These are not crash-recovery guarantees.

## Security and architecture assessment

`ExecutionService` provides dynamic risk classification and caller policies;
workflow/macro propagation and denial handling exist in source. Targeted existing
entrypoint and process-supervision tests were run locally, but every execution
entry point, every runtime-dependent risk, and nested substitution were **not**
exhaustively audited. Direct subprocess use remains beyond the supervisor's
coverage. Plugins execute approved Python in-process with the user's privileges;
SHA-256 approval is not sandboxing, publisher verification or isolation. The API
is loopback/token-based, not an OS-level authorization boundary; this change does
not bound all connection threads. Backup and undo do not imply all destructive
operations can be recovered. CI actions use mutable version tags and remain a
supply-chain hardening item. Dependency vulnerability scanning and SBOM were
not performed.

## Verification and remaining roadmap

- Local (unsupported 3.11, synthetic data): 15 targeted recovery/framing tests
  passed independently of the repository's Qt-dependent session fixtures; 8
  existing entrypoint/process tests passed the same way. Full `pytest -q`
  **blocked during collection** by missing PySide6; it is not a pass.
- Ruff, compileall, dependency-list sync, static stats, documentation checks
  (42/42), packaging-input checks (30/30 with the two signing warnings), and
  release consistency checks ran successfully. These do not build or exercise
  a Windows installer. Coverage was not measured; the existing 70% gate was not
  lowered. Type checking, supported-version local tests, wheel/build and installer
  execution were not performed locally.
- Windows baseline CI passed on `windows-latest` Python 3.12/3.13; OS marketing
  version, installation/uninstallation, upgrade, UAC, real devices, abrupt
  shutdown and post-change packaging were not established by that baseline.
- No performance profiling, startup/memory/CPU or before/after comparison was
  performed. No visual or accessibility review of the UI was possible here.

Recommended next work: P0 verify real crash/restart, backup restoration, disk-full,
concurrent-instance and interrupted-update behavior on Windows; eliminate partial
migration/restore hazards. P1 make registry loading failures visible, enumerate
and individually audit all 292 tools with actual runtime schemas, validate all
policy entry points and run the full matrix on the proposed patch. P2 review Qt
UI/accessibility and localization, profile before optimization, assess bounded
queues and process coverage, pin and verify action SHAs. P3 consider product
features only after safety and usability evidence. No new cloud service is needed.

## Release assessment

**NO-GO for an enterprise or signed release.** The signing identity is not ready,
profile recovery is not end-to-end verified, comprehensive tool/security/UI
review remains incomplete, and this environment cannot run the supported Windows
matrix. CI green alone would not change those conclusions. No release was made.
