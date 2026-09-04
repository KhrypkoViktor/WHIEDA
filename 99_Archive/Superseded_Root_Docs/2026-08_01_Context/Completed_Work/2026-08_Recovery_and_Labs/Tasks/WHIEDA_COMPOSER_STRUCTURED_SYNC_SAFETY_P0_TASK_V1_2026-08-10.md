# WHIEDA Composer Task: Structured Sync Safety P0

## Goal

Make the existing Google Sheets -> Postgres structured sync recoverable and
observable. Preserve the current Google Sheet as master and Postgres as runtime
cache. This task prepares local artifacts only. Do not publish to n8n, do not
run a live migration, and do not edit the Google Sheet.

## Known live facts

- Workflow: `WHIEDA Structured Sync Cron`, id `9roEvXNsDpnwqjzH`.
- The VPS cron calls its webhook every 15 minutes.
- Recent executions are successful and take roughly 16-26 seconds.
- The current workflow executes many Postgres nodes in sequence. A failure in
  the middle can leave a partial catalogue.
- A local P0 row-count circuit breaker already exists in
  `n8n/current/whieda_structured_sync_code_2026-07-13.js`. Do not remove it.

## Scope

Only these locations may change:

- `D:\Projects\WHIEDA\n8n\current`
- `D:\Projects\WHIEDA\n8n\patches`
- `D:\Projects\WHIEDA\qa\structured_sync`
- narrow supporting tests or docs under `D:\Projects\WHIEDA\backend\platform-api\tests`

Never change production, n8n UI, Telegram, Dify, Google Sheets, product-card
copy, credentials, server cron, or site files.

## Deliverable A: one atomic apply query

1. Replace the runtime write chain conceptually with one Postgres transaction:
   `BEGIN -> all structured replacements -> audit success -> COMMIT`.
2. The Code node may still parse all TSV inputs, but it must expose one
   `query_apply_all` field for the one Postgres write node.
3. On any error the transaction must roll back every changed structured table.
4. Use a transaction-scoped advisory lock for tenant `whieda` so two cron
   invocations cannot overlap. A second run must fail cleanly before writes.
5. Keep all current supported layers: products, aliases, resources, cards,
   details, comparisons, access, owners, business FAQ/objections, promotions,
   recommendations, baskets, events, community resources, intents,
   clarifications, capabilities, canonical questions, and Partners Ref.
6. Do not silently convert a missing price or malformed row into a valid zero.

## Deliverable B: immutable sync-run audit

1. Extend `advisor_structured_sync_runs` safely if needed. One run must record:
   run id, started/finished timestamps, status `running|success|failed`,
   source row counts, workflow execution id when available, and a redacted
   error summary.
2. A failure must create a `failed` audit row without storing raw Sheet data,
   credentials, user identifiers, or SQL text.
3. A successful run writes `success` in the same transaction as the cache
   replacement.
4. Provide a separate n8n Error Trigger workflow definition or a controlled
   error branch that writes the failed audit record. It must not retry the
   sync automatically and must not notify Telegram yet.

## Deliverable C: freshness/readiness contract

1. Create a small read-only health query/script that returns:
   last success, age in minutes, last failure, current row counts, and status
   `healthy|stale|failed|never_synced`.
2. Use these thresholds: healthy <= 30 minutes; stale > 30 minutes.
3. A failed run is visible even when an older last-good cache still serves.
4. Define a future API shape only; do not wire `/v1/admin/sync-status` yet.

## Required local tests

Use isolated local fixtures or Docker staging only. No real Sheets or server.

1. Valid corpus: all layers apply and one `success` audit exists.
2. Empty products, aliases, resources, or cards: no runtime write; last-good
   row counts remain unchanged; failed audit exists.
3. Throw after a middle layer: every runtime layer remains last-good; failed
   audit exists.
4. Two simultaneous runs: one owns lock; second writes nothing.
5. Error text containing a fake token/password is redacted in audit output.
6. Freshness states: healthy, stale, failed, never_synced.
7. Existing 15-minute cron is out of scope: test the webhook contract only.

## Required report

Create `STRUCTURED_SYNC_SAFETY_P0_LOCAL_REPORT.md` with:

- architecture before/after;
- exact test commands and results;
- row counts before/after every failure test;
- proof that no production/n8n/Sheets were touched;
- changed files;
- remaining release step: architect reviews then publishes one workflow patch
  and triggers one controlled live sync.

Make clean, small commits by deliverable. Do not claim live verification.
