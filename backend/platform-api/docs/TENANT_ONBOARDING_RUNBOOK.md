# WHIEDA Platform — Tenant Onboarding Runbook

Дата: 2026-08-02  
Статус: ops runbook для второго tenant без code/workflow fork

## Принципы

- Один codebase, один Core API image, один n8n control plane.
- Изоляция через `tenant_id`, RLS, entitlements и отдельные domain/bot bindings.
- Google Sheets — master editor для партнёров; Postgres — runtime.

## Checklist нового tenant

### 1. Registry (SQL)

```sql
-- tenants, tenant_domains, tenant_entitlements, tenant_bot_bindings
-- См. postgres/sql/platform_tenant_registry_v1.sql и platform_tenant_rls_v1.sql (test-acme seed)
```

- [ ] `tenants` row: `status=active`
- [ ] `tenant_domains`: apex + partner subdomains
- [ ] `tenant_entitlements`: `structure_basic`, `partner_leads` (по продукту)
- [ ] `tenant_bot_bindings`: opaque `binding_id` для Telegram webhook

### 2. Runtime data

- [ ] `referral_profiles` + `lead_actors` (из Sheets sync или seed)
- [ ] `advisor_structured_*` client_id = tenant_id
- [ ] `service_locations` при необходимости country routing

### 3. Edge routing

- [ ] Site nginx: `X-Forwarded-Host` = новый domain → Core tenant resolution
- [ ] Telegram: `POST /v1/telegram/{binding_id}/webhook` на Core (после cutover)
- [ ] Route switches в `.env` те же ключи `CORE_ROUTE_*` (не per-tenant в v1)

### 4. Verification

```powershell
python n8n/current/whieda_platform_core_smoke_2026-08-02.py --base https://NEW-DOMAIN
python n8n/current/whieda_platform_core_lead_smoke_2026-08-02.py --base https://NEW-DOMAIN
pytest backend/platform-api/tests/test_cross_tenant_isolation.py -q
```

### 5. Sheets / n8n

- [ ] Отдельный sync job payload с `tenant_id` (см. `WHIEDA_N8N_CONTROL_PLANE_CONTRACT.md`)
- [ ] Нет копии workflow — только tenant в envelope

## Rollback

- Отключить domain в `tenant_domains.is_active`
- Route switches на `legacy` для affected routes
- Tenant `status=suspended` — controlled 503, не default tenant

## Optional dedicated deployment

При scale gate Tier 2+ и изоляции billing: тот же image, отдельный `.env` с `PLATFORM_DATABASE_URL` и domain set; без fork SQL schema.
