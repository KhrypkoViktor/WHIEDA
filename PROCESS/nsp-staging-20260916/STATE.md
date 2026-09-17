# NSP staging — состояние (NSP-агент)

Ветка `nsp/canary-integration` слита в `master` ff-only (`84911a6`, `a125801`, 17.09), три запроса Core закрыты в `99ad696`. Дальше — новая ветка `nsp/…` от свежего master на каждую тему.
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
| Поддержка | `/support` подключён: `PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID` в `secrets/api.env` staging NSP (аккаунт поддержки клиента, не имя — человек может меняться; id только в env, не в git/БД). Форум-группа не создана: тикеты идут админу в личку. Чтобы включить темы — группа с topics, бот админом, `/forum` от аккаунта поддержки |
| Golden | **79/79** (`qa/nsp_golden/`, прогон на `master@73d958d`+ветка, отчёт `reports/nsp_golden_run_20260917b.tsv`) |

## Файлы этой ветки (только зона nsp)

- `backend/platform-api/app/tenants/` — профиль NSP (тексты), курс НБРБ (`fx_nbrb.py`). `voice.py` (Core, `73d958d`) читает его через `get_tenant_profile`.
- `backend/platform-api/tests/test_tenant_profiles.py`, `test_tenant_fx_nbrb.py`.
- `qa/nsp_golden/` — генератор, 79 кейсов, in-process runner (без Telegram).
- `PROCESS/nsp-staging-20260916/nsp-stagingctl` — копия `/usr/local/bin/nsp-stagingctl` с сервера: `status|env-init|up|nginx-enable|down|logs|webhook-*|menu-set|preflight|apply-package|binding-stage`.

## Запросы Core-агенту

Закрыты в `99ad696`: порог SKU ≥ 17 в `catalog.py`; старые golden под `qa/telegram_golden/` удалены; `.pyc` сняты с учёта.
Открыт (не блокер): `app/telegram/support.py:309` — текст при `no_admin` брать из tenant-профиля, без имён.

## Не сделано / ждёт

- 24ч canary Максим+Виктор — не проводился.
- 39 SKU без подтверждённой цены/приёма — ждут Максима (мастер-таблица у него).
- Сравнения, маркетинг-план, бизнес-FAQ — ждут ответов Максима.
- Production — после canary и договора с юрлицом Максима.
