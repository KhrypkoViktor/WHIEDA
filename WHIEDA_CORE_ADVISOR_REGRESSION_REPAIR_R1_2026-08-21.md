# Core Repair R1: Advisor Regression Cleanup

Дата: 2026-08-21  
Исполнитель: Core developer  
База: `6d83fa6` (`core-tenant-release-package`)  
Ветка: `core-advisor-regression-repair-r1`

## Почему

Gate E package firewall локально работает, но полный Core slice был запущен с
двумя `--deselect`. Это не принимается как зелёный полный suite: оба кейса
влияют на Telegram UX.

## Исправить ровно два дефекта

1. Вопрос цены без известного товара.

`сколько стоит?` без product/session context должен вернуть точное
clarification из `load_clarification_prompt` (например «О каком товаре хотите
узнать цену?»), а не universal menu и не `knowledge_gap`.

2. Явный запрос товара перекрывает discovery.

`фото Активатор PRO` при старом comparison/session context обязан показывать
фото PRO. Product discovery не должен выполняться раньше explicit product
resolution и не должен требовать прямого DB cursor в unit path.

## Границы

Только advisor engine/routing и точечные тесты. Не менять ожидания тестов,
не использовать `--deselect`, не ослаблять assertions, не трогать tenant
изоляцию, release package, SQL, n8n, сайт, deploy и staging.

## Acceptance

```powershell
cd backend/platform-api
python -m pytest tests/test_advisor_sql_engine.py -q
python -m pytest tests/test_telegram_binding_ingress.py tests/test_telegram_chat_sequencer.py tests/test_telegram_delivery.py tests/test_telegram_durable_inbox.py tests/test_telegram_log_safe.py tests/test_telegram_navigation.py tests/test_telegram_photo_delivery.py tests/test_telegram_processor.py tests/test_telegram_route_truth_table.py tests/test_advisor_contract.py tests/test_advisor_followup_context.py tests/test_advisor_sql_engine.py tests/test_advisor_tenant_data_plane.py tests/test_local_staging_proof.py tests/test_shared_staging_release_harness.py tests/test_staging_sql_order.py tests/test_tenant_release_package.py -q
```

Ноль `deselect`, все тесты зелёные. Один focused commit и короткий report в
`D:\Projects\_peer-sync\nsp-whieda\from-core`.
