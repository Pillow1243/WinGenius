# Post-audit hardening — historical notes

> Historical document: the notes below describe the earlier 5.x hardening pass,
> not the current release. For current changes see `CHANGELOG.md` and `SECURITY.md`.
> In particular, `ExecutionService` now exists; cancellation, resource locking,
> sandboxing, encrypted indexing and Windows end-to-end validation remain open.
> Cooperating vault writers now use a sidecar lock and stale-write detection;
> this does not yet cover output files or the journal.

These changes are on the development branch. They do **not** mean that the
published Windows 5.0.1 binary has been replaced, signed or independently tested.

## Changes

| Area | Behaviour |
|---|---|
| Secret persistence | Password fields, sensitive field metadata and common credential keys are omitted from history, saved workflow steps, schedules, watch rules and newly exported profile backups. |
| Encryption plaintext | `text_encrypt.text` and `vault_add.note_text` are private inputs, not replay data. |
| Replay | Runtime inputs are not changed. Persisted/exported recipes require credentials to be entered again. No credential manager or automatic secret storage is implied. |
| Legacy data | Data schema 3 scrubs known JSON profiles and readable local migration-snapshot JSON. A fresh migration snapshot is sanitised and does not copy `api_token.txt`. External backups are not rewritten. |
| Atomic persistence | History/workflows use private uniquely named temporary files, fsync and replace. This does not make multi-process updates transactional. |
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
- A single ExecutionService/policy across CLI, workflows and scheduler.
- Cross-process/per-resource locking for vaults, outputs and journal updates.
- Guaranteed cancellation of blocking OS calls/subprocess trees and shutdown QA.
- Encrypted search index, streaming large vaults or complete DLP for free text.
- Reproducible dependency lock/SBOM for a Windows artifact not built here.

Never post a real personal access token in a ticket, commit, log or chat. If one
has been exposed, revoke it and use a fresh, scoped credential through a secure
integration when publishing.
