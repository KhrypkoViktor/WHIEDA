# NSP staging — состояние (NSP-агент)

Ветка: `nsp/canary-integration` от `origin/master` (после `73d958d`).
Тенант `nsp-maxim`, staging-only. `--publish` не выполнялся.

## Что живое (17.09.2026)

| Что | Значение |
|---|---|
| Сервер | `185.252.232.93` (`ssh whieda-n8n`), контейнер `whieda-shared-staging-api`, образ `whieda-shared-staging-core:lead-10763d5` |
| Домен / бот | `https://nsp-staging.sysarch.pro`, `@NSP_Stage_bot`, binding `nsp-maxim-canary-bot` active, webhook стоит |
| БД | `whieda_shared_staging` (контейнер `whieda-staging-db`), не production |
| Пакет | `nsp-maxim-canary-v2`, 58 SKU импортировано (`advisor_structured_products` = 58), sha `7eb2610a…` |
| Медиа | `https://media.sysarch.pro/nsp-media/nsp-maxim/<sku>/<file>`, 97/97 товаров с фото; media-manifest 58 строк `ready`, `sources/<sku>/` на сервере |
| Меню | `/start /products /business /company /support` |
| Golden | 72/79 (`qa/nsp_golden/`), 7 провалов = два бага движка, закрытые Core в `73d958d` — перепрогнать после слияния |

## Файлы этой ветки (только зона nsp)

- `backend/platform-api/app/tenants/` — профиль NSP (тексты), курс НБРБ (`fx_nbrb.py`). `voice.py` (Core, `73d958d`) читает его через `get_tenant_profile`.
- `backend/platform-api/tests/test_tenant_profiles.py`, `test_tenant_fx_nbrb.py`.
- `qa/nsp_golden/` — генератор, 79 кейсов, in-process runner (без Telegram).
- `PROCESS/nsp-staging-20260916/nsp-stagingctl` — копия `/usr/local/bin/nsp-stagingctl` с сервера: `status|env-init|up|nginx-enable|down|logs|webhook-*|menu-set|preflight|apply-package|binding-stage`.

## Запросы Core-агенту (в его зоне, по одной строке)

1. `scripts/shared_staging_canary/catalog.py:171` — `len(importable) != 17` → `< 17` (пакет v2 = 58 SKU; на staging уже так через mount, в `master` нет).
2. `qa/telegram_golden/build_nsp_golden_cases.py` и `nsp_telegram_golden_cases_v1.jsonl` — удалить, канон теперь `qa/nsp_golden/` (там исправленные версии).
3. `app/telegram/support.py` — куда падает тикет `/support` у tenant `nsp-maxim` (сейчас fallback NSP ведёт в `/support`).

## Не сделано / ждёт

- 24ч canary Максим+Виктор — не проводился.
- 39 SKU без подтверждённой цены/приёма — ждут Максима (мастер-таблица у него).
- Сравнения, маркетинг-план, бизнес-FAQ — ждут ответов Максима.
- Production — после canary и договора с юрлицом Максима.
