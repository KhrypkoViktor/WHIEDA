# WWC Markets & Service Centers API — Backend ТЗ V1

**Исполнитель:** Composer.  
**Заказчик интерфейса:** `03_Website/wwc-best`.  
**Цель:** дать сайту реальные цены, рынки и сервисные центры без смешения структур и без изменения существующих заявок, Telegram или n8n.

## 0. Граница задачи

Разрешено изменять только:

- `backend/platform-api`;
- новые миграции в `postgres` для этой функции;
- конфигурацию reverse proxy, только чтобы открыть перечисленные ниже read-only endpoints сайту `wwc.best`;
- документацию и тесты этой функции.

Запрещено изменять:

- `POST /api/lead`, `POST /api/v1/leads`, логику owner/исполнителя/watcher;
- n8n workflows;
- Telegram routes и webhook;
- существующие API advisor/ref/journey;
- frontend, product content, цены в статике и UI-kit.

Один релиз. До явного включения владельцем frontend-флаг `market_centers_v1` остаётся `false`.

## 1. Продуктовые правила

1. Рынки V1: `ru`, `by`, `global`.
2. Россия показывает только RUB; Беларусь — только BYN.
3. `global` показывает исходную RUB-цену. Это не конвертация. Консультация доступна, но цену не заменяет.
4. Сервисные центры принадлежат **структуре**, не конкретному партнёру и не всему WWC.
5. Структура определяется сервером по активному `ref`. Клиент никогда не передаёт `structure_id` как право выбрать чужой реестр.
6. `ref` считается валидным только если одновременно активны запись `ref_structures` и существующий публичный referral profile. Если `ref` неизвестен, выключен или отсутствует — использовать `wwc-default`.
7. `first_ref` служит только атрибуции; для списка центров приоритет имеет активный валидный `ref`. Если активного нет — можно использовать валидный `first_ref`, иначе `wwc-default`.
8. Город определяется только введённым/выбранным пользователем. IP-геолокации в этой задаче нет.
9. Город вне реестра не получает выдуманный центр. Сервер возвращает честный fallback. Региональный центр допустим только при явной записи покрытия в источнике данных.
10. `center_id` в будущей заявке — контекст обслуживания. Он не меняет атрибуцию, owner или получателя уведомления.

## 2. Источник данных и хранение

### 2.1. Google Sheets — редактор, PostgreSQL — runtime

Менеджер редактирует данные в Google Sheets. Сайт никогда не читает Sheets напрямую.

Backend синхронизирует таблицу в PostgreSQL и отвечает из последнего успешного снимка. Если Google временно недоступен, сайт продолжает работать на последнем валидном снимке. Не делать публичный Google Sheet и не использовать браузерные ключи.

Доступ к Google Sheets — через service account; credential и ID таблицы только в server env, не в git и не в API-ответах.

Все новые runtime и staging-таблицы обязаны включать существующую tenant RLS-политику через `platform_current_tenant_id()` с `USING` и `WITH CHECK`. Фильтр `tenant_id` в SQL приложения полезен, но не заменяет RLS.

### 2.2. Минимальные вкладки Sheets

| Вкладка | Ключ | Смысл |
|---|---|---|
| `markets` | `market_id` | рынки и правила цены |
| `ref_structures` | `ref_code` | ref → structure |
| `service_centers` | `center_id` | публичные центры |
| `service_center_coverage` | `structure_id + country_iso + city_alias` | явное покрытие города/райцентра |
| `product_prices` | `sku + market_id` | одна активная цена SKU для рынка |

Пока не будет реальной таблицы, создать **только локальные fixtures/тестовые миграции**. Не подставлять тестовые города и цены в production.

### 2.3. Обязательные колонки

`markets`:

```text
market_id, country_iso, country_name, currency_code, price_visibility, is_active, is_default
```

Начальные строки:

```text
ru, RU, Россия, RUB, full, true, false
by, BY, Беларусь, BYN, full, true, false
global, *, Другая страна, RUB, full, true, true
```

`ref_structures`:

```text
ref_code, structure_id, is_active
```

`service_centers`:

```text
center_id, structure_id, country_iso, city, region, title, manager_name,
photo_url, telegram, phone, address, working_hours, map_url_yandex,
map_url_google, notes, is_active, priority
```

