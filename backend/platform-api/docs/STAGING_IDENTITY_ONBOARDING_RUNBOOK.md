# Staging Apply Runbook — Identity, Journey, Onboarding (local-first)

Дата: 2026-08-07  
Статус: **только staging** — prod apply только после review владельца.

## Prerequisites

- Postgres staging с `platform_tenant_registry_v1.sql` и RLS уже применёнными
- Core API image с текущей веткой
- **Не** менять `CORE_ROUTE_TELEGRAM`, webhook, n8n workflows

## 1. Apply SQL (порядок)

```powershell
$env:PLATFORM_DATABASE_URL = "postgresql://..."  # staging only

psql $env:PLATFORM_DATABASE_URL -f postgres/sql/platform_identity_journey_v1.sql
psql $env:PLATFORM_DATABASE_URL -f postgres/sql/platform_onboarding_v1.sql
psql $env:PLATFORM_DATABASE_URL -f postgres/sql/platform_user_memory_v1.sql
```

## 2. Core env (staging)

```env
PLATFORM_TELEGRAM_BOT_USERNAME=your_staging_bot
# НЕ задавать prod bot token в dev-ветке без cutover plan
```

## 3. Restart Core (staging host only — owner executes)

```powershell
python backend/deploy/core/deploy_platform_core_2026-08-02.py  # owner script
```

## 4. Local unit tests (разработчик)

```powershell
cd backend/platform-api
python -m pytest tests/ -q
```

## 5. HTTP smoke (localhost или staging base URL)

```powershell
# Advisor media
python n8n/current/whieda_core_service_media_smoke_2026-08-07.py --base-url http://127.0.0.1:8080

# Parity + media (subset P0)
python n8n/current/whieda_core_parity_local_smoke_2026-08-07.py --base-url http://127.0.0.1:8080
```

## 6. Identity API manual checks

```powershell
# Create session + link token
curl -s -H "host: wwc.best" -H "content-type: application/json" `
  -X PUT http://127.0.0.1:8080/api/v1/visitor-sessions `
  -d '{"ref":"ladnaya","journey_type":"product"}'

curl -s -H "host: wwc.best" -H "content-type: application/json" `
  -X POST http://127.0.0.1:8080/api/v1/telegram-link-tokens `
  -d '{"ref":"ladnaya","journey_type":"product"}'

# Event idempotency
curl -s -H "host: wwc.best" -H "content-type: application/json" `
  -X POST http://127.0.0.1:8080/api/v1/interaction-events `
  -d '{"event_type":"route_opened","idempotency_key":"test-route-1","payload":{"route":"product"}}'
```

## 7. Onboarding check

```powershell
curl -s -H "host: wwc.best" -H "content-type: application/json" `
  -X POST http://127.0.0.1:8080/v1/onboarding/enroll `
  -d '{"telegram_user_id":999001,"first_ref":"ladnaya","idempotency_key":"test-enroll-1"}'
```

## 8. Leader report

```powershell
curl -s -H "host: wwc.best" "http://127.0.0.1:8080/v1/reports/leader-digest?days=7"
curl -s -H "host: wwc.best" "http://127.0.0.1:8080/v1/reports/leader-digest.csv?days=7"
```

## 9. Full API contract

See `backend/platform-api/docs/PLATFORM_API_CONTRACT_V1.md`

## 10. Scheduled jobs (staging)

```powershell
cd backend/platform-api
python scripts/run_scheduled_jobs.py
```

## Gate checklist (staging)

- [ ] SQL applied without error
- [ ] pytest green
- [ ] SERVICE-GREETING-NO-MEDIA green on Core route
- [ ] Link token create → exchange → 409 on reuse
- [ ] first_ref immutable on second upsert with different ref
- [ ] interaction event duplicate idempotency_key → created=false
- [ ] onboarding enroll idempotent
- [ ] escalation duplicate → no second row
- [ ] leader digest returns JSON + CSV

## Rollback

- DDL tables are additive; rollback = не вызывать новые endpoints с сайта
- Drop tables only on staging with owner OK
