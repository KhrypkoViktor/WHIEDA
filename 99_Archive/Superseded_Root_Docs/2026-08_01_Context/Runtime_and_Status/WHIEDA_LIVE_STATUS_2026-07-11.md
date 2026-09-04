# WHIEDA — live status

Дата: 2026-07-12

## Read-only snapshot 2026-07-12

- Live workflow подтвержден read-only:
  - `advisor-whieda-phase1`
  - `active = true`
  - `versionId = 8089af25-7b29-46f9-9e9b-bfcd25bcfe0e`
  - `activeVersionId = 8089af25-7b29-46f9-9e9b-bfcd25bcfe0e`
- Live `advisor_review_queue` подтверждена read-only:
  - первичный ключ уже нормальный: `(client_id, source_type, source_ref)`
  - legacy `status` уже допускает весь рабочий цикл:
    - `candidate`
    - `pending`
    - `triage`
    - `in_work`
    - `applied`
    - `verified`
    - `closed`
    - `duplicate`
    - `rejected`
- Вывод:
  - прошлый safe-режим через один `source_payload.queue_status` больше не является единственным вариантом;
  - можно переходить к явным operational columns и синхронно поддерживать `status`.

## n8n

- Вход в `n8n` подтвержден.
- Live workflow найден: `advisor-whieda-phase1`.
- Workflow активен.
- Найден и исправлен рассинхрон: draft-версия была новее, чем active/published.
- Текущая active version: `8089af25-7b29-46f9-9e9b-bfcd25bcfe0e`.
- Trusted review patch опубликован в live.
- Дополнительно накатан compatibility patch для старой таблицы `advisor_review_queue`:
  - trusted/candidate логика сохраняется в `source_payload`
  - в legacy-колонку `status` для `candidate` временно пишется совместимое значение, чтобы runtime не падал
- Review action patch доведен до рабочего legacy-compatible режима:
  - legacy `status` больше не трогаем для action-команд;
  - операционное состояние пишется в `source_payload.queue_status`;
  - служебные метки `taken_by` / `closed_by` тоже живут в `source_payload`.

## Что уже живет в flow

- `sendPhoto` для фото.
- feedback loop командами и свободным текстом.
- trusted reviewers:
  - `@SunRaySword` → `super_admin`
  - `@OnlineElena` → `business`
- review-команды:
  - `/review_today`
  - `/review_pending`
  - `/review_candidates`
  - `/review_stats`
  - `/review_help`
  - `/review_high`

## Live smoke tests

- Старый набор до publish:
  - `1232`
  - `1233`
  - `1234`
- После publish:
  - `1235` — `@SunRaySword`, trusted `super_admin`, success
  - `1237` — `@OnlineElena`, trusted `business`, success
  - `1236` / `1238` / `1239` — поймали legacy-конфликт таблицы `advisor_review_queue` по статусу `candidate`
  - `1240` — обычный пользователь, `candidate`, success после compatibility patch
- Новые live-проверки review reports:
  - `1248` — `/review_today`, success
  - `1249` — `/review_candidates`, success
  - `1250` — `/review_pending`, success
  - `1252` — `/review_today` после фикса сводки, success
  - `1253` — `/review_stats`, success
  - `1254` — `/review_help`, success
  - `1255` — `/review_high`, success
  - `1256` — `/review_next`, success
  - `1266` — `/review_help`, success после rollback на рабочую версию
  - `1273` — `/review_help`, success после action-patch fix
  - `1274` — `/review_next`, success после action-patch fix
  - `1277` — `/review_help`, success
  - `1278` — `/review_next`, success
  - `1281` — `/review_take whieda-tg-700023-990023`, success
  - `1282` — `/review_close whieda-tg-700023-990023`, success

## Review actions

- Первая action-ветка сломалась на `Code: Review Queue Action Prep`:
  - в `jsCode` попали буквальные `\n`, а не реальные переводы строк;
  - из-за этого падали даже `/review_help` и `/review_next`.
- Это исправлено в локальном патче `v3`.
- Следующая проблема всплыла уже в SQL action-ветке:
  - executions `1279` / `1280` падали на legacy constraint `advisor_review_queue_status_check`;
  - причина: старая таблица не принимала `in_work` / `closed` в колонке `status`.
- Финальный совместимый live-режим:
  - action-команды не меняют legacy `status`;
  - рабочее состояние пишется в `source_payload.queue_status`;
  - служебные поля action-цикла тоже пишутся в `source_payload`.