`service_center_coverage`:

```text
structure_id, country_iso, city_alias, center_id, is_active, priority
```

`product_prices`:

```text
sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at
```

Валидация синхронизации:

- `market_id` только `ru`, `by`, `global`;
- RUB допустим для `ru`/`global`, BYN — только для `by`;
- активный `sku + market_id` уникален;
- `amount` неотрицательный integer/decimal; `formatted` формирует backend, не таблица;
- URL фото/карт только `https`/`http`; Telegram только handle; телефон нормализовать сервером;
- центр должен иметь `structure_id`, страну, город, название, менеджера, адрес и `is_active`;
- ошибка одной строки не должна публиковать частичный новый снимок: сохранить предыдущий и записать понятный sync-error.

## 3. API

Все ответы JSON имеют поле `ok`. Успех — только `{"ok": true}`. Ошибка — `{"ok": false, "error": "...", "message": "...", "trace_id": "..."}`.

Маршруты Platform API:

| Метод | Core path | Публичный alias для сайта |
|---|---|---|
| GET | `/v1/site-context` | `/api/site-context` |
| GET | `/v1/catalog-prices` | `/api/catalog-prices` |
| GET | `/v1/service-centers` | `/api/service-centers` |
| GET | `/v1/service-center-cities` | `/api/service-center-cities` |

Alias должен проксироваться на Platform API только для `wwc.best` и не конфликтовать с существующим `/api/lead`.

### 3.1. `GET /api/site-context`

Query: `ref`, `first_ref`, `market_id`, `country_hint`, `city`, `page`, `sku`.

`market_id` допустим только `ru`, `by`, `global`; иначе сервер возвращает `global` или текущий market по правилам, без 500.

Ответ:

```json
{
  "ok": true,
  "ref": "harold",
  "first_ref": "harold",
  "personalization_active": true,
  "partner": { "id": "partner-123", "name": "Harold", "contact": "https://t.me/Haroldsvetoch" },
  "structure": { "id": "leader-x", "name": "Structure X" },
  "market": { "id": "by", "country_iso": "BY", "currency_code": "BYN", "price_visibility": "full" },
  "available_markets": ["ru", "by", "global"],
  "country_hint": null,
  "city_match": null,
  "service_centers": []
}
```

Не возвращать owner_id, watchers, внутренние tenant IDs, приватные заметки или данные других структур.

### 3.2. `GET /api/catalog-prices`

Query: `market_id` (required), `sku` (CSV, optional).

Если `market_id=global`, брать активные RUB-цены рынка `ru` как исходные цены, если отдельной активной global-строки нет. Никакой конвертации.

Ответ:

```json
{
  "ok": true,
  "market_id": "by",
  "currency_code": "BYN",
  "price_visibility": "full",
  "prices": [
    {
      "sku": "M015-00",
      "amount": 1750,
      "currency_code": "BYN",
      "formatted": "1 750 BYN",
      "price_state": "active",
      "is_active": true,
      "updated_at": "2026-08-09T00:00:00Z"
    }
  ]
}
```

Если конкретный SKU отсутствует, вернуть для него `price_state: "unavailable"`, `amount: null`, `formatted: null`, но сам endpoint остаётся `ok: true`.

### 3.3. `GET /api/service-center-cities`

Query: `ref`, `first_ref`, `country_iso`.

Сервер резолвит структуру. Возвращает только города с активными центрами этой структуры и страны.

```json
{
  "ok": true,
  "structure_id": "leader-x",
  "country_iso": "BY",
  "cities": [{ "city": "Минск", "region": "Минск / Минская область" }]
}
```

Для `country_iso=*` возвращать пустой массив, если центров в other-country нет.

### 3.4. `GET /api/service-centers`

Query: `ref`, `first_ref`, `country_iso`, `city`.

Алгоритм:

1. resolve structure server-side;
2. нормализовать город (trim, casefold, aliases);
3. exact city → активный центр с наибольшим `priority`;
4. explicit record in `service_center_coverage` → назначенный центр;
5. иначе пустые `centers` и `fallback.type = "no_registry_match"`.

