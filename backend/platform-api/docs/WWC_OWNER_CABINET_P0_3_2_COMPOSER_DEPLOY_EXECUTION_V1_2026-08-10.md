# WWC Owner Cabinet — P0.3.2: staging deploy и живая приёмка

**Исполнитель:** Composer.  
**Архитектор/приёмка:** Codex + Виктор.  
**Цель:** довести существующий P0.3 staging-контур до живого входа нового Telegram-бота. Production не трогать.

## Source of truth

Перед работой прочитать полностью:

1. `00_READ_FIRST_WHIEDA_CANON.md`;
2. `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md`;
3. `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_1_SINGLE_HOST_CORRECTION_V1_2026-08-09.md`;
4. `03_Website/wwc-best/AGENTS.md`.

Если документы расходятся — действует P0.3.1.

## Целевая схема — не менять

```text
admin-staging.wwc.best → 173.249.45.83 (site VPS)
  /cabinet/             → static cabinet
  /api/*                → same-origin nginx proxy
                           → 185.252.232.93:8081 (staging Core API)

Telegram staging bot → https://admin-staging.wwc.best/api/v1/telegram/wwc-cabinet-staging-bot/webhook
```

Не использовать: `api-staging.wwc.best`, `cabinet-staging.wwc.best`, production `wwc.best`, рабочий Telegram-бот, production БД, n8n workflow.

## Предусловия — проверить, не выдумывать

1. DNS `admin-staging.wwc.best` резолвится на `173.249.45.83`.
2. На Core VPS существует файл с правами `600`:

```text
/opt/whieda-platform-staging/secrets/cabinet-staging.env
```

Он содержит все ключи:

```text
PLATFORM_TELEGRAM_BOT_TOKEN
PLATFORM_TELEGRAM_BOT_USERNAME=wwc_admin_staging_bot
PLATFORM_TELEGRAM_WEBHOOK_SECRET
PLATFORM_ADMIN_SUPER_TELEGRAM_IDS
PLATFORM_ADMIN_CONFIRM_SECRET
```

Не читать значения в отчёт, stdout, git или browser. Если файл/ключи отсутствуют — остановиться с `missing_server_secret`, не просить токен в чате.

3. Получен TLS-сертификат **для `admin-staging.wwc.best`**. Сертификат `wwc.best` не использовать, если он не wildcard.
4. Есть проверенный staging bot binding `wwc-cabinet-staging-bot` tenant `whieda`, не связанный с рабочим ботом.

## Статус попытки 2026-08-10

**Apply не выполнен:** `missing_server_secret` — на Core VPS нет заполненного `cabinet-staging.env`.  
Перед повтором: `precheck_staging_cabinet_p0_3_2_2026-08-10.py` → bootstrap → fill secrets on server → `run_staging_cabinet_p0_3_2_2026-08-10.py --apply`.

## Выполнение

1. Снять dry-run и backup существующих staging конфигов. Подтвердить, что production vhost, production database и production Telegram webhook не затрагиваются.
2. Применить нужные SQL migration/binding только к staging окружению.
3. Развернуть Platform API на Core VPS `:8081` с server-only secrets.
4. Ограничить firewall Core VPS: TCP `8081` принимает только localhost и `173.249.45.83`. До изменения проверить текущие правила и не закрыть работающие production сервисы.
5. Развернуть static build с staging runtime config (`enabled: true`, same-origin `/api/v1/admin`) на site VPS.
6. Установить/проверить Nginx vhost `admin-staging.wwc.best`:
   - один server_name;
   - HTTPS;
   - `/api/` превращает `/api/v1/*` в Core `/v1/*`;
   - сохраняет cookies и `X-Telegram-Bot-Api-Secret-Token`;
   - arbitrary `/cabinet/leads/{uuid}/` отдаёт static shell;
   - `X-Robots-Tag: noindex, nofollow, noarchive`;
   - без Метрики и advisor widget в HTML кабинета.
7. До webhook запустить HTTP smoke. Обязательный результат:

```text
/cabinet/                 → 200
/wwc-cabinet-config.json  → enabled=true
/api/v1/admin/me          → 401 без cookie, не 404
```

8. Только после успешного smoke назначить webhook **новому** `@wwc_admin_staging_bot`; подтвердить `getWebhookInfo` без показа token.
9. Не объявлять живой вход готовым до ручного QR-прохождения Виктора.

## Rollback

При ошибке nginx/API/webhook:

- вернуть только staging vhost/static/API к backup;
- снять webhook только у staging-бота, если необходимо;
- не переключать и не перезаписывать webhook рабочего бота;
- не применять широкий firewall deny, если не подтверждены текущие правила.

## Обязательные доказательства

1. Точные staging URL и HTTP statuses;
2. `nginx -t` и успешный reload;
3. API pytest и website build/unit tests;
4. Smoke 401 на `/api/v1/admin/me`;
5. Проверка webhook URL нового бота;
6. Скриншоты 1440/390 login shell — без Метрики/advisor;
7. После ручного действия Виктора: QR → bot confirmation → authenticated browser → overview → logout 401.

## Отчёт

Ровно четыре пункта из `AGENTS.md`:

1. Что изменено.
2. Где видно: URL.
3. Что проверено фактически.
4. Что не проверено/ограничения.

Запрещены формулировки «живой вход готов» без реального QR-прохождения Виктора. Нельзя выкладывать `admin.wwc.best` production в этой задаче.
