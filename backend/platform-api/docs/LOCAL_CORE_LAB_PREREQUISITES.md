# Local Core E2E Lab — prerequisites

One command after Docker Desktop is installed:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e
```

Tenant Telegram local canary (last local Core gate):

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --tenant-telegram-canary
```

Run the doctor first if anything fails:

```powershell
python backend\platform-api\scripts\doctor_local_core_lab.py
```

## Required software

| Component | Check | Notes |
|-----------|-------|-------|
| Docker Desktop | `docker info` | Daemon must be running |
| Docker Compose v2 | `docker compose version` | Bundled with Docker Desktop |
| Python 3.11+ | `python --version` | Same interpreter used for lab scripts |

The doctor is read-only: it does not download, start containers, or modify files.

## Ports

| Port | Service | Container |
|------|---------|-----------|
| `55432` | Staging Postgres | `whieda-local-staging-postgres` |
| `8080` | Platform Core API | `whieda-local-core-api` |
| `18081` | Local Telegram HTTP capture | host process (canary only) |

If these ports are already used by the lab containers from a previous run, the doctor reports **WARN** (safe to rerun). Any other occupier is **FAIL**.

## Compose files

- `postgres/docker-compose.local-staging.yml` — Postgres 16 on host port 55432
- `backend/platform-api/docker-compose.local-core.yml` — Core API on 8080

## Environment

Copy is not required for E2E: Core uses `backend/platform-api/.env.local.example` via compose.

The doctor verifies `.env.local.example`:

- localhost / `host.docker.internal` only
- no production hosts (Supabase, VPS, duckdns, Telegram API, etc.)
- no live secret assignments (`PLATFORM_TELEGRAM_BOT_TOKEN`, etc.)
- API database role `whieda_platform_api_local` (not `postgres` superuser)

## Database

E2E creates **only** `whieda_platform_local_core` on local Docker Postgres.

Staging SQL apply order (12 files + seed) comes from `postgres/scripts/staging_proof_lib.py`:

1. `platform_tenant_registry_v1.sql`
2. `platform_tenant_rls_v1.sql`
3. `whieda_website_leads_p0_v1.sql`
4. `wwc_leads_p01_runtime_migration.sql`
5. `platform_tenant_rls_legacy_leads_v1.sql`
6. `platform_api_session_context_v1.sql`
7. `platform_identity_journey_v1.sql`
8. `platform_onboarding_v1.sql`
9. `platform_user_memory_v1.sql`
10. `platform_pilot_telemetry_v1.sql`
11. `platform_retention_export_v1.sql`
12. `platform_whieda_telegram_binding_v1.sql`
13. Seed: `staging_seed_whieda_journey_v1.sql`

All SQL is applied via `docker exec` into the staging container — no host `psql`.

API role `whieda_platform_api_local` is created with `NOSUPERUSER NOBYPASSRLS`.

## Acceptance target

P0 acceptance needs `qa/acceptance/acceptance_target.local.json`.

If missing, copy from example:

```powershell
copy qa\acceptance\acceptance_target.example.json qa\acceptance\acceptance_target.local.json
```

The E2E orchestrator creates this file automatically from the example when `--e2e` runs.

## What is NOT touched

- n8n, Telegram, Dify, Google Sheets
- Supabase / production Postgres
- VPS hosts, live nginx, public site
- Runtime product SQL or catalog cards in Sheets

## Reports

E2E run reports are written to:

`backend/platform-api/reports/local_core_e2e/latest_run.md`

Status is always honest: `PASS`, `FAIL`, or `NOT_RUN` (when Docker is unavailable).

## Standalone verify (Core must already be up)

```powershell
python backend\platform-api\scripts\verify_local_core_e2e.py
```

Uses real `urllib` to `127.0.0.1:8080` — no FakeTransport.