```json
{
  "ok": true,
  "structure_id": "leader-x",
  "country_iso": "BY",
  "city_query": "Минск",
  "city_match": { "type": "exact", "city": "Минск", "center_id": "minsk-01" },
  "centers": [
    {
      "center_id": "minsk-01",
      "structure_id": "leader-x",
      "country_iso": "BY",
      "city": "Минск",
      "region": "Минск / Минская область",
      "title": "Сервисный центр WWC — Минск",
      "manager_name": "Имя Фамилия",
      "photo_url": "https://...",
      "telegram": "username",
      "phone": "+375...",
      "address": "...",
      "working_hours": "...",
      "map_url_yandex": "https://yandex.ru/maps/...",
      "map_url_google": "https://maps.google.com/...",
      "notes": "...",
      "is_active": true,
      "priority": 100
    }
  ],
  "fallback": null
}
```

При отсутствии совпадения:

```json
{
  "ok": true,
  "structure_id": "leader-x",
  "country_iso": "RU",
  "city_query": "Новосибирск",
  "city_match": null,
  "centers": [],
  "fallback": {
    "type": "no_registry_match",
    "message": "В вашем городе сервисный центр пока не указан. Подскажем ближайший районный центр и формат консультации."
  }
}
```

## 4. Синхронизация

1. Реализовать service/repository для pull из Google Sheets в staging tables и атомарной публикации нового снимка.
2. Добавить защищённую команду/CLI для ручного запуска sync в staging и production; HTTP admin endpoint в V1 не нужен.
3. Планировщик: каждые 10 минут, но только после первой успешной ручной загрузки.
4. Сохранять `source_updated_at`, `synced_at`, статус и текст последней ошибки в техническом registry.
5. При неуспешном sync предыдущий snapshot остаётся активен.
6. Не хранить service-account JSON в репозитории или в PostgreSQL.

## 5. Leads: только расширить принимаемые поля

Не менять route/delivery. `parse_lead_body` может принять и сохранить в metadata (если схема позволяет):

```text
market_id, country_iso, city_selected, center_id
```

Backend сам повторно валидирует `center_id` против resolved structure/ref или сохраняет пустым. Нельзя доверять `structure_id`, owner или executor из body.

`country_code` для существующей маршрутизации берётся как `body.country_code` **или**, если он не задан, `body.country_iso`. Для `BY` сохранять и использовать `BY`; нельзя молча заменить Беларусь на `RU`. Значение `*`/иная страна в V1 может нормализоваться в `RU` только после явной проверки правила, но исходный `country_iso` сохраняется в metadata.

Если миграция metadata сейчас рискованна — не блокировать read-only API; оформить отдельный точный gap, не менять n8n.

## 6. Тесты и приёмка

Нужны pytest, без зависимости от настоящего Google в unit-тестах.

Обязательно проверить:

1. `ref=alpha` и `ref=beta` в Минске получают разные центры.
2. Нельзя получить center alpha через `ref=beta`, подставив query/structure.
3. неизвестный ref → `wwc-default`.
4. RU даёт только RUB, BY только BYN, global даёт RUB.
5. `global` не возвращает consultation-only цену.
6. отсутствующий SKU даёт `unavailable`, но `ok: true`.
7. Новосибирск вне реестра не получает случайный центр.
8. explicit coverage возвращает только заранее назначенный центр.
9. невалидные city/ref/query не вызывают 500.
10. ошибка Google sync не заменяет предыдущий активный snapshot.
11. ответы не содержат owner, watcher, tenant/private notes.
12. заявка с `country_iso=BY` не превращается в `RU` в `country_code` и не меняет существующее правило ref-маршрутизации неявно.
13. отключённый/несуществующий public ref не получает структуру только по записи `ref_structures`.
14. новые таблицы реально защищены tenant RLS: запрос с tenant A не читает/не пишет данные tenant B.
15. scheduler перебирает активные tenant с включённой синхронизацией, а не содержит захардкоженный tenant `whieda`.
16. существующие tests Platform API, leads, Telegram и advisor не регрессируют.

## 7. Отчёт исполнителя

Только четыре пункта:

1. Что изменено.
2. Где доступно: staging URL и exact endpoints.
3. Что проверено фактически: pytest, curl-примеры всех рынков и двух структур.
4. Что не проверено / известные ограничения.

Не писать «готово», пока staging endpoints не отдают реальные либо явно маркированные staging данные и не пройдены пункты 1–12.
