# WWC Owner Cabinet — P0.1: вход и read-only API

**Исполнитель:** Composer.  
**Статус:** следующее исполняемое задание после принятого P0.0.  
**Основание:**

1. `00_READ_FIRST_WHIEDA_CANON.md`;
2. `WHIEDA_PLATFORM_MASTER_PROGRAM_SPEC_V1_2026-08-09.md`;
3. `WHIEDA_FILE_MAP_CURRENT.md`;
4. `03_Website/wwc-best/docs/WWC_OWNER_CABINET_SPEC_V0_1_2026-08-09.md`;
5. `backend/platform-api/docs/WWC_OWNER_CABINET_DATA_MAP_V1.md`;
6. `backend/platform-api/docs/WWC_OWNER_CABINET_API_GAPS_V1.md`;
7. `backend/platform-api/docs/WHIEDA_DATA_COLLECTION_ANALYTICS_CATALOG_V1_2026-08-09.md`.

## 0. Результат

На local и испытательной среде существует безопасный закрытый API кабинета.

- Единственный главный администратор — Виктор.
- Вход подтверждается через Telegram, без пароля.
- Сессия браузера хранится безопасно, не в localStorage.
- Доступен read-only API заявок, ref, рынков, цен, центров, состояния синхронизации и сводки.
- Если данных ещё нет, API возвращает честный `gap`, а не ноль и не фиктивные записи.
- Никакой публичный UI, Google Sheets, n8n, delivery заявок или production не меняются.

## 1. Разрешённые изменения

- `backend/platform-api`;
- новые **добавочные** миграции PostgreSQL только для admin identity/session/audit и необходимых безопасных представлений/индексов чтения;
- тесты, staging-config и документация P0.1.

## 2. Запрещённые изменения

- production deploy, feature flag и nginx public routing;
- сайт `03_Website`, публичные страницы и формы;
- Google Sheets и любой их sync;
- n8n workflows, Telegram legacy webhook и delivery заявок;
- существующая логика owner/assigned owner/watcher;
- изменение существующих таблиц заявок/ref «для удобства»;
- write API кабинета;
- хранение Telegram bot token, access token или admin secret в браузере;
- hardcode Telegram ID, паролей, tenant или секретов в git.

## 3. Необходимая поправка к P0.0

Фраза «без новых SQL» относится к P0.0-инвентаризации. Для P0.1 она неверна.

Без добавочных таблиц невозможно проверить и отозвать кабинетную сессию, хранить allow-list, разграничить роли и вести журнал доступа. Поэтому разрешены только изолированные admin-миграции с RLS. Существующие бизнес-данные не дублировать и не переносить.

## 4. Вход через Telegram

### 4.1. Требуемый сценарий

```text
Браузер открывает кабинет
→ API выдаёт одноразовую challenge-сессию и Telegram deep link/QR
→ пользователь подтверждает вход в Telegram
→ Telegram/Core проверяет allow-list и роль
→ браузер получает short-lived HttpOnly Secure session cookie
→ browser вызывает /v1/admin/* без bearer token в JavaScript
```

### 4.2. Правила

1. Первый `super_admin` — только Виктор. Его Telegram ID задаётся серверной переменной окружения или безопасным seed вне git.
2. Новая роль не создаётся из Telegram username, ref, параметра URL или body запроса.
3. Challenge одноразовый, ограничен временем, привязан к browser nonce и не содержит открытые персональные данные.
4. Сессия хранится на сервере; в cookie только случайный идентификатор/секрет с `HttpOnly`, `Secure`, `SameSite`.
5. Logout/revoke немедленно прекращает доступ.
6. `super_admin` имеет all-tenant scope. Выбор конкретного tenant — проверяемый сервером и журналируемый фильтр, а не доверенное значение из браузера.
7. В P0.1 роли `admin` и `viewer` можно заложить схемой и тестами, но никого не назначать без отдельной команды владельца.
8. Кабинетные запросы не меняют существующие public ref/lead/Telegram контракты.

### 4.3. Минимальные защищённые сущности

Названия можно адаптировать к текущему code style, но смысл обязателен:

- `platform_admin_principals`: Telegram ID, роль, статус, допустимый scope, audit metadata;
- `platform_admin_login_challenges`: hash challenge/browser nonce, TTL, used/approved state;
- `platform_admin_sessions`: hash session secret, principal, expiry/revoked/logout state;
- `platform_admin_audit_log`: principal, действие, целевой tenant, объект, timestamp, безопасные технические детали.

Все таблицы получают корректную изоляцию/RLS. Суперадминистраторская cross-tenant выборка выполняется только через выделенный серверный путь с проверенной ролью и `admin_sensitive_view` в журнале.

