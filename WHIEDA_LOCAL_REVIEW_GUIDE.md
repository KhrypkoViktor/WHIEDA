# WHIEDA Core — Local Review Guide

**Scope:** `backend/platform-api/`, `postgres/sql/`, `postgres/scripts/`, `n8n/current/*smoke*` (read-only ops reference).  
**Out of scope for Core developer:** `03_Website/`, live n8n, Telegram webhook, prod SQL, Google Sheets.

## Honest block manifest

[`WHIEDA_LOCAL_BUILD_BLOCKS_V1.json`](WHIEDA_LOCAL_BUILD_BLOCKS_V1.json) — **32 atomic tasks**, no filler rows.

Statuses:

| Status | Meaning |
|--------|---------|
| `implemented_local` | Code + unit tests in repo |
| `verified_staging` | Proved on empty local DB or HTTP smoke with Core up |
| `live_blocked` | Needs owner / live infra |

## Verify (Core)

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/ -q

cd D:\Projects\WHIEDA
python postgres\scripts\verify_staging_apply_empty.py

# Optional — only when Core listens on :8080:
python n8n\current\whieda_core_p0_local_full_smoke_2026-08-07.py --base-url http://127.0.0.1:8080
python n8n\current\whieda_staging_journey_e2e_2026-08-07.py --base-url http://127.0.0.1:8080
```

## Staging SQL apply order (full)

```powershell
.\postgres\scripts\apply_staging_platform_all.ps1 -DbHost 127.0.0.1 -Db whieda_platform -CreateDb
```

1. `platform_tenant_registry_v1.sql`
2. `platform_tenant_rls_v1.sql`
3. `platform_api_session_context_v1.sql`
4. `platform_identity_journey_v1.sql`
5. `platform_onboarding_v1.sql`
6. `platform_user_memory_v1.sql`
7. `platform_pilot_telemetry_v1.sql`
8. `platform_retention_export_v1.sql`
9. `platform_whieda_telegram_binding_v1.sql`
10. `staging_seed_whieda_journey_v1.sql` (optional seed)

## Production rails (unchanged)

| Flag | Value |
|------|-------|
| CORE_ROUTE_TELEGRAM | **legacy** |
| CORE_ROUTE_ADVISOR | shadow |

## Telegram delivery rule

1. `sendPhoto` — **без caption**
2. `sendMessage` — полный текст ответа
3. Если `sendPhoto` упал → всё равно `sendMessage`

Supported modes: `app/telegram/modes.py` → `TELEGRAM_DELIVERABLE_MODES` (includes `structured_product_detail`, `structured_comparison_layer`).

## LIVE BLOCKED

- Webhook cutover (`CORE_ROUTE_TELEGRAM=core`)
- Prod SQL apply
- n8n legacy greeting media
- Gate 3 real Telegram E2E

See [`CORE_LOCAL_BUILD_REPORT.md`](CORE_LOCAL_BUILD_REPORT.md) for last run results.
