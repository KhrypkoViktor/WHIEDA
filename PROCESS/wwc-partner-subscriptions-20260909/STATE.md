# STATE: wwc-partner-subscriptions-20260909

**Обновлено:** 2026-09-09
**Статус:** блоки 0, 1 и 2 выполнены. Следующий шаг: блок 3, платный доступ и repeat prices.

## Источник задачи

Прямое решение Виктора:

- платёж фиксируется вручную через Telegram-бота;
- вводятся партнёр, сумма и одна из двух валют (`RUB`, `BYN`);
- сервер сам ставит дату и время;
- доступ всегда продлевается на три календарных месяца;
- `grace` равен трём суткам;
- после `grace` весь партнёрский сайт перенаправляется на основной `wwc.best`;
- подтверждать платежи может только Виктор;
- фактический приём денег, налоги и чеки остаются вне системы;
- повторные цены и рабочие материалы доступны только при оплаченной подписке;
- всем существующим реальным партнёрским субдоменам однократно выдать доступ по
  21.09.2026 включительно; `paid_until` равен 22.09.2026 00:00 МСК, grace
  заканчивается 25.09.2026 00:00 МСК;
- технические hostname не получают стартовый доступ, новые сайты после seed
  включаются только после подтверждённой оплаты;
- бесплатный стартовый доступ не записывается как фиктивный платёж;
- для новой заявки с просроченным ref действует last-touch: и фактический
  владелец, и атрибуция переходят organic owner, первый ref остаётся только в
  исторических полях.

## Решения, принятые 2026-09-09 после preflight

Полные формулировки — в `TASK.md`, раздел 25.

1. Поддомен берётся инверсией действующего Core-resolver
   (`public_profile->>'subdomain'` -> `REF_TO_ISSUED_SUBDOMAIN` -> `ref_code`).
   Новую SQL-колонку не создавать. `dev`, `staging`, `admin`, `www` исключить.
   При дубликате поддомена snapshot не публикуется, остаётся last-known-good.
2. Заявка с просроченным ref: last-touch, владелец и атрибуция уходят organic owner.
3. Закрытая библиотека: приватный S3-совместимый bucket через абстракцию
   `StorageBackend`, подписанные URL с коротким TTL, локальный каталог — только
   dev/test adapter. LMS сейчас не строить, но хранилище проектировать под неё.
4. `check (access_months = 3)` остаётся строгим.
5. `/pay` и `/status` — точные slash-алиасы к `оплата` и `статус`.
6. Новая миграция регистрируется в `apply_staging_platform_all.ps1` и в
   `EXPECTED_ORDER` теста `test_staging_sql_order.py`.
7. RLS обязателен на обеих новых таблицах + негативные cross-tenant тесты.

## Репозитории и среды

- Канонический root: `D:\Projects\WHIEDA`, ветка `wip/consolidation-20260831`, HEAD `a4abf8e`.
  Рабочая копия грязная, изменения принадлежат другим задачам.
- Website: `D:\Projects\WHIEDA\03_Website\wwc-best`, ветка `fix/fedorov-runtime-context`,
  HEAD `e1cd939`, грязная, отстаёт от `master` на 58 commits.
- Core: `backend/platform-api` внутри root repo. SQL: `postgres/sql`.
- Production этим ТЗ не разрешён.

## Изоляция работы

- **Core worktree создан:** `D:\Projects\_worktrees\whieda-partner-subscriptions`,
  ветка `core/partner-subscriptions-20260909`, база `wip/consolidation-20260831` (`a4abf8e`).
  На момент записи чистый, коммитов нет.
- **Website worktree ещё не создан.** Создавать от `master` (`61188ef`), не от
  `fix/fedorov-runtime-context`.
- Интерпретатор: собственного `.venv` в worktree нет. Проверено, что рабочий вариант —
  venv основного checkout с `PYTHONPATH` на worktree; импортируется код worktree:

```text
cd D:/Projects/_worktrees/whieda-partner-subscriptions/backend/platform-api
PYTHONPATH=D:/Projects/_worktrees/whieda-partner-subscriptions/backend/platform-api \
  D:/Projects/WHIEDA/backend/platform-api/.venv/Scripts/python.exe -m pytest -q
```

## Базовый прогон тестов до начала работы

```text
869 passed, 83 failed, 1 skipped, 35 errors, 7 ошибок сбора
```

Все падения — отсутствующие внешние файлы (`n8n/current/*`, `qa/telegram_golden/*`,
три `.sql`), не связаны с этой задачей. Профильные файлы зелёные (65 passed):
`test_content_access.py`, `test_lead_attribution.py`, `test_theme_access.py`,
`test_lead_idempotency.py`, `test_partner_runtime_reconciliation.py`.
Отчёты блоков сравнивать с этой базой.