- В итоге подтверждено живьем:
  - `/review_help` жив;
  - `/review_next` жив;
  - `/review_take` жив;
  - `/review_close` жив.

## Локально готово к следующему накату

- Обновлена миграция:
  - `D:\Projects\WHIEDA\postgres\sql\WHIEDA_review_queue_schema_V03.sql`
  - что добавлено:
    - явные поля `queue_status`, `trust_level`, `priority`, `owner`
    - явные поля цикла: `taken_by`, `taken_at`, `applied_by`, `applied_at`, `verified_by`, `verified_at`, `closed_by`, `closed_at`
    - backfill из `source_payload`
    - views:
      - `advisor_review_queue_actionable_view`
      - `advisor_review_queue_candidates_view`
      - `advisor_review_queue_stats_view`
  - совместимость сохранена:
    - `status` тоже синхронизируется
    - `source_payload` остается как raw-контекст и fallback

- Собран новый локальный workflow artifact `v6`:
  - `D:\Projects\WHIEDA\n8n\workflow-backups\workflow_live_review_actions_v6.json`
  - в нем:
    - `review_apply`
    - `review_verify`
    - `review_next` видит `approved`
    - snapshot читает явные колонки с fallback на payload
    - action SQL пишет и в явные поля, и в `status`, и в `source_payload`

- Локальная валидация `v6` пройдена полностью:
  - `D:\Projects\WHIEDA\n8n\scripts\validate_review_commands.js`
  - проверено:
    - regex команд
    - help
    - `take/apply/verify/close`
    - snapshot query
    - action query

- Сгенерирован следующий SQL patch для workflow:
  - `D:\Projects\WHIEDA\postgres\sql\advisor-whieda-phase1_review_actions_v6_patch_2026-07-12.sql`

## Smoke pack

- Собран первый рабочий каркас smoke pack:
  - cases:
    - `D:\Projects\WHIEDA\n8n\scripts\whieda_smoke_pack_cases.json`
  - report builder:
    - `D:\Projects\WHIEDA\n8n\scripts\build_smoke_pack_report.js`
  - первый отчет:
    - `D:\Projects\WHIEDA\n8n\live-exports\WHIEDA_smoke_pack_report_2026-07-12.md`
  - telemetry builder:
    - `D:\Projects\WHIEDA\n8n\scripts\build_route_telemetry_report.js`
  - первая telemetry summary:
    - `D:\Projects\WHIEDA\n8n\live-exports\WHIEDA_route_telemetry_2026-07-12.md`

- Что показал первый прогон:
  - это пока не финальный pass-rate продукта;
  - это в первую очередь проверка наличия достаточных execution summary;
  - сейчас есть подтвержденный `PASS` по `review_next`;
  - часть price/photo/follow-up кейсов пока не попала в локальную выборку summary и отмечена как `summary_not_found`;
  - один старый action sample показал пустой summary, это полезный сигнал для добора нормальных execution export.
- Что показала первая telemetry summary:
  - текущая сводка собрана по `10` summary;
  - `no_llm_rate = 20%` на имеющейся неполной выборке;
  - `photo_count = 0`, значит нормального photo-summary пакета для метрик пока нет;
  - `missing_route` и `missing_answer_mode` относятся к старым неполным summary и сами по себе являются сигналом на добор telemetry.

## Что уже подтверждено живьем

- `/review_today` больше не считает всю очередь как "сегодня".
- `/review_stats` дает короткую общую сводку очереди.
- `/review_help` отдает список доступных review-команд.
- `/review_high` работает как отдельный срез по high priority.
- Для review-команд есть локальная регрессионная проверка:
  - `validate_review_commands.js`
  - базовая live-проверка: `node validate_review_commands.js workflow_live_review_next.json`
  - action-ветка локально: `node validate_review_commands.js workflow_live_review_actions.json --actions`

## Dify

- Страница `signin` доступна.
- На странице явно стоит `data-support-mail-login="false"`.
- Вывод: обычный mail/password login через стандартный endpoint сейчас не подтвержден и, вероятно, выключен.
- Значит, Dify пока не считаем блокером для текущей работы по `n8n` и review loop.

## Runtime DB map

- Серверный compose-контур подтвержден:
  - `n8n`
  - локальный `postgres`
- Локальная служебная БД `n8n` точно существует и содержит:
  - `advisor_structured_products`
  - `advisor_structured_aliases`
  - `advisor_structured_resources`
  - `advisor_review_queue`
  - другие `advisor_*` / `content_*` / `publish_*` таблицы