## 5. Read-only API P0.1

Все endpoint требуют cabinet session. Ошибки: `{ "ok": false, "error": "...", "trace_id": "..." }`. Успех: `{ "ok": true, ... }`.

### Авторизация

| Метод | Путь | Результат |
|---|---|---|
| `POST` | `/v1/admin/auth/challenge` | browser challenge + безопасный deep link/QR |
| `POST` | `/v1/admin/auth/telegram-confirm` | внутренний защищённый callback Core/Telegram, не public browser action |
| `GET` | `/v1/admin/auth/challenge/{id}` | status/exchange только с исходным browser nonce |
| `POST` | `/v1/admin/auth/logout` | отозвать текущую сессию |
| `GET` | `/v1/admin/me` | роль, разрешённый scope, безопасный профиль |

### Данные кабинета

| Метод | Путь | P0.1 данные |
|---|---|---|
| `GET` | `/v1/admin/leads` | paginated список и server-side filters |
| `GET` | `/v1/admin/leads/{lead_id}` | заявка, статусы, attributed/assigned/watchers, delivery history |
| `GET` | `/v1/admin/referrals` | ref и публичные профили без секретов |
| `GET` | `/v1/admin/markets` | рынки/snapshot либо `gap` |
| `GET` | `/v1/admin/prices` | цены/sостояние либо `gap` |
| `GET` | `/v1/admin/service-centers` | новые центры отдельно от legacy `service_locations` |
| `GET` | `/v1/admin/sync-status` | markets sync; partners/structured sync как `gap`, пока Core их не видит |
| `GET` | `/v1/admin/overview` | композиция доказанно существующих показателей |

### Общие правила DTO

- контакты маскируются в list endpoint; полный контакт только в detail при разрешённой роли;
- `metadata` заявки не выдаётся dump-ом: только allowlist нужных полей;
- `partner_tier`, `subscription_cost`, `last_activity_at`, несуществующие sync-данные возвращаются как `null` + `meta.field_status = "gap"`;
- `wwc_service_centers` и legacy `service_locations` никогда не смешиваются под общим словом «центр»;
- пагинация server-side, limit с верхней границей;
- tenant/scope/role всегда переопределяются сервером.

## 6. События и аналитика

В этой задаче не включать production telemetry и не строить кабинеты аналитики.

Нужно только:

1. оформить машинно-читаемый registry текущих event types и их статусов (`implemented`, `staging`, `production`, `gap`);
2. показать в `overview` только реально доступные агрегаты заявок/ref;
3. не создавать новый `website_events` writer;
4. подготовить точный следующий P0.1A-подэтап для staging: `visitor_session_id` в lead и read-only `interaction_events` timeline.

## 7. Проверки

Обязательны pytest и staging/local smoke:

1. неизвестный Telegram ID не получает кабинетную сессию;
2. Виктор получает `super_admin`;
3. challenge истёк/использован повторно/подменён — доступ запрещён;
4. browser не получает bearer token/Telegram secret;
5. logout и revoke закрывают следующую попытку API;
6. unauthenticated — 401; неподходящая роль — 403;
7. super_admin all-tenant view строго журналируется;
8. tenant admin/viewer не может прочитать чужой tenant;
9. list API не раскрывает контакты/секреты; detail выдаёт только разрешённый набор;
10. lead list/detail читают существующие production-compatible таблицы без изменения routing;
11. пустые рынки/центры/sync дают честный `gap` и 200, не 500;
12. полная существующая test-suite остаётся зелёной.

## 8. Последовательность

1. Прочитать перечисленные документы и существующие миграции/auth building blocks.
2. Сформировать короткий design note: как Telegram подтверждает browser challenge и где проверяется allow-list.
3. Добавить миграции + RLS + тестовые seeds только для admin-сущностей.
4. Реализовать auth endpoints и tests.
5. Реализовать read DTO/repositories в порядке: leads → referrals → sync-status → markets/prices/centers → overview.
6. Добавить event registry/status document, не включая writers.
7. Прогнать полный pytest и staging/local smoke.
8. Написать отчёт строго в четырёх пунктах из `AGENTS.md`.

## 9. Приёмка и ограничения

Результат не считается опубликованным, пока не показаны фактические local/staging requests:

- login challenge → Telegram confirm → `GET /v1/admin/me`;
- lead list/detail;
- ref list;
- markets empty/gap state;
- unauthorized/forbidden cases;
- logout/revoke;
- all-tenant audit record.

Production deploy, `admin.wwc.best` и UI — отдельная P0.2-задача после freeze JSON-контракта и приёмки владельцем.
