# STATE: wwc-partner-subscriptions-20260909

**Обновлено:** 2026-09-12
**Статус:** платёжный контур, бонусы и заявка на создание сайта развёрнуты на
Core staging и production в ревизии `9504441`. Курсы и библиотека оставлены
заглушкой. Website/UI сайта не синхронизировать в рамках этого среза.

**Выпуск:** production PostgreSQL сохранён в проверенном логическом backup до
миграции; оба API прошли `/health/live` и `/health/ready`; command menu
установлено для `whieda-advisor-bot`.

## Источник задачи

Прямое решение Виктора:

- платёж фиксируется вручную через Telegram-бота;
- вводятся партнёр, сумма и одна из двух валют оплаты сайта (`RUB`, `W$`);
  в базе `W$` хранится как `WUSD`;
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
3. Закрытая библиотека: текущий MVP хранит файлы в приватном каталоге Core VPS
   через `StorageBackend` и выдаёт подписанные URL с коротким TTL. S3-адаптер
   сохранён для будущего роста; Google Drive для платных файлов не использовать.
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
- Storage отделён интерфейсом `StorageBackend`. Изначально staging/production были
  ограничены S3, затем это решение заменено отдельным production-safe режимом
  `filesystem`; детали текущего решения находятся ниже. Dev/test режим `local`
  остаётся отдельным и запрещён в staging/production.
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

## Выполнено в блоке 5

- Core commit: `042fc12` (`feat: enforce paid partner edge access`).
- Закрытый `/v1/internal/edge/partner-hosts` работает внутри обычного tenant
  middleware, требует отдельный `PLATFORM_EDGE_SNAPSHOT_SECRET`, возвращает только
  active/grace hostname и подписывает точные байты ответа HMAC-SHA256. При неверном
  или отсутствующем секрете отвечает безопасным `404`.
- Sync на VPS сайта проверяет HTTP, подпись, точную схему, tenant, свежесть времени,
  hostname, deny-list технических имён и SHA списка. Пустой snapshot запрещён.
- Кандидат карты сначала проходит изолированный `nginx -t`, затем заменяется
  атомарно. После замены выполняется полный `nginx -t` и reload; ошибка возвращает
  предыдущие байты. Ошибка первой установки удаляет невалидную карту.
- Gate делает временный `302` на `https://wwc.best$uri`: путь сохраняется, query
  удаляется. Он предназначен только для wildcard-vhost партнёров; основной,
  staging, admin, API и media host в него не включаются.
- Systemd timer запускает sync раз в 60 секунд. Секрет читается из файла окружения
  с ожидаемыми правами `0600`, в CLI и лог не передаётся.
- Public ref теперь одинаково возвращает `referral_not_available` для отсутствующего,
  disabled, no-subscription и suspended партнёра.
- Новые заявки проверяют active/grace в БД. Исходный first-touch сохраняется в
  `initial_ref_code` и `first_ref_code`; недоступный ref не получает ownership.
  Fallback owner вынесен в `PLATFORM_ORGANIC_OWNER_ID`.
- Локальная регрессия: `95 passed, 1 skipped`; compileall и `git diff --check`
  успешны. Пропущен существующий PostgreSQL integration при недоступной локальной БД.
- Настоящий nginx-canary не выполнялся: nginx на Windows-host отсутствует. Создан
  отдельный loopback-шаблон `127.0.0.1:8443` и runbook. Общий `:443`, TLS, HTTP/2,
  gzip, DNS, shared staging и production не изменялись.

## Выполнено локально в блоке 6

- Core canary commit: `b9120b0` (`test: prove paid referral lead routing`).
- Website UI commit: `58c4f98` (`feat: show partner subscription status`).
- На отдельном локальном PostgreSQL 18, `127.0.0.1:55439`, выполнены два
  integration-теста: подписки/RLS/конкурентность и реальные INSERT заявок для
  active, grace, suspended. Результат `2 passed`; тестовый сервер остановлен.
- Доказано на реальных SQL-строках: active и grace получают ownership; suspended
  сохраняется в `initial_ref_code`/`first_ref_code`, получает `active_ref_code = null`,
  а `assigned_owner_id` и `attributed_owner_id` становятся organic owner. Public ref
  для active/grace доступен, для suspended отсутствует.
- UI показывает `Доступ оплачен до <дата>` для active, `Льготный срок до <дата>`
  для grace и понятный путь продления для suspended. Суммы, ledger и Telegram ID
  в интерфейс не попадают.
