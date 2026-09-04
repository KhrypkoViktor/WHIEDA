# WHIEDA review queue - operational plan

Дата: 2026-07-12

## Что уже есть

- live read-only review commands:
  - `/review_today`
  - `/review_pending`
  - `/review_candidates`
  - `/review_stats`
  - `/review_help`
  - `/review_high`
  - `/review_next`
- trusted reviewers:
  - `@SunRaySword` -> `super_admin`
  - `@OnlineElena` -> `business`

## Что мешает глубине сейчас

- очередь все еще живет в режиме "явные данные + важное в source_payload";
- из-за этого любые следующие write-команды будут хрупкими;
- нет нормального batch-cycle:
  - взять задачу,
  - пометить в работе,
  - применить,
  - закрыть/верифицировать.

## Следующий правильный шаг

Нормализовать `advisor_review_queue` до явной операционной схемы.

Для этого подготовлен:

- `WHIEDA_review_queue_schema_V03.sql`

Что он дает:

- backfill полей из `source_payload` в явные колонки;
- отдельный actionable view;
- отдельный candidates view;
- отдельный stats view;
- индексы под owner/status/priority/type/layer.

## После V03 можно делать

1. `/review_next business`
2. `/review_next medical`
3. `/review_take <source_ref>`
4. `/review_apply <source_ref>`
5. `/review_close <source_ref>`
6. controlled batch processing по trusted reviewers

## Текущее live-состояние по action-командам

- `review_help`, `review_next`, `review_take`, `review_close` уже подтверждены живьем.
- Рабочий совместимый режим сейчас такой:
  - legacy `status` не меняем;
  - action-state живет в `source_payload.queue_status`;
  - `taken_by` / `closed_by` / timestamps тоже пишутся в `source_payload`.
- Почему так:
  - live-таблица держит старый check constraint на `status`;
  - попытка писать туда `in_work` / `closed` ломает action-ветку.
- Значит, текущий live-путь уже рабочий и безопасный без немедленной миграции схемы.

## Безопасный порядок

1. read-only проверить реальную структуру live-таблицы;
2. держать текущий action-loop в legacy-compatible режиме;
3. отдельно подготовить V03 как additive migration;
4. прогнать select-проверки;
5. только потом переводить queue state в явные колонки.

## Что пока не делать

- не делать авто-закрытие;
- не пускать всех пользователей в action-команды;
- не смешивать review loop и medical approval как будто это один процесс;
- не писать destructive SQL без live readback.
