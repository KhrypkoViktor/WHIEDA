# Core Gate C: Durable Telegram Inbox/Outbox V1

Дата: 2026-08-20  
Владелец: Core lead  
Исполнитель: Core developer  
Статус: выполнить локально; без deploy, shared staging и production.

## Зачем

Текущий webhook отвечает Telegram `200`, затем ставит обработку в память
процесса. При restart после ACK сообщение может потеряться, а несколько Core
процессов не имеют общей дедупликации. Для NSP и следующих tenant нужен
устойчивый контур: принять update, зафиксировать его, ответить ACK, обработать
один раз или безопасно повторить.

## База и границы

Новый clean worktree от `d053ee2` (`core-binding-stage-readiness`).
Новая ветка: `core-telegram-durable-inbox`.

Разрешены только:

- `backend/platform-api/app/telegram/**`;
- `backend/platform-api/tests/test_telegram_*`;
- одна additive PostgreSQL migration и staging-proof файлы;
- `postgres/scripts/**`, относящиеся только к inbox/outbox;
- этот ТЗ и focused report/manifest.

Не трогать: n8n, webhook Telegram, production, shared staging, сайт, cart,
pricing, Sheets, Dify, admin cabinet, legacy workflow. Не merge в грязный
`feat/platform-scale-core`.

## Обязательное поведение

1. Active valid binding: до HTTP ACK update атомарно записывается в durable
   inbox с `binding_id`, `tenant_id`, Telegram `update_id`, минимально нужным
   payload и временем приёма.
2. Уникальность update: `(binding_id, telegram_update_id)`. Один и тот же
   update в разных binding может существовать отдельно.
3. Unknown/disabled binding: HTTP 200 ACK, но inbox-записи нет.
4. Bad secret: 403, inbox-записи нет. Unavailable/misconfigured binding: 503,
   inbox-записи нет.
5. Worker забирает только pending записи через безопасную блокировку. После
   crash/lease timeout запись можно обработать повторно.
6. Внешняя отправка использует исключительно `BotBindingContext` записи.
   NSP не может отправить ответ WHIEDA token.
7. Outbox/статус доставки не должен сделать два одинаковых бизнес-ответа из
   одного успешно обработанного inbox update. Если полная exactly-once
   доставка недостижима из-за Telegram API, явно зафиксировать границу и
   обеспечить at-least-once с idempotency key.
8. Raw Telegram token, URL с token, телефон и raw chat id не попадают в logs.
   Не хранить лишние поля update без причины.

## SQL

Создать одну additive migration. В ней нужны минимум:

- inbox id, binding/tenant/update identity, state, received/lease/processed
  timestamps, retry count, безопасное error summary;
- outbox или эквивалентная delivery identity, если это необходимо для
  idempotent delivery;
- индексы pending/lease и RLS/tenant scope по действующему паттерну;
- migration идемпотентна и отдельна от seed/backfill.

Добавить миграцию в staging apply один раз в порядке после binding-context
migration. Локальный proof обязан применить весь порядок дважды на пустой DB.

## Тесты

Нужны реальные unit/contract и local staging checks:

- одинаковый update дважды в одном binding -> одна inbox-запись;
- один update id в WHIEDA и NSP -> две изолированные записи;
- параллельное получение -> один worker owner;
- worker crash/expired lease -> допустимый retry;
- processed update не отправляется повторно;
- disabled/unknown/bad-secret не сохраняются;
- ошибка Telegram delivery -> управляемый retry без чужого token;
- restart-scenario моделируется не mock-only флагом, а состоянием БД;
- apply x2, RLS и cross-tenant proof;
- весь предыдущий binding/B1/B2 suite зелёный.

## Результат

Один focused commit и push ветки. В `from-core` положить:

- `CORE_GATE_C_DURABLE_INBOX_MANIFEST_V1.json`;
- `CORE_GATE_C_DURABLE_INBOX_LOCAL_REPORT.md`;
- команды, фактические результаты и известную границу exactly-once.

Никакого live запуска. После review этого commit lead сам решает, когда
объединять Core slices и открывать shared staging gate.
