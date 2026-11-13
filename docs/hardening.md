# Post-audit hardening — historical notes

> Historical document: the notes below describe the earlier 5.x hardening pass,
> not the current release. For current changes see `CHANGELOG.md` and `SECURITY.md`.
> The 7.1 source now has a shared `ExecutionService`, but its presence alone is
> not proof that every process boundary, blocking call, or resource writer is
> comprehensively supervised. The remaining limits listed below are not claims
> that those capabilities do not exist at all; they identify work not verified
> end-to-end here.

This historical document describes source changes and does **not** establish
that a particular Windows binary has been replaced, signed or independently tested.

## Changes

| Area | Behaviour |
|---|---|
| Secret persistence | Password fields, sensitive field metadata and common credential keys are omitted from history, saved workflow steps, schedules, watch rules and newly exported profile backups. |
| Encryption plaintext | `text_encrypt.text` and `vault_add.note_text` are private inputs, not replay data. |
| Replay | Runtime inputs are not changed. Persisted/exported recipes require credentials to be entered again. No credential manager or automatic secret storage is implied. |
| Legacy data | Data schema 3 scrubs known JSON profiles and readable local migration-snapshot JSON. A fresh migration snapshot is sanitised and does not copy `api_token.txt`. External backups are not rewritten. |
| Profile backup | SQLite backup errors abort rather than falling back to a raw copy that can omit WAL transactions. Failed profile backups are not published; migration and update preparation refuse to proceed. Malformed profile JSON is preserved privately without sanitization and can contain secrets. Individual SQLite snapshots do not make the whole profile an atomic snapshot. |
| Atomic persistence | JSON writes use private uniquely named temporary files, fsync, atomic replace and a per-resource cross-process lock. This serializes cooperating writes but does not make every read-modify-write store transactional. |
| Process supervision | `app.core.processes.run_process` rejects shell command strings, enforces timeouts/cancellation, and reaps process groups on POSIX; on Windows it uses a kill-on-close Job Object when permitted plus a bounded `taskkill /T` fallback. `run_cmd` and Tesseract discovery use it. Other direct subprocess call sites and arbitrary blocking OS calls are not yet all supervised. |
| Execution entry points | CLI and Settings SendTo operations go through `ExecutionService`; the public registry invocation helper also delegates correctly and defaults to denying risky work unless a caller supplies confirmation. |
| API credentials | Empty/whitespace credentials and credential I/O failure prevent startup. Clearing the token while running denies subsequent requests. The server binds only to IPv4 loopback. |
| API execution | Destructive tools are denied by default. A Python integrator must explicitly set `allow_dangerous=True` if necessary. Simultaneous tool execution is bounded (default 3), sockets time out and JSON request bodies must be objects. |
| API console | The credential file path is printed, not the full bearer credential. |
| GUI safety | Both Run and Run in background call the same confirmation gate. The default dialog answer is No. The existing user preference to disable confirmation is preserved. |
| Queue isolation | Jobs snapshot language, output directory and deep-copied values at submission. Later jobs/configuration do not change a waiting job's context. Submitting after shutdown raises an error. |
| Copied CLI | Generated commands omit secret values. Supply the necessary secret safely at execution time; a copied command alone may be incomplete. |
| Plugins | Newly discovered or modified Python files do not execute until explicitly approved. The approval records the exact path and SHA-256 hash. Changed already-loaded code requires restart after re-approval. |
| Plugin registration | Existing tool IDs cannot be overwritten through the registration API during plugin loading. A failed import rolls back partial registration. This is not a security sandbox. |
| Vault KDF | Attacker-controlled scrypt costs are checked against combined memory/work budgets before allocating the KDF. Normal UI presets remain supported. |
| Release gate | Python invokes and checks each quality command individually; an earlier failed native command cannot be hidden by a later successful PowerShell command. UI, regression tests and the coverage gate run before packaging. |

## User-visible compatibility changes

- Saved encrypted workflows that previously kept a password must be completed
  with fresh runtime credentials before running again. Unattended workflows that
  need passwords require a future secure credential-reference design; do not put
  the password back into JSON or macro source as a workaround.
- Review migrated settings and backup copies before sharing them. Sanitisation is
  **not a general data-loss-prevention system**: arbitrary prose, filenames,
  paths, macros, logs, damaged JSON rescue files and external archives can still
  contain private data. Known passwords echoed in history messages are redacted.
- In Settings → Plugins → Reload, approving a file grants it the same privileges
  as the app. Inspect its source first. Hash approval verifies the main file only,
  not its imported dependencies, and does not verify a publisher signature.
- The API no longer runs destructive tools with its default configuration.
- Vaults created outside the supported UI presets with extreme KDF parameters
  may now be refused rather than exhausting memory. The accepted budget is
  128 MiB of estimated scrypt working memory with a separate work ceiling.

## Validation

Install the runtime requirements and `pytest coverage` in an isolated environment.

```sh
python -m pytest tests/hardening_test.py -q
python -m pytest -q
python scripts/quality_gate.py
```

The hardening suite currently has 39 non-destructive cases: no production
profile, no real credentials, no external service, no real deletion-tool run.
The total in `docs/stats.json` counts this suite separately from the original
57 security assertions. The Windows-only paths still require Windows VM testing.

## Work intentionally not claimed as done

- Code signing, Defender/SmartScreen testing and a new Windows binary release.
- A process sandbox for arbitrary Python plugins.
- Credential Manager/DPAPI references and secure unattended secret resolution.
- Transactional read-modify-write coordination across every JSON store and file-output path; current sidecar locks protect cooperating writes and the vault's existing revision-checked update path.
- Migration of every direct subprocess caller and cancellation of arbitrary blocking OS calls; the new supervisor currently covers `run_cmd` and Tesseract discovery, with regression tests for real process trees on CI.
- Actual Windows 10/11 installer, upgrade, rollback, SmartScreen and clean-machine validation in a Windows VM.
- Encrypted search index, streaming large vaults or complete DLP for free text.
- Reproducible dependency lock/SBOM for a Windows artifact not built here.

Never post a real personal access token in a ticket, commit, log or chat. If one
has been exposed, revoke it and use a fresh, scoped credential through a secure
integration when publishing.
