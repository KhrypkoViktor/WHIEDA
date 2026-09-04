# Structured Sync Safety P0: deploy guard fix

## Причина

Текущий `--apply` создаёт/сохраняет оба workflow с `active=false`.
Если основной live workflow уже активен, helper выключает рабочий cron-sync после записи патча. Это недопустимо.

## Задача

Исправить только `n8n/current/prepare_whieda_structured_sync_safety_deploy_2026-08-10.py` и его тесты.

1. До любой записи прочитать `active` у существующего main workflow.
2. Сохранить оба patch workflow неактивными для проверки, как сейчас.
3. После успешного сохранения:
   - если main был активен до начала, восстановить `active=true` у main;
   - активировать Error Audit workflow только после того, как его id реально записан в `settings.errorWorkflow` main workflow;
   - если main был неактивен, не активировать ничего автоматически.
4. При любой ошибке после первой записи:
   - восстановить сохранённый backup main workflow;
   - не оставлять новый Error Audit workflow активным;
   - вернуть non-zero exit и ясный `rollback` в отчёте.
5. В dry-run не должно быть сети и никаких записей.
6. Не менять cron, Google Sheets, Postgres runtime или Telegram.

## Обязательные тесты

- active main -> apply plan сохраняет active main и активирует error workflow только после link;
- inactive main -> apply plan не активирует workflow;
- падение после записи main -> rollback main и inactive error;
- dry-run не создаёт HTTP session / network request;
- существующие Safety P0 тесты остаются зелёными.

## Результат

Один focused commit. В отчёте отдельно: prior main state, final main state, final error workflow state, backup path, rollback state.
