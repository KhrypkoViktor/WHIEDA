# NSP Tenant Launch Contract V1

Дата: 2026-08-19  
Статус: исполнимое ТЗ разработчику на запуск второго коммерческого tenant  
Заказчик платформы: Виктор Хрипко  
Клиент tenant: Максим, команда NSP

## 1. Назначение документа

Развернуть для Максима отдельного Telegram-советника NSP внутри общей платформы
WHIEDA Platform Core. Это первый коммерческий запуск второго tenant и одновременно
проверка того, что платформа действительно масштабируется на других клиентов.

Tenant здесь означает изолированную клиентскую организацию внутри одной платформы:
у неё собственные знания, товары, цены, пользователи, Telegram-бот, настройки,
расходы и журналы, но общий код и инфраструктурные контракты платформы.

Этот документ не заменяет базовую архитектуру. Он применяет её к конкретному
клиенту NSP:

- `WHIEDA_PLATFORM_TENANT_CONTRACTS_V1_2026-08-02.md`;
- `WHIEDA_PLATFORM_SCALE_ARCHITECTURE_V1_2026-08-02.md`;
- `WHIEDA_PLATFORM_SCALE_DEVELOPER_PLAN_V1_2026-08-02.md`;
- `backend/platform-api/docs/TENANT_ONBOARDING_RUNBOOK.md`;
- `01_Context/Handoff/External_Client_Packs/WHIEDA_NSP_TELEGRAM_ADVISOR_INFORMATION_COLLECTION_V1_2026-08-14.md`.

При противоречии действуют инварианты tenant-контракта: никакого fork кода,
никакого доверия к `tenant_id` из клиента и никаких межклиентских утечек.

## 2. Результат первого релиза

Максим получает отдельного Telegram-бота, который на материалах NSP:

1. приветствует пользователя и объясняет возможности;
2. показывает каталог и карточки товаров;
3. отвечает по цене, партнёрской цене, PV/баллам и вариантам товара;
4. показывает разрешённые фото, видео, PDF и ссылки на первоисточник;
5. сравнивает товары по структурированным параметрам;
6. отвечает на утверждённые вопросы по компании и маркетинг-плану;
7. сохраняет контекст диалога и понимает короткие follow-up вопросы;
8. при неоднозначности задаёт один понятный уточняющий вопрос;
9. при отсутствии знания направляет к Максиму или назначенному эксперту, не
   выдумывая ответ и не упоминая технические внутренности;
10. не видит и не выдаёт никакие данные WHIEDA или других клиентов платформы.

Release 1 является SQL-first/structured advisor. Dify, RAG, интернет-поиск,
коуч, генерация контента, автоматические выплаты, регистрация партнёров,
рассылки и сайт NSP в этот релиз не входят.

## 3. Коммерческие и имущественные границы

- Код, общая схема БД, Platform Core, инструменты импорта, deployment и общая
  архитектура остаются собственностью владельца платформы.
- Максим владеет переданными им исходными материалами, своей экспертизой,
  нормализованным клиентским контентом и клиентскими данными в рамках договора.
- Максим и его администраторы видят только tenant NSP.
- Главный администратор платформы может получить доступ для поддержки,
  безопасности, аудита и восстановления. Такие действия журналируются.
- Экспорт данных NSP должен быть возможен в согласованном структурированном
  формате. Экспорт не включает исходный код платформы и данные других tenant.
- Остановка обслуживания не удаляет данные автоматически. Tenant переводится в
  `suspended`; порядок экспорта, хранения и удаления фиксируется отдельно.

## 4. Идентификаторы и конфигурация

До production утвердить карточку tenant:

| Поле | Предлагаемое значение | Правило |
|---|---|---|
| `tenant_id` | `nsp-maxim` | immutable lowercase slug после production activation |
| `display_name` | `NSP — команда Максима` | можно менять |
| `status` | `draft` → `active` | active только после acceptance gate |
| `default_locale` | `ru` | Release 1 |
| `default_country` | определить из материалов | не угадывать |
| `bot_binding_id` | случайный opaque id | не должен раскрывать tenant/token |
| `domain` | отсутствует в Release 1 | Telegram-only запуск допустим |

Почему не `nsp`: один лидер не должен автоматически занимать namespace всей
компании. Если позднее NSP официально подключается как единая организация,
создаётся отдельный tenant или проводится согласованная миграция. Код и импортеры
не должны зависеть от конкретного slug.

Начальные entitlements:

