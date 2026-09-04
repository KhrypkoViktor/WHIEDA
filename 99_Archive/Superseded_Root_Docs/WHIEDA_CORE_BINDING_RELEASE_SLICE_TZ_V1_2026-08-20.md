# WHIEDA / NSP: Core Binding Release Slice V1

Дата: 2026-08-20  
Владелец: Core lead  
Исполнитель: WHIEDA Core developer  
Статус: выполнить локально, без deploy/staging/webhook.

## Цель

Выделить из грязного `feat/platform-scale-core` один переносимый коммит,
который даёт multi-tenant Telegram binding: вход определяется binding-ом,
ответ уходит токеном того же binding-а, NSP не может попасть в WHIEDA.

Это снимает блокер NSP. Коммит должен накладываться на `69db0b5` без корзины,
сайта, nginx, рынков, кабинета и n8n.

## Не включать

- `app/cart/**`, `app/markets/**`, `app/admin/**`, `app/leads/**`;
- `03_Website/**`, `n8n/**`, JS сайта, nginx, health extras;
- newcomer panel, `?cart=`, `calculator_web_url`, создание cart session;
- рынки, pricing, отзывы, product cards;
- любой deploy, SQL apply, secret, setWebhook.

## Разрешённая функциональность

1. Новый `BotBindingContext`: binding_id, tenant, refs token/secret, username,
   status, mode; значения secret только из `env:NAME`, не из БД.
2. До ACK: unknown/disabled → 200 без работы; active/bad-secret → 403;
   store/missing-secret/misconfigured → 503; active/valid → 200 + background
   process.
3. Выходящие text/photo/callback/inline и group mention используют current binding.
4. Namespace sequencer/dedupe по `binding_id`.
5. NSP и любой non-WHIEDA допускает только `core`; не получает legacy/shadow,
   WHIEDA admin challenge, брендинг или `wwc.best`.
6. В logs/repr/HTTP нет raw chat id, token, secret и Telegram URL с token.

## Разрешённые файлы

Можно создать/изменить только это, плюс точно нужные тесты:

```text
backend/platform-api/app/telegram/bindings.py
backend/platform-api/app/telegram/log_safe.py
backend/platform-api/app/telegram/routes.py
backend/platform-api/app/telegram/processor.py
backend/platform-api/app/telegram/delivery.py
backend/platform-api/app/telegram/sequencer.py
backend/platform-api/app/telegram/update_parser.py
backend/platform-api/app/telegram/catalog_browse.py
backend/platform-api/app/telegram/navigation.py
backend/platform-api/app/telegram/admin_login.py
postgres/sql/platform_bot_binding_context_v1.sql
backend/platform-api/tests/test_telegram_binding_ingress.py
backend/platform-api/tests/test_telegram_log_safe.py
backend/platform-api/tests/test_telegram_processor.py
backend/platform-api/tests/test_telegram_chat_sequencer.py
backend/platform-api/tests/test_telegram_delivery.py
backend/platform-api/tests/test_telegram_route_truth_table.py
backend/platform-api/tests/test_telegram_navigation.py
backend/platform-api/tests/test_staging_sql_order.py
backend/platform-api/tests/test_local_staging_proof.py
```

`catalog_browse.py` и `navigation.py` брать только в части tenant-scoped
delivery и скрытия WHIEDA calculator. Не тянуть cart imports и newcomer flow.
Если для корректного import нужен иной файл, остановиться и записать его в
`D:\Projects\_peer-sync\nsp-whieda\from-core\NEEDS_LEAD_DECISION.md`.

## Приёмка

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests/test_telegram_binding_ingress.py tests/test_telegram_log_safe.py tests/test_telegram_processor.py tests/test_telegram_chat_sequencer.py tests/test_telegram_delivery.py tests/test_telegram_route_truth_table.py tests/test_telegram_navigation.py tests/test_staging_sql_order.py tests/test_local_staging_proof.py -q
```

Нужно `95 passed` или больше, без нового unrelated fail.

В конце:

1. отдельная ветка `core-binding-release` от `69db0b5`;
2. один focused commit;
3. `CORE_BINDING_RELEASE_MANIFEST_V1.json`: commit, base, file list,
   SHA-256 migration, тестовая команда и результат;
4. copy manifest в `D:\Projects\_peer-sync\nsp-whieda\from-core\`;
5. не merge/rebase NSP и не deploy.

После review лида commit становится единственным допустимым Core dependency
для `nsp-tenant-launch`. Shared staging — отдельный Gate B.
