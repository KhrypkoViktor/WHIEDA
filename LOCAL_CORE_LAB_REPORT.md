# Local Core Runtime Lab — Report

**Date:** 2026-08-03  
**Scope:** local only (`D:\Projects\WHIEDA`); no prod/VPS/n8n/Telegram/Sheets/Dify/site touched.

## Commands

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/ -q --cache-clear

cd D:\Projects\WHIEDA
python backend\platform-api\scripts\run_local_core_lab.py
```

## Actual output (this machine)

### pytest

```
151 passed in 1.61s
```

### run_local_core_lab.py

```
=== WHIEDA local Core runtime lab ===
FAIL: Docker required — install Docker Desktop and retry.
```

Exit code: **1** (expected — Docker not installed/in PATH on this host).  
Script does **not** attempt `docker compose down` when Docker is missing.

## With Docker (expected flow)

1. `postgres/docker-compose.local-staging.yml` — Postgres 16 on **55432** (volume retained).
2. `postgres/scripts/run_local_staging_proof.py` — temp DB SQL + RLS proof.
3. `postgres/scripts/ensure_local_core_database.py` — persistent `whieda_platform_local_core`.
4. `backend/platform-api/docker-compose.local-core.yml` — Platform API on **127.0.0.1:8080**.
5. Wait `GET http://127.0.0.1:8080/health/ready` (Host: `wwc.best`).
6. `backend/platform-api/scripts/local_http_contract_smoke.py`.
7. Stop Core only (`compose down` on local-core); **Postgres volume not removed**.

## What HTTP smoke checks

| Check | Detail |
|-------|--------|
| `/health/live`, `/health/ready` | 200, no secrets in body |
| `/openapi.json` | Valid OpenAPI, no external legacy URLs |
| Invalid JSON → POST `/api/v1/leads` | 400 or 422, no traceback |
| Tenant isolation | `ladnaya` on `wwc.best` → 200; missing Host → 404; `acme.test.local` → 404 |
| Telegram webhook | POST `/v1/telegram/local-lab-smoke/webhook` → 200 `{ok:true}`, no legacy URLs in response |
| No legacy outbound | Smoke refuses non-localhost base URL; no Telegram/n8n webhook calls |
| Response hygiene | No passwords, connection strings, tokens, traceback |

## Static pytest guards (`test_local_core_lab.py`)

- `docker-compose.local-core.yml` — no prod/Supabase/VPS URLs; port 8080; `.env.local.example`.
- `.env.local.example` — localhost only; no secret assignments.
- HTTP smoke — no outbound requests to Telegram/n8n (external hosts only in deny-list constants).
- Lab script — no `volume rm`, `down -v`, or `down --volumes`; runs staging proof + ensure DB.

## Entry point

```powershell
cd D:\Projects\WHIEDA
python backend\platform-api\scripts\run_local_core_lab.py
```

Env template: `backend/platform-api/.env.local.example`

## Honest limits (verified 2026-08-08)

- **E2E lab not run on this machine** — Docker not in PATH; only static tests + script exit code verified.
- **Full PASS requires Docker** on your side: `python backend\platform-api\scripts\run_local_core_lab.py`
- **`postgres/sql/platform_tenant_rls_legacy_leads_v1.sql`** referenced by staging proof but **not in git** (pre-existing gap); lab needs this file on disk locally.
- **`ensure_local_core_database.py`** skips re-apply if `platform_tenants` exists (second run safe); use `--force-reapply` to rebuild.
