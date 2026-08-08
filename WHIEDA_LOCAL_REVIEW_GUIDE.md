# WHIEDA Core — Local Review Guide

**Scope:** `backend/platform-api/`, `postgres/sql/`, `postgres/scripts/`.  
**Out of scope:** `03_Website/`, live n8n, Telegram webhook, prod SQL, Google Sheets, Supabase.

## One-command local staging proof

**Requires:** Docker Desktop (or Docker Engine) installed locally. The script does not install Docker.

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/ -q

cd D:\Projects\WHIEDA
python postgres\scripts\run_local_staging_proof.py
```

## One-command local Core runtime lab

**Requires:** Docker (same staging Postgres on port 55432).

```powershell
cd D:\Projects\WHIEDA
python backend\platform-api\scripts\run_local_core_lab.py
```

Flow: staging Postgres → SQL proof → Core DB → Platform API on **8080** → HTTP contract smoke → stop Core (Postgres stays up).

Env template: `backend/platform-api/.env.local.example` (copy to `.env.local` if needed).

Report: [`LOCAL_CORE_LAB_REPORT.md`](LOCAL_CORE_LAB_REPORT.md)

### What the proof does

1. Starts `postgres:16-alpine` via `postgres/docker-compose.local-staging.yml` on port **55432**
2. Creates temporary DB `whieda_platform_staging_verify_<random>`
3. Applies all staging SQL **twice** (idempotency check)
4. Creates local-only API role `whieda_platform_api_proof` (NOBYPASSRLS, not superuser)
5. Verifies RLS isolation `whieda` vs `test-acme` on:
   - `website_leads`
   - `referral_profiles`
   - `website_events`
   - `website_lead_watchers`
   - `referral_agreements`
6. Drops the temporary database; **container stays running**

### Expected output (success)

```
=== LOCAL STAGING PROOF: PASS ===
  SQL files x2: 12 (+ seed)
  RLS tables: website_leads, referral_profiles, ...
  Role: whieda_platform_api_proof (NOBYPASSRLS)
  dropped temporary database whieda_platform_staging_verify_...
```

Exit code `0`.

### Manual Docker control

```powershell
docker compose -f postgres\docker-compose.local-staging.yml up -d
docker compose -f postgres\docker-compose.local-staging.yml down   # when finished
```

## Block manifest

[`WHIEDA_LOCAL_BUILD_BLOCKS_V1.json`](WHIEDA_LOCAL_BUILD_BLOCKS_V1.json) — 31 tasks; counts from `blocks[]`.

## Staging SQL apply order

See `postgres/scripts/staging_proof_lib.py` → `APPLY_ORDER` (same as `apply_staging_platform_all.ps1`).

## Production rails (unchanged)

| Flag | Value |
|------|-------|
| CORE_ROUTE_TELEGRAM | **legacy** |
| CORE_ROUTE_ADVISOR | shadow |

## LIVE BLOCKED

- Webhook cutover, prod SQL, n8n legacy greeting media, Gate 3 Telegram E2E

Report: [`LOCAL_STAGING_PROOF_REPORT.md`](LOCAL_STAGING_PROOF_REPORT.md)