- `structure_basic=true`;
- `partner_leads=false`, если заявки не входят в отдельно согласованный релиз;
- `deep_coach=false`;
- `broadcast=false`;
- `custom_domain=false`.

Лимиты первого релиза задаются конфигурацией, а не кодом: число пользователей,
запросов в минуту, суточный лимит, допустимый размер медиа и журнал использования.

## 5. Жёсткие запреты

Разработчику запрещено:

- копировать `advisor-whieda-*` workflow и переименовывать его в NSP;
- создавать отдельную ветку бизнес-логики или отдельную SQL schema только для NSP;
- hardcode `nsp-maxim`, `whieda`, Telegram id Максима или bot token в коде;
- принимать `tenant_id`, owner или role из Telegram payload/frontend как доверенные;
- использовать общий Telegram token для WHIEDA и NSP;
- загружать RAW напрямую в production runtime;
- смешивать WHIEDA и NSP в одной Google Sheet без строгого server-side scope;
- включать Dify/RAG как способ скрыть пробелы structured-корпуса;
- менять live WHIEDA ради запуска NSP без отдельного canary и rollback;
- помещать секреты, токены, пароли, ключи Google или URL с токенами в git,
  отчёты, логи и screenshots;
- считать задачу завершённой только потому, что webhook отвечает HTTP 200.

## 6. Входные материалы и реестр источников

Исходный пакет Максима сначала инвентаризировать. Создать versioned manifest:

`01_Context/Handoff/External_Client_Packs/NSP/NSP_SOURCE_MANIFEST_V1.tsv`

Минимальные поля:

```text
source_id
source_type
title
original_url_or_path
owner
received_at
effective_from
effective_to
is_current
content_area
product_hint
contains_personal_data
contains_medical_claims
publication_permission
processing_status
notes
```

Content areas:

- products;
- prices;
- promotions;
- business_plan;
- company;
- faq;
- objections;
- media;
- documents;
- chats_reviews;
- archive.

Каждый исходник получает `source_id`. У нормализованной записи должен быть
provenance до конкретного файла/ссылки и по возможности страницы, строки,
таймкода или фрагмента. Если дата или актуальность неизвестна, запись получает
review status, а не публикуется как факт.

Не делать NotebookLM обязательной частью pipeline. Его вывод может быть принят
как дополнительный RAW-кандидат, но публикация происходит только через
канонический importer, schema validation, review и staging.

## 7. Каноническая структура NSP master

Использовать тот же логический schema contract, что у WHIEDA. Допускаются новые
универсальные колонки и сущности, но не NSP-специфичные таблицы.

Минимальный набор данных Release 1:

### 7.1 Products

- стабильный `sku` или клиентский product id;
- название и display name;
- категория;
- статус `active/inactive/archive`;
- короткое описание;
- варианты и объём/комплектация;
- ссылка на официальный источник;
- дата актуальности.

### 7.2 Product cards

- `sku`;
- короткий ответ для Telegram;
- ключевые особенности;
- кому/для какой бытовой задачи предназначен без медицинского диагноза;
- способ применения только из разрешённого источника;
- ограничения и обязательные предупреждения;
- source ids;
- review/publication status.

### 7.3 Prices and points

- `sku`;
- market/country;
- currency;
- retail/partner/initial price, если такие типы реально существуют;
- PV/points;
- effective dates;
- source id;
- `missing`, а не `0`, если цена неизвестна.

### 7.4 Aliases

- разговорные названия;
- опечатки;
- сокращения;
- цвета/варианты;
- связь с одним SKU или статус ambiguity.

### 7.5 FAQ and business FAQ

- intent;
- варианты вопроса;
- канонический ответ;
- уточняющий вопрос;
- escalation target;
- source ids;
- effective dates;
- risk/review flags.

### 7.6 Media and documents

- media id;
- SKU/topic;
- type;
- original URL;
- title;
- permission/publication status;
- medical and personal-data flags;
- checksum/version where possible.

Сначала импортировать минимально достаточный corpus для 10–20 главных товаров и
базового маркетинг-плана. Остальной материал добавлять итерациями. Не задерживать
staging из-за неполного архива, но честно показывать coverage.

## 8. Publication firewall и compliance

RAW, candidate, reviewed и published — разные состояния. Только `published`
доступен советнику.

Автоматически отправлять на ручной review:

