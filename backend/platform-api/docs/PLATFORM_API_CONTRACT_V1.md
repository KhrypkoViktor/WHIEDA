# WHIEDA Platform Core API Contract V1

Дата: 2026-08-07  
Статус: локальный контракт для review; live rails не меняются без владельца.

Tenant: middleware по `Host` / `X-Forwarded-Host`. Поле `tenant` в body игнорируется.

## Health

| Method | Path | Auth |
|--------|------|------|
| GET | `/health/live` | none |
| GET | `/health/ready` | none |

## Public ref

| Method | Path | Entitlement |
|--------|------|-------------|
| GET | `/v1/public/ref/{code}` | structure_basic |
| GET | `/api/v1/public/ref/{code}` | alias |

## Leads

| Method | Path | Entitlement |
|--------|------|-------------|
| POST | `/api/v1/leads` | partner_leads |
| POST | `/v1/leads` | alias |

Body: `name`, `contact`, `product`, `idempotency_key` required.  
Optional: `visitor_session_id`, `journey_type`, `route`, `ref`, `product_sku`, `page_url`.  
Forbidden: `owner_id`, `assigned_owner_id`, `attributed_owner_id`.

On create: outbox `lead_created` + `interaction_events.lead_created` when `visitor_session_id` set.

## Advisor

| Method | Path | Entitlement |
|--------|------|-------------|
| POST | `/v1/advisor/query` | structure_basic |

Body: `question`, `session` required. Optional: `ref`, `country`, `sku`, `slug`.

Service intents return empty `media`. Product responses include `product`, `media`, `context`.

## Identity & journey (Stage 3–4)

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/api/v1/visitor-sessions` | upsert session, immutable first_ref |
| GET | `/api/v1/visitor-sessions/{id}` | read session |
| POST | `/api/v1/telegram-link-tokens` | one-time deep link |
| POST | `/v1/telegram-link-tokens/exchange` | server-side bind Telegram |
| POST | `/api/v1/interaction-events` | idempotent route telemetry |

Route event types: `route_opened`, `product_viewed`, `useful_action_completed`, `advisor_question`, `telegram_link_created`, `telegram_opened`, `lead_created`, `partner_contacted`, `outcome_updated`.

## Onboarding (Stage 5)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/onboarding/enroll` | idempotent enrollment |
| POST | `/v1/onboarding/command` | text commands |

Commands: `мой план`, `начать обучение`, `сделал`, `нужна помощь`, `перенести`, `мой наставник`, `остановить напоминания`, `продолжить обучение`.

## Memory (Stage 3)

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/v1/memory-facts` | upsert confirmed fact |
| GET | `/v1/memory-facts/{subject_type}/{subject_id}` | list facts |

Subject types: `visitor_session`, `telegram_user`. No raw chat storage.

## Reports (Stage 6)

| Method | Path | Entitlement |
|--------|------|-------------|
| GET | `/v1/reports/leader-digest?days=7&owner_id=` | partner_leads |
| GET | `/v1/reports/leader-digest.csv` | CSV UTF-8 export |

## Telegram webhook (Stage 2 — code ready, live=legacy)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/v1/telegram/{binding_id}/webhook` | `X-Telegram-Bot-Api-Secret-Token` |

When `CORE_ROUTE_TELEGRAM=core`:

1. `/start <token>` → exchange link token, welcome + mentor
2. onboarding commands → state machine
3. else → SQL advisor

Delivery rule: `sendPhoto` without caption, then `sendMessage` with full text. If photo fails, text still sends.  
Deliverable modes: `app/telegram/modes.py` → `TELEGRAM_DELIVERABLE_MODES`.

When `legacy` or `shadow`: existing behavior unchanged.

## Outbox event types

| event_type | Handler |
|------------|---------|
| `lead_created` | n8n delivery |
| `onboarding_reminder` | log (Telegram send via future binding) |
| `leader_digest_weekly` | log (Telegram send via future binding) |

## SQL apply order (staging)

1. `platform_tenant_registry_v1.sql`
2. `platform_tenant_rls_v1.sql`
3. `platform_api_session_context_v1.sql`
4. `platform_identity_journey_v1.sql`
5. `platform_onboarding_v1.sql`
6. `platform_user_memory_v1.sql`
7. `platform_pilot_telemetry_v1.sql`
8. `platform_retention_export_v1.sql`
9. `platform_whieda_telegram_binding_v1.sql`

Script: `postgres/scripts/apply_staging_platform_all.ps1`  
Verify empty DB: `python postgres/scripts/verify_staging_apply_empty.py`

## Pilot (Stage 7)

| Method | Path | Entitlement |
|--------|------|-------------|
| POST | `/v1/pilot/outcomes` | partner_leads |
| POST | `/v1/pilot/refresh-metrics` | partner_leads |
| GET | `/v1/pilot/summary` | partner_leads |

## Retention (export only — no auto-delete)

| Method | Path | Entitlement |
|--------|------|-------------|
| GET | `/v1/retention/registry` | partner_leads |
| POST | `/v1/retention/export-requests` | partner_leads |

## Site journey client (Stage 4 — feature-flagged off by default)

`03_Website/wwc-best/public/wwc-api/journey.js`, `telegram-link.js`, `JourneyBootstrap.astro`  
Flags: `journeyApi`, `telegramLinkApi` in `wwc-runtime-config.json` (`off` until staging).

## Local verification

```powershell
cd backend/platform-api && python -m pytest tests/ -q
python n8n/current/whieda_local_verify_all_2026-08-07.py
python n8n/current/whieda_staging_journey_e2e_2026-08-07.py --base-url http://127.0.0.1:8080
python n8n/current/whieda_core_p0_local_full_smoke_2026-08-07.py
```