- Website: build успешен; unit `248 passed`; browser desktop/mobile `14 passed`;
  SEO `6/6`; repeat-price leak-check успешен. QA-скриншоты обновлены и просмотрены.
- Общий live smoke: `6/7`; прежний production advisor снова вернул пустое тело.
  Этот endpoint не менялся ни в одном блоке задачи.
- Оба worktree после коммитов чистые.

## Staging canary 2026-09-11

- Развёрнут Core commit `b8953f7` только на
  `/opt/whieda-platform-staging`; production не изменялся.
- Применена добавочная миграция `platform_partner_subscriptions_v1.sql`.
- В staging созданы 13 подписок: 12 проверенных персональных сайтов и отдельный
  технический `dev`. Всем выставлен `paid_until = 22.09.2026 00:00 МСК`, то есть
  доступ по 21 сентября включительно и grace по 24 сентября включительно.
- Служебный профиль `nnm` в seed не попал. Алиасы `onlineelena -> elena` и
  `olga-samtsova -> samtsova` сверены до записи.
- `PLATFORM_BILLING_OWNER_TELEGRAM_ID` установлен только в staging и указывает
  на Виктора. Подтверждать ручные платежи может только этот numeric Telegram ID.
- У `@wwc_admin_staging_bot` установлен компактный command menu. Старую большую
  reply-клавиатуру бот удаляет следующим своим сообщением.
- Отправлено одно тестовое уведомление для `dev` в чат Виктора, Telegram
  подтвердил `message_id = 38`. Текст построен из staging-БД; для страны РБ
  показан только белорусский способ оплаты.
- Единый операторский реестр создан первым листом `Partner_Subscriptions` в
  Google Sheet `1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4`. Есть поля страны,
  chat ID, признака `/start`, срока, grace, уведомлений и подтверждения платежа.
- Health после финального деплоя: `200`. Последняя резервная копия перед ним:
  `/opt/whieda-platform-staging/backups/partner-subscriptions-20260910T210022Z.tar.gz`.
- Проверки шаблона, Telegram billing, подписок и меню: `87 passed`. Локальный
  `ruff` отсутствует; тесты и `git diff --check` прошли.

### Валюты оплаты сайта

- Коммит `ec484b0` устранил расхождение между уведомлением и журналом платежей:
  для подписки разрешены только `RUB` и WHIEDA-доллары. Пользователь пишет и
  видит `W$`, в базе хранится `WUSD`. Каталожные цены в `BYN` не изменялись.
- Перед миграцией на Core staging проверено: в ledger и незавершённых intents
  нет ни одной записи `BYN` или `WUSD`; 13 подписок сохранены без изменений.
- Миграция `platform_partner_subscription_currency_v2.sql` применена только к
  staging. Read-back подтвердил оба ограничения БД: `RUB`/`WUSD`, без `BYN`.
- В работающем staging-контейнере команда `оплата ref:dev 30 W$` разобрана как
  `amount_minor = 3000`, `currency = WUSD`, показана как `30 W$`; команда с
  `BYN` отклонена. Health после перезапуска: `200`.
- Резервная копия кода перед обновлением:
  `/opt/whieda-platform-staging/backups/wusd-currency-code-20260910T211305Z.tar.gz`.
- Локальная профильная регрессия: `91 passed`; `git diff --check` успешен.

В canary не входят website, nginx edge-redirect, повторные цены в UI, курсы,
storage и рассылка реальным партнёрам.

## Read-only preflight staging 2026-09-10

- Core staging жив: `/health/ready` вернул `200`; отдельные `api`, `worker` и
  `redis` работают из `/opt/whieda-platform-staging`, порт API `8081`.
- В staging-БД есть prerequisite-таблицы `referral_profiles` и `lead_actors`, но
  таблиц подписок, payment ledger и библиотеки ещё нет. Миграции задачи не применялись.
- В staging env пока нет billing owner, edge secret и S3-настроек. Username
  `sunraysword` однозначно связан с одним numeric Telegram ID, входящим в
  staging admin-list; сам ID не выводился. Его можно безопасно назначить при deploy.
- Staging Telegram использует `wwc_admin_staging_bot`, но
  `CORE_ROUTE_TELEGRAM=legacy`; для проверки команды оплаты нужен отдельный
  контролируемый перевод staging-бота на Core.
- Website: production отдаёт `61188ef`, staging отдаёт `556044f`, локальный
  кандидат `58c4f98`. Кандидат на staging ещё не выкладывался.
- Живой wildcard nginx не содержит `/api/v1/partner-library` и edge-gate.
  Канонический nginx overlay маршрут библиотеки уже содержит; его контракт теперь
  защищён тестом, коммит `44789f9`, результат `6 passed`.
