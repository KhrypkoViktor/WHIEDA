# WWC Owner Cabinet — P0.4.1 completion report

**Дата:** 2026-08-12  
**Scope:** `product_name` в `GET /v1/admin/prices` из канонического реестра Core (`advisor_structured_products`). Frontend, SQL-миграции, production и nginx **не менялись**.

## 1. Что изменено

| Файл | Изменение |
|------|-----------|
| `app/admin/repositories/products.py` | Новый batch-read `fetch_product_names_by_skus()` |
| `app/admin/services/markets.py` | `build_prices_payload()` обогащает строки `product_name`; убран gap-meta на уровне item |
| `tests/test_admin_prices_product_name_p0_4_1.py` | Pytest: имена, null для unknown SKU, 401, tenant 403 |

## 2. Endpoint

| Метод | Path (FastAPI) | Alias (nginx staging) |
|-------|----------------|------------------------|
| GET | `/v1/admin/prices` | `/api/v1/admin/prices` |

Query: `market_id`, `limit`, `offset`, `tenant_id` (super-admin only) — без изменений.

## 3. Пример ответа

```json
{
  "ok": true,
  "tenant_id": "whieda",
  "items": [
    {
      "sku": "M015-00",
      "product_name": "Активатор клеток",
      "market_id": "BY",
      "currency_code": "BYN",
      "amount": 4800.0,
      "formatted": "4 800 BYN",
      "price_state": "published",
      "is_active": true,
      "updated_at": "2026-08-12T10:00:00+00:00"
    },
    {
      "sku": "UNKNOWN-SKU",
      "product_name": null,
      "market_id": "RU",
      "currency_code": "RUB",
      "amount": 100.0,
      "formatted": "100 RUB",
      "price_state": "published",
      "is_active": true,
      "updated_at": "2026-08-12T10:00:00+00:00"
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 2 }
}
```

Источник `product_name`: `advisor_structured_products.canonical_name` по `client_id = tenant_id` и `sku`.

## 4. Pytest

```bash
cd backend/platform-api
pytest tests/test_admin_prices_product_name_p0_4_1.py -q
```

Покрытие задачи §Проверка:

- `M015-00`, `EU-N000024-24`, `D014` — имена из mock registry
- Unknown SKU → `product_name: null` (не подставляется SKU)
- Поля `sku`, `market_id`, `formatted`, `price_state` сохранены
- `GET /v1/admin/prices` без сессии → 401
- Tenant admin + чужой `tenant_id` → 403 (`resolve_effective_tenant`)

## 5. Staging после деплоя Core

1. `docker compose build api && docker compose up -d api` на Core staging
2. В кабинете `/cabinet/markets/` — колонка «Товар» из API (fallback статического каталога остаётся на frontend)
3. `/cabinet/service-centers/` — после фикса API на Core (200) страница должна открываться; P0.4.1 не менял service-centers endpoint

## 6. Ограничения

- Deploy на Core **не выполнялся** (owner approval)
- Production не затронут