## Точки врезки, установленные фактически

- Схема: `postgres/sql/wwc_leads_p01_runtime_migration.sql` — `lead_actors` (нет
  числового Telegram ID), `referral_profiles` (`ref_code` — глобальный PK).
- Идентичность: `content_access_sessions.telegram_user_id` уже существует.
- Telegram: `app/telegram/processor.py::_process_core_telegram_update_scoped`,
  billing handler ставить до `handle_onboarding`; образец owner-only ingress —
  `app/telegram/admin_login.py`.
- Заявки: `app/leads/service.py::save_lead`, два `left join referral_profiles`.
- Поддомен: `app/ref/service.py::load_public_ref_by_subdomain`,
  `app/theme_access/service.py::REF_TO_ISSUED_SUBDOMAIN`.
- Повторные цены: `03_Website/wwc-best/src/data/repeat-purchase-prices.js`,
  потребители — `src/pages/price/index.astro` (импорт есть, `partnerSections`
  считается, но в разметку не выводится) и `src/pages/price/repeat/index.astro`
  (полностью статическая публичная страница).
- Контрольные значения утечки для теста: `1575` и `5250` в `dist/price/repeat/index.html`.

## Выполнено в блоке 1

- Миграция: `postgres/sql/platform_partner_subscriptions_v1.sql`.
- Runtime: `backend/platform-api/app/subscriptions/service.py`.
- Seed CLI: `backend/platform-api/app/subscriptions/seed_initial_access.py`.
- Unit/contract tests: `backend/platform-api/tests/test_partner_subscriptions.py`.
- PostgreSQL integration: `backend/platform-api/tests/test_partner_subscriptions_postgres.py`.
- Регистрация: `apply_staging_platform_all.ps1`, `staging_proof_lib.py` и локальный proof.
- Профильный прогон: `83 passed in 0.85s`.
- Живой локальный PostgreSQL: `1 passed in 0.45s`.

Docker Desktop на этой машине не запустился из-за повреждённого runtime path
`AppData/Local/Docker/run/dockerInference`. Проверка выполнена без Docker на отдельном
локальном PostgreSQL 18, порт `55439`; сервер после теста остановлен.

Старый clean-install дефект `wwc_leads_p01_runtime_migration.sql` подтверждён:
nullable `country_code/region_code` включены в primary key `lead_actor_roles`, и seed
падает на `NOT NULL`. Его не исправлять внутри этой задачи. Тест подписок использует
минимальную реальную prerequisite-схему; shared staging не изменялась.

## Выполнено в блоке 2

- `app/telegram/billing.py`: parser, owner guard, preview, status, due и callbacks.
- `app/telegram/processor.py`: billing callback до catalog callback, billing message
  до start/onboarding/navigation/advisor.
- `partner_payment_intents`: durable preview с TTL 10 минут, привязкой к tenant,
  user, chat и исходному message ID; RLS и короткий UUID callback.
- Подтверждение использует один DB connection и одну транзакцию для intent,
  subscription и ledger; проверено также при `database_pool_max=1`.
- `PLATFORM_BILLING_OWNER_TELEGRAM_ID` добавлен отдельно от admin allow-list.
- Критичный Telegram regression: `98 passed in 0.68s`.
- Живой локальный PostgreSQL: `1 passed in 0.81s`.

Широкий набор `test_telegram*.py` по-прежнему содержит baseline-проблемы:
3 collection error из-за отсутствующей `qa/telegram_golden`, ещё 13 старых тестов
advisor не мокируют новый `load_active_solution_bundles`. Профильные ingress,
binding, processor, navigation и route truth-table зелёные.

## Выполнено в блоке 3

- Core commit: `2e6f224` (`feat: protect partner repeat prices`).
- Website commit: `5f00fdd` (`feat: gate repeat prices by partner access`).
- `/api/v1/content-access/me` при каждом запросе вычисляет `partner_paid` по
  подтверждённому числовому Telegram ID и текущей подписке. Active и grace дают
  доступ, suspended и отсутствие подписки не дают. Telegram ID наружу не возвращается.
- Добавлен защищённый `/api/v1/content-access/repeat-prices`. Он независимо проверяет
  живую content session и подписку, возвращает `401/403` без права и ставит
  `Cache-Control: private, no-store`.
- Повторные цены перенесены в серверный JSON Core. Статический источник сайта и
  старый генератор удалены; `/price/` оставлен только с публичным розничным прайсом.
