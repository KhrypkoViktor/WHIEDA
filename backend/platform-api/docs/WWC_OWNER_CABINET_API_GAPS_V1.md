# WWC Owner Cabinet — пробелы API V1

**Дата:** 2026-08-09  
**Этап:** P0.0  
**Основание:** `WWC_OWNER_CABINET_SPEC_V0_1_2026-08-09.md` §5, `WWC_OWNER_CABINET_DATA_MAP_V1.md`  
**Правило:** перечислено только то, чего **нет** в Platform API сегодня; существующие маршруты не дублируются как «новые».

## 1. Текущий HTTP-контур Platform API

**Факт:** ~39 маршрутов в `backend/platform-api/app/*/routes.py`.  
**Факт:** **0** маршрутов `/v1/admin/*`.  
**Факт:** auth middleware для cabinet/admin **отсутствует**.

### Существующие маршруты (релевантные кабинету)

| Method | Path | Назначение | Ограничение для кабинета |
|--------|------|------------|--------------------------|
| GET | `/health/live`, `/health/ready` | health | публичный; нет business metrics |
| GET | `/v1/public/ref/{code}` | профиль ref | один код; публичный контракт |
| POST | `/v1/leads` | создание заявки | write-only ingest |
| GET | `/v1/reports/leader-digest` | сводка лидера | нет admin auth; нет tenant picker; coarse aggregates |
| GET | `/v1/reports/leader-digest.csv` | CSV | то же |
| GET | `/v1/pilot/summary` | pilot metrics | staging/local data |
| GET | `/v1/catalog-prices` | цены | public; нет admin snapshot meta |
| GET | `/v1/site-context` | market + ref context | public |
| GET | `/v1/service-centers`, `/v1/service-center-cities` | центры | public; нет completeness |
| PUT | `/v1/visitor-sessions` | journey | не admin |
| POST | `/v1/interaction-events` | telemetry | ingest, не read |
| GET | `/v1/memory-facts/{type}/{id}` | memory | не P0 cabinet |

---

## 2. Обязательные admin endpoints (cabinet spec §5)

Статус: **все — gap** (не реализованы).

| # | Endpoint | Назначение P0 | Источники данных (read) | Блокеры |
|---|----------|---------------|-------------------------|---------|
| A1 | `GET /v1/admin/overview` | карточки 7/30d, health, sync summary | `website_leads`, `referral_profiles`, `wwc_markets_sync_registry`, `wwc_*`, health internal | admin auth; markets may be empty on prod |
| A2 | `GET /v1/admin/leads` | paginated list + filters | `website_leads` + joins `lead_actors`, watchers | masking rules; pagination contract |
| A3 | `GET /v1/admin/leads/{lead_id}` | карточка + history | `website_leads`, `website_lead_status_history`, `website_lead_owner_history`, `website_lead_watchers`, `lead_delivery_attempts` | Telegram deep link optional |
| A4 | `GET /v1/admin/referrals` | ref/partner registry | `referral_profiles`, `lead_actors`, optional `wwc_ref_structures` | tier field gap; last_activity gap |
| A5 | `GET /v1/admin/markets` | рынки + snapshot | `wwc_markets`, `wwc_markets_sync_registry` | prod may have no rows |
| A6 | `GET /v1/admin/prices` | paginated SKU prices | `wwc_product_prices` + SKU names | name join gap |
| A7 | `GET /v1/admin/service-centers` | centers + completeness | `wwc_service_centers`, `wwc_service_center_coverage` | vs `service_locations` label |
| A8 | `GET /v1/admin/sync-status` | sync runs, errors, row counts | `wwc_markets_sync_registry`; partners cron **not in Core** | n8n execution metadata gap |

### Общие требования ко всем A1–A8

- Tenant из session/host — **не** из query/body.
- Ответ `{ ok: true, ... }` на успехе.
- Server-side pagination (`cursor` или `page`+`limit`).
- 401/403 без auth / cross-tenant.
- Запрет секретов: webhook secrets, raw contacts policy, service account paths, `metadata` dump без фильтра.
- Pytest: cross-tenant denial + secret leakage checks.

---

## 3. Auth foundation (P0.1 prerequisite)

| # | Capability | Статус | Примечание |
|---|------------|--------|------------|
| B1 | Admin session / identity | **gap** | Spec: Telegram allow-list, no passwords |
| B2 | `super_admin` / `admin` / `viewer` RBAC | **gap** | Only Viktor = super_admin (spec §13) |
| B3 | Multi-tenant scope for super_admin | **gap** | Summary vs detailed view + audit |
| B4 | Admin audit log (`admin_sensitive_view`, `admin_change`) | **gap** | Catalog §5.10 — plan |
| B5 | Middleware on `/v1/admin/*` | **gap** | Must not proxy via public wwc.best without auth |

### Переиспользуемые building blocks (не admin, но полезны)

- `identity_link_tokens` + exchange flow — pattern for Telegram proof.
- `tenant_entitlements` — feature gating pattern (`require_entitlement`).
- `TenantMiddleware` + RLS `platform_current_tenant_id()`.

---

## 4. Дополнительные gaps по экранам кабинета

### 4.1. Обзор (§3.1)