- обещания лечения, излечения или гарантированного результата;
- отмену лекарств, операций или обращения к врачу;
- диагнозы и тяжёлые заболевания;
- рекомендации детям, беременным и другим чувствительным группам;
- персональные медицинские истории;
- неподтверждённые доходы и гарантии заработка;
- отзывы без подтверждённого разрешения на публикацию;
- устаревшие цены, акции и маркетинг-план;
- противоречия между источниками.

Советник не должен автоматически превращать отзыв или опыт сообщества в
рекомендацию. Небезопасный RAW сохраняется для анализа, но не публикуется.

## 9. Platform foundation: обязательная проверка до NSP seed

Перед добавлением данных клиента разработчик проводит factual audit и обновляет
отчёт. Не доверять старому статусу документов.

Проверить:

1. применены ли `platform_tenant_registry_v1.sql` и tenant RLS на staging;
2. существуют ли `tenants`, `tenant_domains`, `tenant_bot_bindings`,
   `tenant_entitlements`, `tenant_provider_bindings`, `tenant_usage_ledger`;
3. все ли runtime-таблицы advisor, media, prices, aliases, FAQ, users, sessions,
   leads и audit имеют настоящий tenant scope;
4. есть ли tenant-aware indexes;
5. отсутствует ли production fallback неизвестного host/binding на WHIEDA;
6. нет ли hardcoded `client_id='whieda'` в hot path, импортерах и sync jobs;
7. работают ли RLS/policies под реальными database roles, а не только в unit mock;
8. жив ли WHIEDA Core baseline до изменений.

Если tenant foundation неполон, сначала довести его на staging. Не создавать
временную NSP-копию для обхода этого этапа.

## 10. Этапы реализации

### Этап 0. Baseline и безопасная точка возврата

- Зафиксировать git commit/working tree и не включать посторонние изменения.
- Снять read-only карту live/staging routing и Postgres target.
- Сохранить backup затрагиваемых workflow/config и schema-only dump.
- Прогнать текущий WHIEDA P0 smoke до изменений.
- Создать `NSP_TENANT_LAUNCH_PREFLIGHT_REPORT.md` с фактами и найденными gaps.

Gate: baseline воспроизводим, секретов в артефактах нет.

### Этап 1. Доказать реальную изоляцию двух tenant

- Применить additive migrations только на staging.
- Seed `whieda` и synthetic tenant `isolation-test`, без client secrets.
- Расширить integration tests: products, prices, aliases, FAQ, media, sessions,
  Telegram bindings, users, audit, usage.
- Выполнить прямые DB-проверки под runtime role.
- Исправить tenant-unaware запросы в общем коде.

Gate: cross-tenant read/write = 0; unknown binding/host не получает WHIEDA.

### Этап 2. Создать NSP tenant в staging

- Создать `nsp-maxim` со статусом `draft`.
- Добавить entitlements и limits.
- Создать provider binding на NSP master source без хранения secret value в БД.
- Создать роли: platform owner, tenant admin Максим, content editor/reviewer при
  наличии подтверждённых Telegram ids.
- Создать отдельный usage/cost scope.

Gate: tenant существует, но live traffic и production bot не включены.

### Этап 3. Параметризованный importer

- Importer принимает обязательный `--tenant-id` и проверяет tenant registry.
- Поддерживает `--validate`, `--dry-run`, `--full-staging-run`.
- Не имеет default tenant для write operations.
- Идемпотентен: повтор не создаёт дублей.
- Изменённая запись создаёт новую версию или audit revision по действующему
  платформенному контракту.
- Ошибка в середине не оставляет частичные published данные.
- Выдаёт counts: read, valid, candidate, review_required, published, rejected,
  reused, new_version.
- Публикация цен выполняется только с currency, market и effective date.

Gate: два повтора дают одинаковое состояние; аварийный тест оставляет чистый
staging; WHIEDA counts/hash не изменились.

### Этап 4. Сборка минимального NSP corpus

- Заполнить source manifest.
- Выделить активные товары и актуальный price source.
- Собрать upload-ready candidates.
- Провести content/compliance review.
- Опубликовать только approved subset в NSP staging runtime.
- Сформировать coverage/gap report.

Минимум для canary:

- 10–20 приоритетных товаров или весь каталог, если он меньше;
- цена и PV для каждого опубликованного SKU либо честный `price_missing`;
- минимум одно основное фото на товар при наличии разрешения;
- 50+ aliases/опечаток суммарно;
- 30+ product FAQ;
- 15+ business/company FAQ;
- 30–50 клиентских smoke-вопросов.

