# Core local build report — 2026-08-07 (rev 2)

## Scope

Backend Core only (`backend/platform-api`, `postgres/`). Site, n8n, webhook, prod SQL, Sheets — not touched.

## Manifest

[`WHIEDA_LOCAL_BUILD_BLOCKS_V1.json`](WHIEDA_LOCAL_BUILD_BLOCKS_V1.json) — **31 blocks**, counts derived from `blocks[]`:

| Status | Count |
|--------|-------|
| implemented_local | 27 |
| verified_staging | 0 |
| live_blocked | 4 |

Runner: `python n8n/current/whieda_local_verify_all_2026-08-07.py` prints the same counts (no manual summary field in JSON).

## pytest

```
cd backend/platform-api && python -m pytest tests/ -q
137 passed
```

## Staging SQL empty-DB proof

```powershell
python postgres\scripts\verify_staging_apply_empty.py
```

Apply order (12 files + seed):

1. tenant registry → core RLS helpers
2. `whieda_website_leads_p0_v1.sql` → `wwc_leads_p01_runtime_migration.sql`
3. legacy leads RLS
4. platform journey / onboarding / memory / pilot / retention / bot binding

DB name guard: only `whieda_platform_staging_verify` or `whieda_platform_staging_verify_<suffix>`.

**Last run:** Postgres not reachable on this machine (`psql` exit 2). Re-run when local Postgres is up.

## Fixes in rev 2

1. `knowledge_gap` and any non-internal mode with `answer_text` → Telegram delivery
2. Split RLS: core helpers vs legacy leads (empty DB bootstraps)
3. Manifest counts from `blocks[]`; runner fixed
4. Verify script DB name validation

## LIVE BLOCKED (unchanged)

- `CORE_ROUTE_TELEGRAM=core`
- Prod SQL
- n8n legacy greeting media
- P0 smoke without Core running
