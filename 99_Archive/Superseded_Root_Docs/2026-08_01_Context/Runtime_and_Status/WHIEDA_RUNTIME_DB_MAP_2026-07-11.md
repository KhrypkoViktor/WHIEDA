# WHIEDA runtime DB map

Дата: 2026-07-11

## Что подтверждено точно

### 1. Серверный контур

Основной WHIEDA backend живет на:
- `185.252.232.93`

В каталоге:
- `~/n8n`

Там подняты только два compose-сервиса:
- `n8n`
- `postgres`

По `docker compose`:
- `n8n` слушает `5678`
- `postgres` локальный контейнер `postgres:16-alpine`

### 2. Локальная служебная база n8n

В `docker-compose.yml` подтверждено:
- `DB_TYPE=postgresdb`
- `DB_POSTGRESDB_HOST=postgres`
- `DB_POSTGRESDB_PORT=5432`
- `DB_POSTGRESDB_DATABASE=n8n`
- `DB_POSTGRESDB_USER=n8n`

Вывод:
- сама платформа `n8n` хранит свои workflow/executions в локальном контейнере `postgres`
- это служебная БД `n8n`

## Что есть в локальной postgres-базе `n8n`

Read-only проверка таблиц показала:
- `advisor_conversation_context`
- `advisor_conversations`
- `advisor_events`
- `advisor_review_queue`
- `advisor_structured_aliases`
- `advisor_structured_products`
- `advisor_structured_resources`
- `advisor_structured_sync_log`
- `advisor_surface_accounts`
- `advisor_users`
- `content_jobs`
- `content_seeds`
- `publish_attempts`
- `publish_targets`

### Текущее наполнение локальной БД

- `advisor_structured_products` = `37`
- `advisor_structured_aliases` = `14`
- `advisor_structured_resources` = `31`
- `advisor_events` = `0`
- `advisor_review_queue` = `0`
- `advisor_users` = `0`
- `advisor_conversations` = `0`

## Что это значит

Локальная postgres-база рядом с `n8n` точно содержит:
- structured слой
- часть legacy/content таблиц

Но runtime-следов live-потока в ней сейчас не видно:
- нет пользователей
- нет conversation runtime
- нет events
- нет review queue rows

## Сильнейшая текущая гипотеза

Самая вероятная картина сейчас такая:

1. `n8n` как платформа живет на локальном `postgres`
2. в локальную БД уже когда-то загоняли structured/legacy таблицы
3. credential `advisor-dev-postgres`, который использует WHIEDA workflow, скорее всего смотрит не в эту локальную БД `n8n`, а в отдельную runtime Postgres-точку

Почему это похоже на правду:
- live executions успешны
- workflow читает structured данные и пишет review loop
- но в локальной `n8n` БД нет новых runtime строк
- probe локальной `pg_stat_activity` во время live execution показал только служебные запросы самого `n8n`, а не `advisor_*` insert/select от workflow

## Что пока НЕ доказано на 100%

Не доказан точный host/database для credential:
- `advisor-dev-postgres` (`RmjHh3rdZri7axzq`)

Мы сознательно НЕ вытаскивали расшифрованный secret/connection string, потому что это уже credential extraction.

## Безопасный следующий шаг

Чтобы закрыть вопрос до конца, нужен один из двух путей:

### Вариант A — лучший

Дать отдельный runtime DB access map:
- host
- port
- db name
- user
- где это prod runtime для WHIEDA

### Вариант B — точечный

Отдельно разрешить read-only просмотр только одного credential target:
- `advisor-dev-postgres`

Не всех credentials, а только его.

## Практический вывод для разработки

Прямо сейчас можно опираться на это:

- live workflow в `n8n` стабилизирован
- review loop живой
- серверная топология понятна
- локальная `postgres` рядом с `n8n` НЕ выглядит как подтвержденный единственный runtime source of truth

То есть следующий архитектурный шаг:
- не мигрировать вслепую
- сначала зафиксировать точную runtime DB endpoint для `advisor-dev-postgres`
- потом уже чисто вести миграцию queue/schema/structured sync