- `/price/repeat/` теперь публичная оболочка: сначала Telegram gate, затем проверка
  `partner_paid`, после неё загрузка цен из Core. Калькулятор и строки прайса до
  успешной проверки отсутствуют в DOM.
- В website postbuild включён стоп-тест утечки контрольных цен и товара, доступного
  только для повторной покупки. Проверка текущего `dist` зелёная.
- Core regression: `92 passed`. Website unit: `243 passed`. Playwright:
  `6 passed` на guest, verified-unpaid и paid для desktop/mobile. SEO: `6/6`.
- Общий live smoke: `6/7`; старый production `/api/advisor/query` вернул пустое тело.
  Этот endpoint и production в блоке 3 не менялись.
- QA-скриншоты сохранены в
  `docs/qa/screenshots/2026-09-09/repeat-paid-{desktop,mobile}.png`.
- Staging и production не изменялись. Эталонная страница проверена локально на mock
  Core. Живой PostgreSQL integration не повторялся: локальный PostgreSQL/Docker в
  текущей среде недоступен; границы подписки ранее доказаны в блоках 1-2.

## Выполнено в блоке 4

- Core commit: `640ee25` (`feat: add paid partner library`).
- Website commit: `29c1579` (`feat: add paid partner resources page`).
- Добавлена tenant-scoped таблица `partner_library_items` с RLS, состояниями
  draft/published/archived и выдачей только published.
- Добавлены защищённые list/download API. Каждый запрос повторно проверяет живую
  Telegram session и `partner_paid`; список не содержит `storage_key`, download
  возвращает короткоживущий URL и `Cache-Control: private, no-store`.
- Storage отделён интерфейсом `StorageBackend`. Production/staging работают только
  через приватный S3-совместимый bucket; локальный файловый адаптер разрешён лишь
  для dev/test и использует подписанный HMAC URL. Большие файлы через Core в
  production не проксируются.
- Добавлен manifest importer: tenant-prefix для каждого ключа, проверка существования
  объекта, dry-run SHA и обязательный `--expected-sha` для apply. Пример manifest
  содержит по одной позиции каждой согласованной категории.
- Создана `/partner/resources/`: гость видит только Telegram gate, unpaid видит
  продление, paid получает список из Core. Прямая ссылка на файл запрашивается лишь
  по нажатию. Повторные цены входят как защищённый внутренний инструмент.
- В nginx repo-template добавлен отдельный GET-only prefix `/api/v1/partner-library`.
  Живой nginx не изменялся.
- Проверки: Core regression `108 passed`; website unit `247 passed`; browser
  desktop/mobile `6 passed`; Astro build успешен. В `dist` нет тестовых названий,
  `storage_key` и закрытых путей.
- QA-скриншоты: `docs/qa/screenshots/2026-09-09/resources-paid-{desktop,mobile}.png`.
- Миграция не применялась, bucket и реальные материалы не загружались: для этого
  нужны выбранный S3-провайдер, credentials и утверждённые исходные файлы.
- Общий `test_staging_sql_order.py` сохраняет baseline 3 failures из-за отсутствующих
  в worktree старых `n8n/current` и SQL-файлов. Порядок новой миграции отдельно
  проверяется зелёным тестом блока 4.

## Следующий шаг

Блок 5: edge-map активных hostname, redirect всего просроченного партнёрского
поддомена на `https://wwc.best/` после grace и перевод новых заявок на органического
владельца. Сначала устранить или локально обойти расхождение четырёх hostname-карт,
не меняя live nginx.

## Пока не делать

- не применять SQL к shared staging/production;
- не менять live nginx;
- не подключать реальную оплату;
- не слать Telegram-сообщения реальным партнёрам;
- не смешивать этот diff с незакоммиченными изменениями root/website;
- не чинить существующий дрейф `postgres/sql` и apply-скрипта — это чужая задача;
- не создавать SQL-колонку `subdomain`.

## Техдолг, зафиксированный отдельно

Соответствие `ref_code -> hostname` имеет четыре расходящихся источника
(`ISSUED_SUBDOMAIN_TO_REF`, `SUBDOMAIN_TO_REF`, `referrals.js::subdomainToRef`,
`public_profile->>'subdomain'`). `fedorov` и `dev` есть только на сайте.
В этой задаче используется инверсия действующего resolver; сведение источников
к одному выносится отдельным ТЗ.

## Известный документальный факт

Указанный старым каноном `WWC_PERSONAL_SITE_SUBSCRIPTION_AND_ACCESS_TZ_V1_2026-08-29.md`
отсутствует. Контракт текущей задачи находится в `TASK.md` и не требует восстановления
старого файла.
