# WWC Owner Cabinet — P0.3: staging и приёмка живого контура

**Статус:** задача для Composer/ops.  
**P0.3.1 (обязательно):** один host — см. `WWC_OWNER_CABINET_P0_3_1_SINGLE_HOST_CORRECTION_V1_2026-08-09.md`. Отдельный `api-staging.wwc.best` **не используется**.

**Цель:** развернуть закрытый staging-кабинет и один раз доказать живую цепочку: браузер → QR → отдельный staging Telegram-бот → staging webhook → cookie → кабинет.

## 1. Строгая граница

Работать только в staging. Production `wwc.best`, рабочий Telegram-бот, n8n, текущие заявки и публичные ref не менять.

Целевые адреса:

```text
admin-staging.wwc.best        — UI кабинета + same-origin /api/ proxy
```

Browser и Telegram webhook используют только:

```text
https://admin-staging.wwc.best/cabinet/
https://admin-staging.wwc.best/api/v1/admin/*
https://admin-staging.wwc.best/api/v1/telegram/{staging_binding_id}/webhook
```

Не использовать `wwc.best/admin`, не публиковать ссылку в публичном header/footer, не включать кабинет на production.

## 2. Отдельный Telegram-бот

Владелец создаёт **нового** бота в BotFather, например `WWC Cabinet Staging`. Его token не передаётся в чат и не попадает в git, frontend или отчёт.

На staging server token, username и прочие секреты записываются только в защищённый env/secrets-файл. Нужны минимум:

```text
TELEGRAM_BOT_TOKEN=<staging bot token>
PLATFORM_TELEGRAM_BOT_USERNAME=<staging_bot_username без @>
PLATFORM_ADMIN_SUPER_TELEGRAM_IDS=<Telegram ID Виктора>
PLATFORM_ADMIN_CONFIRM_SECRET=<random server-only secret>
TELEGRAM_WEBHOOK_SECRET=<random server-only secret>
CORE_ROUTE_TELEGRAM=legacy
```

Значения выше — примеры имён; сверить точные имена c `app/settings.py`. Не логировать их и не коммитить.

Вебхук назначается **только новому staging-боту** на:

```text
POST https://admin-staging.wwc.best/api/v1/telegram/{staging_binding_id}/webhook
```

либо эквивалентный закрытый proxy path. Он обязан иметь HTTPS и Telegram webhook secret. Рабочий бот и его webhook не трогать.

## 3. Развёртывание

1. Поднять Platform API против отдельной staging БД/схемы с применёнными миграциями P0.1 и P0.1B.
2. Создать/проверить staging bot binding для tenant `whieda`; binding принадлежит только staging bot.
3. Разместить сборку кабинета на `admin-staging.wwc.best`.
4. В staging runtime config включить:

```json
{
  "enabled": true,
  "apiBasePath": "/api/v1/admin",
  "targetHost": "admin-staging.wwc.best"
}
```

5. Настроить Nginx:
   - `/api/` → staging Platform API с корректным преобразованием `/api/v1/*` → `/v1/*`;
   - неизвестный `/cabinet/leads/{uuid}/` → static shell `cabinet/leads/_/index.html`;
   - HTTPS; cookie не теряется на same-origin запросах;
   - `X-Robots-Tag: noindex, nofollow, noarchive`;
   - не проксировать публичные API и не добавлять CORS `*`.
6. Убедиться, что в HTML кабинета нет Метрики и advisor widget.

## 4. Живой сценарий приёмки

Виктор выполняет один реальный вход:

1. Открывает `https://admin-staging.wwc.best/cabinet/`.
2. Нажимает «Войти через Telegram».
3. Сканирует QR/открывает ссылку именно в staging-боте.
4. Бот отвечает «Вход в кабинет подтверждён. Вернитесь в браузер.».
5. Страница получает `authenticated`, cookie устанавливается сервером, открывается «Обзор».
6. Открыть «Заявки», применить status filter, открыть детальную карточку.
7. Открыть «Партнёры и ref», скопировать ref. Проверить, что он равен `https://wwc.best/?ref=<code>`, а не `/r/<code>`.
8. Проверить logout → `/me` даёт 401 → данные не видны.

Снять скриншоты 1440px и 390px: login, overview, list leads, lead detail. На скриншотах не должно быть исходных контактов, Метрики или advisor widget.

## 5. Проверки

- `python -m pytest tests/ -q` в Platform API;
- `npm run build` и `npm run test:unit` в `03_Website/wwc-best`;
- curl/HTTP proof без auth → 401, после logout → 401;
- проверить `Set-Cookie`: `HttpOnly`, `Secure`, `SameSite`;
- неизвестный ref не раскрывает данные другой структуры;
- production host не менялся.

## 6. Стоп-условия

Остановиться и сообщить, не импровизируя, если:

- приходится менять webhook рабочего бота;
- staging использует production database;
- нет HTTPS, cookie не ставится или API доступен cross-origin;
- неясно, какой bot binding/tenant используется;
- для продолжения требуется секрет в git, браузере или сообщении.

## 7. Формат отчёта

Ровно четыре пункта:

1. Что изменено.
2. Где видно: точные staging URLs.
3. Что проверено фактически: каждый шаг живого сценария.
4. Что не проверено / ограничения.

Только после успешного сценария разрешено сказать: «живой Telegram-вход на staging проверен». Это не является разрешением выкладывать production кабинет.