Gate: ни один ответ не использует WHIEDA fallback/content.

### Этап 5. Отдельный Telegram bot binding

- Максим/владелец создаёт отдельного бота через BotFather и передаёт token только
  через согласованный секретный канал.
- Сохранить token и webhook secret в secret store/environment.
- Получить bot id и создать opaque binding.
- Webhook: `POST /v1/telegram/{binding_id}/webhook`.
- Проверять Telegram secret header до обработки update.
- Повторный `update_id` не создаёт повторную обработку/отправку.
- Команды и кнопки вызывают общие Core services в scope `nsp-maxim`.
- Настроить имя, аватар, описание, команды и privacy mode по согласованной
  продуктовой роли.

Gate: token/binding NSP не принимает запросы как WHIEDA и наоборот.

### Этап 6. Advisor UX Release 1

Реализовать/проверить сценарии:

- `/start` и обычные приветствия;
- «что ты умеешь»;
- каталог/список категорий;
- карточка товара;
- цена/PV и варианты цены;
- фото, видео и документ;
- сравнение двух товаров;
- follow-up после карточки;
- опечатки и алиасы;
- неоднозначное название;
- вопрос из нескольких частей;
- FAQ компании/маркетинг-плана;
- неизвестный вопрос;
- запрос человека/эксперта;
- безопасность при медицинском или финансовом обещании.

Fallback не должен отвечать «товар не найден», если пользователь не искал товар.
Он должен назвать доступные направления и предложить конкретные кнопки/уточнение.

Цель быстрого слоя: p95 server response не более 3 секунд на staging profile;
n8n и Dify выключены — поддерживаемые SQL-first ответы продолжают работать.

### Этап 7. Acceptance и canary

Прогнать:

- automated contract tests;
- cross-tenant suite;
- NSP content smoke;
- multi-turn smoke;
- duplicate Telegram update/callback;
- n8n-down и Dify-down;
- database timeout/recovery;
- media URL validation;
- price freshness and missing-price behavior;
- ручной визуальный smoke в Telegram Максима и владельца платформы.

Canary users: Максим, Виктор и до трёх назначенных тестировщиков. Не приглашать
всю структуру до письменного acceptance отчёта.

Gate: критические сценарии 100%; нет cross-tenant mismatch; p95 ≤3 sec;
нет PII/secrets в logs; пользовательские ответы визуально подтверждены.

### Этап 8. Production activation

- Повторить additive migrations на production после backup/preflight.
- Seed production tenant сначала `draft`.
- Импортировать approved snapshot с проверкой hash/counts.
- Создать production bot binding.
- Установить webhook и выполнить signed probe.
- Активировать tenant только после green canary.
- Наблюдать минимум 24 часа или согласованное окно до массовой выдачи.

Никакие route switches WHIEDA не менять, если это не является отдельно
обоснованной общей платформенной миграцией.

## 11. Обязательный тестовый контракт

| Case | Ожидание |
|---|---|
| NSP bot → NSP product | корректный NSP ответ |
| NSP bot → WHIEDA-only SKU/alias | честное отсутствие, без WHIEDA данных |
| WHIEDA bot → NSP-only SKU/alias | честное отсутствие, без NSP данных |
| forged `tenant_id=whieda` в payload NSP | игнорируется; scope NSP |
| NSP binding + неверный secret | `403`, обработки нет |
| неизвестный binding | controlled `404`, без default tenant |
| одинаковый session/chat id в двух tenant | два независимых контекста |
| одинаковый SKU в двух tenant | возвращается версия текущего tenant |
| одинаковый alias в двух tenant | разрешается внутри текущего tenant |
| price отсутствует | `price_missing`, не ноль и не чужая цена |
| устаревшая акция | не публикуется как текущая |
| media NSP | только approved NSP media |
| duplicate Telegram update | один logical response |
| importer повторён | дублей нет |
| importer аварийно остановлен | partial publish отсутствует |
| tenant suspended | controlled unavailable message, WHIEDA продолжает работать |

К этим cases добавить corpus smoke из реального языка Максима и его команды.

## 12. Логи, аудит, стоимость и приватность

- Каждый request имеет trace id и server-resolved tenant id.
- Не логировать raw Telegram text, bot token, webhook secret, телефоны и полный
  профиль пользователя.
- Допустимы update id, message id, irreversible text hash, intent, SKU, latency,
  result status и error code.
