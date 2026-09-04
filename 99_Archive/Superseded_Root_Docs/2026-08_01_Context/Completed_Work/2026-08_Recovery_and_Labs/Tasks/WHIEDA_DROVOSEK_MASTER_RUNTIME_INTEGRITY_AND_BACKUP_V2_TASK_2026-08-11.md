# WHIEDA Drovosek: Master Runtime Integrity and Backup V2

## Goal

Make the master Google Sheets snapshot and the production Postgres runtime
auditable in one read-only command. The result must tell the owner whether the
current runtime is a safe representation of the latest captured master.

This is an observability and backup task. It must not publish or reconcile any
data.

## Hard boundaries

- Work only in `n8n/current/`, `qa/master_integrity/`, and tests/docs.
- Do not modify Google Sheets, n8n workflows, production Postgres, Telegram,
  Dify, product cards, or website files.
- Never execute `INSERT`, `UPDATE`, `DELETE`, DDL, sync, or reconciliation
  against a DSN passed to the auditor.
- Runtime access is only through `WHIEDA_RUNTIME_READONLY_DSN`.
- Do not print DSNs, passwords, tokens, phone numbers, raw Telegram IDs, or
  question texts in reports.
- No cron, no deploy, no automatic restore, no automatic retention deletion.

## Existing inputs

- Snapshot root: `n8n/live-exports/structured-master/`
- Latest confirmed snapshot at the time of this task:
  `20260811T172219Z/manifest.json`
- Snapshot library: `n8n/current/whieda_master_integrity_lib.py`
- Latest structured sync health probe:
  `n8n/current/structured_sync_health_2026-08-10.py`
- Runtime layer mapping starts in `RUNTIME_TABLES` in the integrity library.
- Current live reference counts: products 40, aliases 118, resources 239,
  product cards 23. These are reference facts only, not assertions to hardcode.

## Deliverables

### A. Read-only master/runtime auditor

Create:

`n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py`

CLI:

```powershell
# Finds newest valid snapshot, read-only runtime comparison and report.
python n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py

# Explicit snapshot, report locations and machine-readable output.
python n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py `
  --snapshot n8n/live-exports/structured-master/<timestamp> `
  --json-out .tmp/master_runtime_integrity.json `
  --markdown-out .tmp/master_runtime_integrity.md

# Prove it does not start a database session with write capability.
python n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py --dry-run
```

Requirements:

1. Select the newest valid `manifest.json` snapshot. If none is valid, abort
   with a non-zero exit code and an explicit reason.
2. Validate the snapshot with existing library rules before any DSN connection.
3. Connect via `BEGIN READ ONLY`. Assert `transaction_read_only = on` after
   connection and fail closed otherwise.
4. Compare every one of the 20 logical master layers. A layer can be:
   - `in_sync`;
   - `runtime_stale` (known master IDs absent in runtime);
   - `runtime_extra` (runtime IDs absent in master);
   - `schema_mismatch`;
   - `runtime_missing`;
   - `master_review_required`;
   - `not_runtime_backed` only where there is genuinely no runtime table.
5. Do not blindly assume every runtime table has `client_id`. Detect the tenant
   discriminator (`client_id`, `project_id`, or absent) from
   `information_schema.columns`. Use `whieda` only when that discriminator
   exists. This fixes the current health-probe class of false negatives.
6. For comparable layers, compare row count, normalized IDs, and a stable hash
   built from the layer's declared fields. Report ID additions/removals capped
   at 25 values, with totals retained.
7. Runtime only has `partners_ref` through the legacy partner path. Clearly
   report it as `not_runtime_backed` unless a real matching runtime relation is
   proven. Never infer it from a similarly named table.
8. Treat optional empty `product_details` as valid only if both snapshot and
   runtime agree it is empty. Non-empty master with empty runtime is stale.
9. Output strict JSON and a compact Russian Markdown report. JSON must include
   snapshot timestamp/hash, read-only proof, per-layer state, totals, sync
   freshness input, and `overall_status` (`safe`, `review_required`, `blocking`).
10. Reports go under `n8n/live-exports/master-runtime-integrity/<run-id>/` by
    default. Reports are generated artifacts and must be ignored by Git.

### B. Backup retention planner

Create:

`n8n/current/plan_whieda_master_snapshot_retention_2026-08-11.py`

Rules:

- Dry-run only; no `--apply` implementation in this task.
- Read snapshot manifests; never scan/delete arbitrary directories.
- Keep all snapshots for 14 days, then keep daily newest for 30 days, then
  weekly newest for 90 days. Mark only candidates, do not delete.
- A snapshot with invalid manifest, a blocking integrity report, or a manifest
  that is not parseable is always retained and marked `manual_review`.
- Emit JSON/Markdown plan and the number of bytes that would become removable.

### C. Repair health probe ergonomics

Extend `structured_sync_health_2026-08-10.py` only as needed:

- prefer `WHIEDA_RUNTIME_READONLY_DSN`, then explicit `--db-url`;
- when run without DSN, return `probe_not_configured`, never silently inspect
  a local database;
- dynamically support `project_id` and `client_id` tenant column variants;
- keep output redacted.

## Tests and fixtures

Create `qa/master_integrity/` with fixture snapshot and fixture runtime data.
No network and no real DSN in unit tests.

Required tests:

1. newest valid snapshot selection;
2. invalid latest snapshot falls back to prior valid snapshot;
3. read-only connection guard rejects writable transaction;
4. `client_id`, `project_id`, and no tenant column behavior;
5. in-sync, stale, runtime-extra, runtime-missing, schema-mismatch;
6. empty optional `product_details` and non-empty master/empty runtime;
7. capped ID list never hides totals;
8. `partners_ref` is not guessed;
9. report has no DSN/secret string;
10. retention plan preserves invalid/blocking snapshots and writes nothing.

Run:

```powershell
python -m pytest qa/master_integrity backend/platform-api/tests -q
python n8n/current/run_whieda_master_runtime_integrity_2026-08-11.py --dry-run
python n8n/current/plan_whieda_master_snapshot_retention_2026-08-11.py
```

## Acceptance

- One real read-only run against `WHIEDA_RUNTIME_READONLY_DSN` must produce a
  report for the current 20-layer master snapshot.
- No source data changes.
- Separate focused commits: A auditor, B retention, C tests/docs.
- Final report: command outputs, current summary, unbacked layers, and exact
  limitations. Do not claim production parity until the real read-only run
  passes.
