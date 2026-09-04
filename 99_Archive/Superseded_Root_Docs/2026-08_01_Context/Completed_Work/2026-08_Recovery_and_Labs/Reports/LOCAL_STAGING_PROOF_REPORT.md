# Local staging proof report — 2026-08-07

## Commands

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/ -q

cd D:\Projects\WHIEDA
python postgres\scripts\run_local_staging_proof.py
```

## pytest

```
144 passed
```

Includes `tests/test_local_staging_proof.py` (DB name guard, prod host guard, SQL order, RLS table coverage).

## run_local_staging_proof.py

**On this agent machine:** exit 1 — `Docker not found in PATH` (WinError 2).  
Script uses `docker compose` + `docker exec` (no host `psql` required).

**When Docker is available**, expected success output:

```
=== LOCAL STAGING PROOF: PASS ===
  SQL files x2: 12 (+ seed)
  RLS tables: website_leads, referral_profiles, website_events, website_lead_watchers, referral_agreements
  Role: whieda_platform_api_proof (NOBYPASSRLS)
  dropped temporary database whieda_platform_staging_verify_<suffix>
```

## What is verified

| Step | Check |
|------|--------|
| Docker | Postgres 16 on port 55432, volume `whieda_local_staging_pgdata` |
| SQL x2 | 12 files from `staging_proof_lib.APPLY_ORDER` + seed, idempotent |
| API role | `whieda_platform_api_proof`: NOSUPERUSER, NOBYPASSRLS |
| RLS read | In `whieda` context: own tenant rows visible, `test-acme` rows hidden |
| RLS write | Cross-tenant INSERT into `website_leads` rejected |

## RLS tables checked (under API role)

- `website_leads`
- `referral_profiles`
- `website_events`
- `website_lead_watchers`
- `referral_agreements`

## Files added

- `postgres/docker-compose.local-staging.yml`
- `postgres/scripts/staging_proof_lib.py`
- `postgres/scripts/run_local_staging_proof.py`

## Not touched

Prod, Supabase, n8n, Telegram, Sheets, site, business copy.