- Usage ledger пишет стоимость/объём отдельно для `nsp-maxim`.
- Отчёт должен показывать: requests, active users, intents, unanswered gaps,
  errors, p50/p95, deliveries, cost estimate.
- Права Максима ограничены его tenant; изменение ролей и export журналируются.
- Сроки хранения диалогового состояния и персональных данных зафиксировать до
  массового запуска; бессрочное хранение не принимать как default.

## 13. Rollback

Rollback NSP не должен затронуть WHIEDA:

1. отключить `tenant_bot_bindings.status` для NSP;
2. перевести tenant в `suspended`;
3. снять/заменить Telegram webhook;
4. остановить NSP sync/import jobs;
5. проверить WHIEDA health и один WHIEDA canary;
6. сохранить failed trace, audit и snapshot для разбора;
7. не делать destructive schema downgrade и не удалять client data в аварии.

Автоматический повод к rollback:

- любая утечка между tenant;
- неправильная цена или товар другого tenant;
- duplicate/lost writes;
- error rate ≥1% в canary окне;
- p95 >3 секунд устойчиво для structured path;
- сломанный audit/usage attribution;
- утечка token/secret/PII.

## 14. Артефакты, которые обязан сдать разработчик

1. `NSP_TENANT_LAUNCH_PREFLIGHT_REPORT.md`.
2. Tenant seed/config package без секретов.
3. `NSP_SOURCE_MANIFEST_V1.tsv`.
4. Параметризованный importer и его tests.
5. NSP staging import report с counts/hash/versions.
6. Cross-tenant test report.
7. NSP content smoke suite и результаты.
8. Telegram binding configuration manifest без token/secret.
9. Production deployment report с точными фактами и rollback point.
10. `NSP_TENANT_OPERATIONS_RUNBOOK.md`: sync, suspend, resume, rotate token,
    add admin, inspect gaps, export data, rollback.
11. Обновление `WHIEDA_LIVE_STATUS.md` только после фактического deployment.

Отчёт обязан различать `prepared`, `staging_verified`, `production_deployed` и
`owner_verified`. Нельзя писать «готово», если проверен только HTTP ack или
локальный тест.

## 15. Что требуется от Виктора/Максима

До production activation нужны:

- подтверждённый display name и внутренний tenant slug;
- отдельный Telegram bot token через секретный канал;
- Telegram ids Максима и назначенных администраторов;
- ссылка на папку материалов и актуальный master source;
- указание главного прайса, страны, валюты и даты актуальности;
- подтверждение 10–20 приоритетных товаров;
- 30–50 реальных вопросов для acceptance;
- список контактов эскалации;
- решение, какие материалы разрешено показывать конечным пользователям.

Если часть данных не предоставлена, разработчик не останавливает техническую
подготовку. Он завершает tenant foundation, importer, staging seed и формирует
один конкретный gap list. Недостающие факты не угадываются.

## 16. Definition of Done

Запуск NSP считается завершённым только когда одновременно выполнено:

- используется общий Platform Core без fork кода/workflow/schema;
- NSP имеет отдельный bot binding, master binding, роли, limits и usage scope;
- production tenant resolution выполняется сервером;
- WHIEDA и NSP проходят полный cross-tenant test contract;
- реальный corpus NSP опубликован через validation/review, а не из RAW;
- цены/PV имеют market, currency, source и effective date;
- минимум 30–50 реальных NSP вопросов проходят согласованный acceptance;
- поддерживаемые structured ответы работают при остановленных n8n и Dify;
- p95 structured path ≤3 секунд в принятом staging profile;
- restart/duplicate update не порождает двойной ответ;
- secrets и PII отсутствуют в git/logs/reports;
- rollback NSP проверен и не влияет на WHIEDA;
- Максим и Виктор визуально подтвердили ответы в Telegram;
- final report перечисляет известные пробелы, а не скрывает их словом «готово».

## 17. Порядок исполнения без самостоятельного изменения scope

Разработчик выполняет этапы строго `0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8`.
После каждого gate фиксирует короткий evidence report и продолжает автономно.

Остановка и запрос решения нужны только если:

- обнаружена реальная cross-tenant утечка;
- требуется production secret/Telegram token;
- требуется destructive migration или удаление данных;
- материалы противоречат друг другу и выбор меняет пользовательский ответ;
- для продолжения необходимо изменить согласованный коммерческий scope.

Во всех остальных случаях разработчик принимает консервативное техническое
решение в рамках существующих контрактов, документирует его и продолжает.