- Приватный S3-совместимый bucket и credentials не настроены. После выбора
  filesystem это больше не является блокером canary библиотеки.
- Ни staging, ни production во время preflight не изменялись.

## S3 integration 2026-09-10

- Выбран Contabo Object Storage EU: endpoint `https://eu2.contabostorage.com`,
  region `default`, обязательный addressing style `path`.
- Коммит `a243953` добавил S3 v4/path-style конфигурацию, закрытый env-шаблон,
  runbook и утилиту `partner_library_s3.py` с командами `check`, `probe`, `upload`.
- `check` проверяет bucket и запрещает публичную ACL. `probe` загружает случайный
  маленький объект, получает его по 60-секундной подписанной ссылке и удаляет.
  `upload` принимает только storage key с префиксом tenant и ставит private ACL.
- Профильная регрессия после изменения: `103 passed`; compileall успешен.
- Локальная Docker-сборка не выполнена: Docker Desktop на рабочей машине выключен.
  Установка boto3 уже входит в `pyproject.toml`; образ проверить при staging build.
- Website worktree и живой website staging не изменялись из-за параллельной
  переделки меню. Синхронизация UI выполняется позже отдельным срезом.
- Физический bucket пока не создан: в окружении нет Contabo API credentials или
  S3 access/secret keys. Это ручное действие владельца аккаунта с оплатой услуги.

## Текущее хранилище MVP 2026-09-10

- Решение о немедленной покупке Contabo Object Storage отменено. S3-код сохранён
  как готовый путь миграции, но текущая работа от внешнего провайдера не зависит.
- Коммит `df17681` добавил production-safe backend `filesystem`. Файлы лежат на
  Core VPS в `/var/lib/wwc-partner-library/<tenant>/`, вне репозитория, Docker image,
  nginx document root и статического сайта. В API-контейнер каталог монтируется
  read-only.
- Список материалов и создание ссылки требуют активной Telegram-сессии и статуса
  подписки active/grace. Затем Core отдаёт файл по HMAC-ссылке с TTL 300 секунд;
  испорченная или просроченная ссылка получает `403`.
- В Git находятся только env-шаблон и runbook. Реальный signing secret хранится
  только в server `.env`. Путь на сервере резервируется отдельно от релизов.
- Google Drive не использовать как прямое хранилище платной библиотеки: полученная
  ссылка продолжает работать вне проверки подписки и может свободно пересылаться.
- Профильная регрессия после изменения: `106 passed`; compileall и
  `git diff --check` успешны. Website и живые серверы не изменялись.

## Следующий шаг

1. Виктор проставляет `РБ`/`РФ` на листе `Partner_Subscriptions`.
2. До массовой рассылки сверить неизвестные chat ID и признак `/start`; партнёрам
   без chat ID отправить личную просьбу открыть `@wwc_admin_staging_bot` и нажать
   `/start`.
3. На `dev` проверить команды `статус ref:dev`, RUB/W$ preview, cancel/confirm,
   повторное подтверждение и отказ для чужого Telegram ID.
4. После приёмки отдельно синхронизировать новый website UI с Core staging.
5. Edge redirect, массовую рассылку и production выпускать отдельным решением.

## Пока не делать

- не применять SQL к production;
- не менять live nginx;
- не разворачивать библиотеку курсов и файловое хранилище: пока это заглушка;
- не синхронизировать текущую ветку с активно меняющимся website;
- не подключать реальную оплату;
- не слать Telegram-сообщения реальным партнёрам до заполнения стран и chat ID;
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

## Финальный локальный срез 2026-09-10

- Коммит `53db8ad` заменил постоянную reply-клавиатуру на стандартное Telegram
  command menu. `/start` удаляет старую большую клавиатуру; `/products`,
  `/calculator`, `/business`, `/company`, `/match`, `/events` идут в существующие
  детерминированные обработчики, а не в advisor.
- Добавлен owner-safe скрипт `configure_telegram_menu.py`: он получает bot token
  через существующий secret-ref, вызывает `setMyCommands` и `setChatMenuButton` и
  не печатает токен.
- Профиль оплат, подписок, доступа, edge и Telegram: `151 passed`; compileall и
  `git diff --check` успешны.
- Текущий `staging.wwc.best` просмотрен read-only на desktop/mobile. Новый header,
  мобильный burger и боковое меню уже являются UI-каноном; будущий статус подписки
  и закрытые пункты встраиваются в него отдельной синхронизацией, не в старый header.
- Staging не выкатывался по прямому указанию Виктора.
