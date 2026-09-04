# WHIEDA review queue logic

Дата: 2026-07-11

## Что подтверждено

### 1. Trusted reviewers в live workflow зашиты прямо в `Code: Normalize Payload`

- `sunraysword` -> `super_admin`
- `onlineelena` -> `business`

На входе workflow сразу вычисляются поля:
- `trusted_reviewer`
- `trusted_reviewer_role`
- `trusted_reviewer_name`
- `external_username`

### 2. Как классифицируется замечание

В `Code: Validate Dify Response` замечание раскладывается по:
- `review_type`
- `target_layer`
- `review_priority`
- `review_owner`

Примеры:
- медицина -> `medical_review` / `medical_safety`
- фото/ссылки/pdf -> `media_issue` / `resource_links`
- цены/PV/W$/SKU -> `structured_data_issue` / `products_prices`
- алиасы/поиск -> `structured_data_issue` / `product_aliases`
- бандлы -> `content_task` / `solution_bundles`
- тон/продажность/вкус ответа -> `business_voice_review` / `voice_rules`
- дубли/зависания/не отправил -> `runtime_bug` / `n8n_workflow`

### 3. Как решается `pending` или `candidate`

В `deriveReviewQueueMeta()` сейчас логика такая:

- если `trusted_reviewer = true` -> `queue_status = pending`
- если замечание high-risk -> тоже `queue_status = pending`
- иначе -> `queue_status = candidate`

High-risk сейчас:
- `review_priority = high`
- или `review_type = runtime_bug`
- или `review_type = medical_review`

### 4. Как решается trust level

- trusted reviewer -> `review_trust_level = trusted`
- обычный пользователь -> `review_trust_level = candidate`

### 5. Что пишется в Postgres

`Postgres: Write Review Queue` пишет в `advisor_review_queue`:
- `client_id`
- `source_type`
- `source_ref`
- `source_title`
- `source_payload`
- `status`

Ключевое:
- в `status` пишется именно `review_queue_status`
- в `source_payload` кладутся:
  - `review_type`
  - `target_layer`
  - `priority`
  - `owner`
  - `queue_status`
  - `trust_level`
  - `trusted_reviewer`
  - `trusted_reviewer_role`
  - `trusted_reviewer_name`
  - `external_username`
  - `feedback_type`
- `feedback_text`
- `gap_reason`
- `user_text`
- `bot_answer`

## Что подтверждено live дополнительно

- `1235`:
  - `@SunRaySword`
  - trusted `super_admin`
  - `review_queue_status = pending`
  - `write_review_queue.success = true`
- `1237`:
  - `@OnlineElena`
  - trusted `business`
  - `review_queue_status = pending`
  - `write_review_queue.success = true`
- `1240`:
  - обычный пользователь
  - `review_queue_status = candidate`
  - `review_trust_level = candidate`
  - `write_review_queue.success = true`

## Важный legacy-нюанс

- Live-таблица `advisor_review_queue` пока еще старая:
  - `client_id, source_type, source_ref, source_title, source_payload, status`
- Поэтому сейчас работает compatibility bridge:
  - реальный `candidate/pending` сохраняется в `source_payload.queue_status`
  - trusted/candidate логика не теряется
  - для старой колонки `status` ordinary `candidate` временно пишется совместимо, чтобы runtime не падал
- Это не финальная архитектура, а безопасный рабочий мост до нормальной миграции схемы очереди.

## Что это значит простым языком

- Виктор и Елена уже сейчас могут поднимать замечания сразу в рабочую очередь.
- Обычные пользователи не ломают поток: их замечания падают как кандидаты.
- Опасные и системные баги тоже не теряются: они автоматически идут в `pending`.

## Что еще не подтверждено до конца

- Нужно снять карту именно той runtime DB, куда реально смотрит credential `advisor-dev-postgres`.
- Потом уже чисто мигрировать очередь на явные поля:
  - `queue_status`
  - `trust_level`
  - `review_type`
  - `target_layer`
  - `review_owner`
  - `review_priority`
