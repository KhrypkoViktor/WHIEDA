# WHIEDA Drovosek Task: Master Integrity and Backup

## Objective

Turn the Google Sheets master into a verifiable, recoverable source of truth.
The task must prove the current master export is complete, retain dated copies,
show drift, and compare it with a read-only runtime snapshot. No automatic
restore is allowed.

## Existing baseline

The architect captured a complete local master snapshot on 2026-08-10:

`n8n/live-exports/structured-master/20260810T083328Z/manifest.json`

It contains 20 layers and SHA-256 hashes. Treat this as input only; do not
rewrite or delete it.

## Hard boundaries

- Never modify Google Sheets.
- Never modify Postgres runtime, n8n, server cron, Telegram, Dify, or product text.
- Never restore any snapshot automatically.
- Do not include access maps, credentials, tokens, or private user data in a
  report or Git.
- Work only locally under `n8n/current`, `qa/master_integrity`, and docs.

## A. Master snapshot contract

Extend `capture_whieda_structured_master_snapshot.py` or create a small helper.

1. A snapshot is valid only when all 20 expected layers are downloaded,
   individually non-empty as files, and recorded in `manifest.json`.
2. Each layer records gid, byte size, row count, SHA-256, and header hash.
3. Preserve completed snapshots. Failed `.partial` captures are removed.
4. Add a retention policy helper: retain daily snapshots for 30 days and one
   monthly snapshot thereafter. It only reports what would be removed unless
   explicitly called with `--apply-retention`.
5. Do not treat a content-empty optional layer as a failed file. For example,
   an empty `product_details` data set with a valid TSV header is allowed.

## B. Drift and destructive-change classifier

Create a read-only comparison command for any two manifests/snapshots.

1. Report per layer: row delta, hash changed, header changed, added/removed IDs
   when an ID field is known.
2. Classify changes:
   - `expected_content_change` for normal edits;
   - `review_required` for header changes, more than 30 percent row decrease,
     or loss of a critical layer;
   - `blocking` for a missing file, invalid TSV, or a critical layer below its
     safety floor.
3. Critical floors are the live guardrails: products 20, aliases 60, resources
   25, product cards 10.
4. The tool must never change either snapshot or Sheet.

## C. Runtime parity reader

Build a separate read-only SQL template/runner. It must be able to connect only
when an explicit local DSN is supplied; never hardcode Supabase credentials.

1. Read row counts and stable hashes from current `advisor_structured_*` tables.
2. Compare runtime counts/hashes with a named master snapshot.
3. Statuses: `in_sync`, `stale_runtime`, `runtime_missing_layer`,
   `master_review_required`, `not_checked`.
4. Do not assume a successful n8n execution proves parity. The report needs
   direct runtime counts/hashes.
5. Redact DSN and any database error details before writing report.

## D. Deployment-ready, not deployed

Prepare a server-side scheduled snapshot definition only:

- one script invocation every 6 hours after the existing sync;
- output location on the server under a dedicated `whieda-master-snapshots`
  directory;
- no Sheet writes;
- no database writes;
- no automatic deletion until retention dry-run has been reviewed.

Do not install cron or copy any files to the server.

## Tests

1. Valid 20-layer snapshot passes.
2. Missing layer and invalid TSV are blocking.
3. Empty optional `product_details` with a valid header passes.
4. Critical row collapse is review/blocking as specified.
5. Header mutation is review-required.
6. Runtime parity uses fake local data and redacts a fake DSN/password.
7. Retention is dry-run by default.

## Deliverables

- local commands and tests;
- `MASTER_INTEGRITY_AND_BACKUP_LOCAL_REPORT.md`;
- one compact Git commit per logical block;
- explicit list of files that still require architect-controlled live deployment.

Do not claim live runtime parity unless it was run against an explicitly
supplied read-only DSN and the report contains only counts/hashes.
