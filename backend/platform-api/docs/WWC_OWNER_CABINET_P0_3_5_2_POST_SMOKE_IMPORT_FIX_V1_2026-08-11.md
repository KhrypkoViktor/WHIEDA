# WWC Owner Admin — P0.3.5.2: рабочий post-deploy smoke

## Факт проверки

P0.3.5.1 precheck прошёл на текущем staging полностью: DNS, TLS, Core/Site SSH и secrets — зелёные.

Публичная часть post-deploy smoke также зелёная:

- `/cabinet/` → 200;
- `/wwc-cabinet-config.json` → 200 и `enabled: true`;
- `/api/v1/admin/me` → 401.

Но три внутренних проверки сейчас падают с `ModuleNotFoundError`:

- `core_local_8081_health`;
- `core_tunnel_unit`;
- `site_local_18081_health`.

Причина: `post_deploy_staging_cabinet_smoke_2026-08-10.py` лежит в `backend/platform-api/scripts/`, но импортирует `whieda_runtime_env` и `staging_deploy_lib` из `n8n/current/`, не добавляя этот путь к импорту.

## Scope

Только:

- `backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py`;
- тесты P0.3.5;
- краткий completion report/runbook при необходимости.

Не выполнять `--apply`, не менять серверы, туннель, nginx, SQL, Telegram или production.

## Исправление

Сделать импорт `n8n/current` явным и локальным для smoke-скрипта. Разрешены два варианта:

1. в начале скрипта вычислить project root и добавить `ROOT / "n8n" / "current"` в `sys.path` **до** импортов; или
2. вынести импорты в существующий проектный модуль с корректным package path.

Не использовать текущую рабочую директорию и не требовать ручной `PYTHONPATH` от оператора.

## Обязательные проверки

1. Unit test запускает сам smoke-скрипт из его фактического расположения через subprocess/import path и подтверждает отсутствие `ModuleNotFoundError`.
2. Mock SSH возвращает:
   - Core `127.0.0.1:8081/health/live` → 200;
   - `wwc-admin-staging-tunnel.service` → active;
   - Site `127.0.0.1:18081/health/live` → 200.
   Итог `passed: true`.
3. Public 401 остаётся обязательным; 404 по-прежнему ошибка.
4. Запустить полный набор P0.3.5 tests.
5. Не называть задачу завершённой без реального локального запуска smoke-скрипта.

## Приёмка после правки

В отчёте показать только:

1. что изменено;
2. где видно;
3. что проверено фактически;
4. ограничения.

После его отчёта я самостоятельно повторю post-deploy smoke на живом staging: без deploy, только read-only проверка.