- Но в локальной БД `n8n` сейчас:
  - `advisor_review_queue = 0`
  - `advisor_events = 0`
  - `advisor_users = 0`
  - `advisor_conversations = 0`
- Сильнейшая текущая гипотеза:
  - workflow credential `advisor-dev-postgres` смотрит не в локальную служебную БД `n8n`, а в отдельную runtime Postgres-точку
- Отдельная карта:
  - `WHIEDA_RUNTIME_DB_MAP_2026-07-11.md`

## Следующий live-safe шаг

1. Добрать чистые execution summaries в API-формате для свежих success runs `1289-1294`.
2. Пересобрать smoke report уже по актуальному live состоянию.
3. Пересобрать route telemetry с учетом нового review loop.
4. Добавить в smoke pack отдельные demo-cases:
   - цена
   - фото
   - trusted review commands
   - trusted action lifecycle
   - medical safety
5. После этого идти в следующий demo-слой:
   - bundles / product cards / stronger structured answers.
## Runtime DB clarification 2026-07-13

- Подтверждено live: runtime credential `advisor-dev-postgres` смотрит не в локальный Postgres рядом с `n8n`, а в Supabase pooler runtime DB.
- Рабочий runtime target для текущего live-контура:
  - host: `aws-0-eu-west-1.pooler.supabase.com`
  - port: `6543`
  - db: `postgres`
- Поэтому накат только в VPS/Postgres был недостаточен: workflow оставался жив, но review action SQL читал старую схему в другом runtime.
- Корневая причина live-падения executions `1283-1288`:
  - `Postgres: Review Queue Snapshot`
  - ошибка: `column "queue_status" does not exist`

## Live recovery 2026-07-13

- На runtime Supabase DB накатан отдельный безопасный schema patch под реальное старое состояние `advisor_review_queue`.
- После накатки на runtime подтверждено:
  - `advisor_review_queue` расширена до явных operational columns;
  - созданы views для actionable/candidates/stats;
  - создана `advisor_trusted_reviewers`;
  - trusted reviewers заведены:
    - `SunRaySword` -> `super_admin`
    - `OnlineElena` -> `business`

## Live smoke 2026-07-13

- Второй smoke cycle после runtime migration:
  - `1289` - success
  - `1290` - success
  - `1291` - success
  - `1292` - success
  - `1293` - success
  - `1294` - success
- Живьем подтвержден полный operational cycle:
  - `/review_help`
  - `/review_next`
  - `/review_take whieda-tg-700023-990023`
  - `/review_apply whieda-tg-700023-990023`
  - `/review_verify whieda-tg-700023-990023`
  - `/review_close whieda-tg-700023-990023`

## Confirmed final state of probe item

- Для `source_ref = whieda-tg-700023-990023` в runtime DB подтверждено:
  - `status = closed`
  - `queue_status = closed`
  - `owner = content`
  - `priority = medium`
  - `taken_by = sunraysword`
  - `applied_by = sunraysword`
  - `verified_by = sunraysword`
  - `closed_by = sunraysword`
- Вывод:
  - feedback/review queue больше не только "почти работает";
  - operational review loop реально живет в prod.


## Review smoke and telemetry refresh 2026-07-13

- Собран устойчивый локальный decoder для `execution_*_live_db.json`:
  - `D:\Projects\WHIEDA\n8n\scripts\decode_db_execution.py`
- Read-only подтверждены последние successful live executions review-цикла:
  - `1289` — `/review_apply whieda-tg-700023-990023`
  - `1290` — `/review_next`
  - `1291` — `/review_take whieda-tg-700023-990023`
  - `1292` — `/review_verify whieda-tg-700023-990023`
  - `1293` — `/review_help`
  - `1294` — `/review_close whieda-tg-700023-990023`
- По этим live execution собраны новые артефакты:
  - `D:\Projects\WHIEDA\n8n\live-exports\WHIEDA_review_actions_smoke_report_2026-07-13.md`
  - `D:\Projects\WHIEDA\n8n\live-exports\WHIEDA_route_telemetry_2026-07-13.md`
- Результат review smoke pack:
  - `cases = 6`
  - `passed = 6`
  - `failed = 0`
  - `pass_rate = 100%`
- Route telemetry по этому блоку:
  - routes: только `answer`
  - answer mode: только `direct_review_report`
  - `no_llm_rate = 100%`
  - `validation_errors = 0`
  - `photo_sends = 0`
- Вывод:
  - review action lifecycle подтвержден на свежем live-прогоне полностью;
  - для demo есть отдельный воспроизводимый smoke-набор именно по review loop.
