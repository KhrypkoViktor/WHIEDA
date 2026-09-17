# NSP staging — состояние (NSP-агент)

Ветка `nsp/canary-integration` слита в `master` ff-only (`84911a6`, `a125801`, 17.09), три запроса Core закрыты в `99ad696`. Дальше — новая ветка `nsp/…` от свежего master на каждую тему.
Тенант `nsp-maxim`, staging-only. `--publish` не выполнялся.

## Что живое (17.09.2026)

| Что | Значение |
|---|---|
| Сервер | `185.252.232.93` (`ssh whieda-n8n`), контейнер `whieda-shared-staging-api`, образ `whieda-shared-staging-core:master-0a4ef1a` (собран из `origin/master` на сервере, `nsp-stagingctl up master-<sha>`) |
| Домен / бот | `https://nsp-staging.sysarch.pro`, `@NSP_Stage_bot`, binding `nsp-maxim-canary-bot` active, webhook стоит |
| БД | `whieda_shared_staging` (контейнер `whieda-staging-db`), не production |
| Пакет | `nsp-maxim-canary-v4` (NSP-репо, `gate-d-canary-content@cb7f2d9`), **60 SKU** + полные карточки (Коротко / Для кого / Когда рассматривают / Как используют) + **36 бизнес-FAQ** (компания, качество, правила партнёра, PV, план, ранги, поддержка) |
| Медиа | `https://media.sysarch.pro/nsp-media/nsp-maxim/<sku>/<file>`, 97/97 товаров с фото; media-manifest 60 строк `ready`, `sources/<sku>/` на сервере |
| Меню | `/start /products /business /company /support` |
| Поддержка | `/support` подключён: `PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID` в `secrets/api.env` staging NSP (аккаунт поддержки клиента, не имя — человек может меняться; id только в env, не в git/БД). Форум-группа не создана: тикеты идут админу в личку. Чтобы включить темы — группа с topics, бот админом, `/forum` от аккаунта поддержки |
| Golden | **261/265** (`qa/nsp_golden/`, 60 SKU + 14 бизнес-FAQ, `master-0a4ef1a`, отчёт `reports/nsp_golden_run_20260917_v4d.tsv`). 4 провала = баг SKU 550 (ниже). Сравнения «сравни X и Y» работают на любой паре из 60 |
| После apply | Обязательно: `python qa/nsp/content/render_business_faq_sql.py` (NSP-репо) → `psql -f` в staging-БД — ставит aliases/priority бизнес-FAQ, пока импортёр их не читает |

## Файлы этой ветки (только зона nsp)

- `backend/platform-api/app/tenants/` — профиль NSP (тексты), курс НБРБ (`fx_nbrb.py`). `voice.py` (Core, `73d958d`) читает его через `get_tenant_profile`.
- `backend/platform-api/tests/test_tenant_profiles.py`, `test_tenant_fx_nbrb.py`.
- `qa/nsp_golden/` — генератор, 79 кейсов, in-process runner (без Telegram).
- `PROCESS/nsp-staging-20260916/nsp-stagingctl` — копия `/usr/local/bin/nsp-stagingctl` с сервера: `status|env-init|up|nginx-enable|down|logs|webhook-*|menu-set|preflight|apply-package|binding-stage`.

## Запросы Core-агенту

Закрыты в `99ad696`: порог SKU ≥ 17 в `catalog.py`; старые golden под `qa/telegram_golden/` удалены; `.pyc` сняты с учёта.
Открыт (не блокер): `app/telegram/support.py:309` — текст при `no_admin` брать из tenant-профиля, без имён.

**Новый, реальный баг (не мой файл, только диагностировал):** `app/advisor/sql/ambiguity.py:120` — `weak_color_or_belt_clarification()` вызывается в `try_ambiguity_clarification` без проверки tenant/best_product (в отличие от соседней ветки `activator_exact`, у которой такая проверка есть). Хардкод трёх WHIEDA-товаров по цветовому стему («красн» → SKU F001-02 «Эликсир Фохоу» и т.д.) перехватывает NSP-товар 550 «Красный клевер» (exact alias match) на любой вопрос — карточка/цена/фото/faq все уходят в один и тот же WHIEDA-clarification вместо структурированного ответа. Тот же паттерн, что чинили для «Подарка» в `73d958d`. Тот же вызов дублируется в `engine.py:1235` (`_should_use_knowledge_gap`). Фикс по образцу activator: пропускать блок, если `tenant_id != "whieda"` (SKU там в формате WHIEDA, F001-02/F002-02/F003-02/T003 — не NSP). 4/251 golden-кейсов (SKU 550) провалены из-за этого, отчёт `reports/nsp_golden_run_20260917_v3full.tsv`.

## Запросы Core-агенту после v4 (все — одна-две строки, файлы Core)

1. `scripts/shared_staging_canary/catalog.py:359-372` (faq insert) — брать `aliases = row.get("aliases") or title` и `priority = row.get("priority") or 100`; сейчас aliases=title, priority=100, а `find_business_faq` читает top-200 по priority — при 240 товарных FAQ бизнес-строки выпадают. Пока лечу SQL-ом после каждого apply.
2. `catalog.py:315-340` (cards insert) — добавить `what_to_expect_soft` (и `contraindications_short`) в insert/update: колонки есть в таблице и в `telegram_card.py`, импортёр их не пишет.
3. `app/telegram/navigation.py:154` — вопрос кнопки «Бизнес» `какие виды входа` для не-home tenant уходит в `has_basket_intent` → WHIEDA-корзина «на какой бюджет в BYN/PV». Для NSP нужен вопрос из профиля (`get_tenant_profile(...).nav_business_question`, добавлю в `app/tenants/nsp.py` по вашему знаку) или пропуск basket-ветки при `calculator=False`.
4. `engine.py` — `какая партнёрская цена` без товара → «О каком товаре хотите узнать цену?»; для tenant с `partner_prices=False` лучше отдать бизнес-FAQ (`biz-partner-price`) — проверка `wants_partner_price(question) and not product`.
5. `engine.py` — `есть ли сертификаты` без товара → `knowledge_gap` (медиа-ветка). Если товар не резолвится, а `find_business_faq` находит строку — вернуть её.
6. `app/advisor/sql/ambiguity.py:120` + `engine.py:1235` — цветовой стем «красн» (SKU 550 «Красный клевер»), см. выше.

## Не сделано / ждёт

- 24ч canary Максим+Виктор — не проводился.
- 60/97 SKU живых на staging (было 58, +2 «Брест комплекс», «Босвелия Плюс НСП» — очищенный от claims текст приёма, см. `nsp-maxim-canary-v3`). Ещё 37 SKU без подтверждённой цены/приёма ждут Максима (мастер-таблица у него); из них 5 SKU (1506, 1841, 999, 832, 1894) вообще без пригодного текста приёма — либо обрезан харвестом (832, 400 симв. лимит), либо там нет отдельного предложения про дозировку без claims (1506, 1841, 999), либо текст явно не про этот товар (1894 — «Хром Хелат» несёт текст про красный клевер, похоже на баг харвеста); RU22485 вдобавок без цены. 5 SKU (30, 70, 90, RU22485, RU24074) — цена не найдена ни в одном источнике (ни Catalogue_2026, ни офсайт).
- Сравнения, маркетинг-план, бизнес-FAQ — ждут ответов Максима.
- Production — после canary и договора с юрлицом Максима.
