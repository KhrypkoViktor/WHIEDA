# Core local build report — 2026-08-07

## Scope

Backend Core only (`backend/platform-api`, `postgres/`). Site, n8n, webhook, prod SQL, Sheets — not touched.

## Manifest

[`WHIEDA_LOCAL_BUILD_BLOCKS_V1.json`](WHIEDA_LOCAL_BUILD_BLOCKS_V1.json): **32 blocks**, honest statuses.

| Status | Count |
|--------|-------|
| implemented_local | 28 |
| verified_staging | 1 (pending Postgres — see below) |
| live_blocked | 3 |

Removed: 120 filler `item N` rows from prior manifest.

## pytest

```
cd backend/platform-api && python -m pytest tests/ -q
132 passed
```

New regression coverage:

- Photo without caption, then text (`test_photo_then_text_separate_messages`)
- Text sent when photo fails (`test_photo_failure_still_sends_text`)
- `structured_product_detail` triggers delivery (`test_product_detail_triggers_delivery`)
- Full staging SQL order in apply script (`test_staging_sql_order.py`)
- All SQL engine deliverable modes in `TELEGRAM_DELIVERABLE_MODES`

## Staging SQL empty-DB proof

Command:

```powershell
python postgres\scripts\verify_staging_apply_empty.py
```

**Result on this machine:** `FAIL` — local Postgres not reachable (`psql` exit 2 on `127.0.0.1:5432`).

Apply order is fixed in `postgres/scripts/apply_staging_platform_all.ps1` (9 files + seed). Re-run verify when Postgres is up to move block `test-02` to verified.

## P0 HTTP smoke

Command (requires Core on `:8080`):

```powershell
python n8n\current\whieda_core_p0_local_full_smoke_2026-08-07.py --base-url http://127.0.0.1:8080
```

**Not run** — Core was not listening during this pass. Prior run without server: 0/28 (expected). Not claimed as pass.

## Fixes in this commit

1. Honest 32-block manifest + Core-only review guide
2. Full SQL apply order incl. tenant registry, RLS, session context, telegram binding
3. Telegram delivery: photo without caption → text; text on photo failure
4. Unified `app/telegram/modes.py` with `structured_product_detail` and `structured_comparison_layer`

## LIVE BLOCKED (unchanged)

- `CORE_ROUTE_TELEGRAM=core`
- Prod SQL
- n8n legacy greeting media
- Gate 3 real Telegram E2E