| Поле UI | API field source | Gap |
|---------|------------------|-----|
| Новые/в работе/завершённые заявки | aggregate on `website_leads.status` | A1 |
| Заявки по ref/структурам | group by ref/structure | structure_id not canonical |
| Активные/отключённые ref | `referral_profiles.enabled` | A4 |
| Дата последней sync рынков | `wwc_markets_sync_registry.synced_at` | A8; prod empty |
| Центры с неполными полями | completeness function | A7 — logic not implemented |
| Health API / last error | internal + sync registry | A1 |

### 4.2. Заявки (§3.2)

| Gap | Detail |
|-----|--------|
| List + filters | A2 — status, ref, structure, country, product, assignee |
| Masked contact | masking helper not in API |
| Attribution triple | attributed vs assigned vs watchers in one DTO |
| Write status/assignee | **P1** — intentionally out of P0.1 read scope |

### 4.3. Партнёры и ref (§3.3)

| Gap | Detail |
|-----|--------|
| Search + pagination | A4 |
| Public URL + QR | computed server-side |
| Tariff level | no DB column — return `null` + `gap: true` |
| Link to Sheets row | URL template from config, not hardcoded secret |
| `last_activity_at` | max(lead.created_at, session.updated_at, event) — query not built |

### 4.4. Рынки и цены (§3.4)

| Gap | Detail |
|-----|--------|
| Market list with default flag | A5 |
| Price table with `price_state` | A6 |
| Compare RU/BY in cabinet | A6 multi-market query |
| «Не опубликовано» marker | needs sync error + `is_active` |

### 4.5. Сервисные центры (§3.5)

| Gap | Detail |
|-----|--------|
| Completeness warnings | required fields validator |
| Coverage gaps | cities without active coverage |
| Legacy `service_locations` | optional read endpoint or explicit `source: legacy` in DTO — **decision needed** |

### 4.6. Источники и синхронизация (§3.6)

| Gap | Detail |
|-----|--------|
| Partners_Ref last sync | n8n execution time — **not exposed to Core** |
| Structured sync last run | same |
| Markets sync history (20 runs) | only last state in registry — **no history table** |
| Row accepted/rejected counts | markets sync returns counts in CLI JSON — not persisted |

**P0 honest UI:** show markets sync from `wwc_markets_sync_registry`; partners block = `gap` with link to Sheets until n8n read-only probe API exists.

---

## 5. Analytics / ref KPI APIs (post-P0, но зафиксировать gap)

Не входят в cabinet spec P0 §5, но требуются master spec §6 и data map §5.

| # | Endpoint (proposed) | Статус |
|---|---------------------|--------|
| C1 | `GET /v1/admin/referrals/{ref_code}/metrics?days=` | **gap** |
| C2 | `GET /v1/admin/analytics/funnel?ref=` | **gap** |
| C3 | `GET /v1/admin/events?session_id=` | **gap** — read `interaction_events` |
| C4 | Metrika import bridge | **gap** — external; no API |

---

## 6. Write APIs (явно вне P0)

Cabinet spec §7 запрещает write в P0. Для P1 backlog:

| Endpoint | Статус |
|----------|--------|
| `PATCH /v1/admin/leads/{id}` | **gap** (P1) |
| `POST /v1/admin/referrals` | **gap** (P1) |
| `PUT /v1/admin/prices/{sku}` | **gap** (P1) |
| `POST /v1/admin/service-centers` | **gap** (P1) |
| `POST /v1/admin/sync/markets/trigger` | **gap** — CLI only today |

---

## 7. Internal / ops gaps (не для browser cabinet)

| Capability | Today | Gap |
|------------|-------|-----|
| Markets manual sync | `python scripts/sync_wwc_markets.py` | HTTP admin trigger |
| Partners sync status | n8n UI / scripts | Read-only status endpoint |
| n8n execution logs | n8n host | Safe subset for A8 |
| Cross-tenant super_admin view | — | Aggregator in A1 |

---

## 8. Приорitized implementation list (P0.1)

Минимальный безопасный порядок без архитектурного риска:

1. **B1–B5** — Telegram allow-list auth + `/v1/admin` router shell + 401/403 tests.
2. **A2, A3** — leads read (production data exists).
3. **A4** — referrals read (production data exists).
4. **A8** — partial: markets registry only; partners = structured `gap` object.
5. **A5, A6, A7** — markets/prices/centers read (graceful empty if prod not seeded).
6. **A1** — overview composes above services.

Не начинать UI (P0.2) до JSON contract freeze + pytest green.

---

## 9. Контрактные заглушки для `gap` полей

Чтобы UI не показывал «0» вместо отсутствия данных:

```json
{
  "ok": true,
  "partner_tier": null,
  "meta": {
    "field_status": {
      "partner_tier": "gap",
      "last_activity_at": "gap",
      "subscription_cost": "gap",
      "partners_sync_last_run": "gap"
    }
  }
}
```

---

## 10. Связанные документы

- `WWC_OWNER_CABINET_DATA_MAP_V1.md` — откуда читать данные
- `WWC_STAGING_ENVIRONMENT_PLAN_V1.md` — где проверять A1–A8 до production
- `WWC_OWNER_CABINET_SPEC_V0_1_2026-08-09.md` — приёмка P0.1
