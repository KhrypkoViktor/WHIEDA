# n8n: согласие на рассылку — инструкция для лида (26.09.2026)

Исполнитель в n8n ничего не публиковал. Два воркфлоу, два патч-скрипта в
`n8n/current/`, у каждого есть офлайн-режим и `--dry-run`. Оба проверены офлайн
на снимках (заявки — на `wwc_website_leads_p0_after_crm_card.json`, рассылка — на
`workflow()` из publish-скрипта): диффы ниже совпадают с тем, что скрипты сделают вживую.

## Порядок

1. **Сначала миграция Core** `postgres/sql/platform_marketing_consent_v17.sql`
   (staging → бой в окно релиза). Без неё INSERT заявки упадёт на отсутствующем
   столбце `website_leads.marketing_consent`, а join рассылки — на отсутствующей
   таблице `telegram_marketing_consents`.
2. Релиз Core (ветка `core/legal-consents-v2`) — бот начинает записывать ответы.
3. Патч заявок (п. A). До него поле `marketing_consent` с сайта просто теряется,
   заявки не ломаются.
4. Патч рассылки (п. B). До него рассылка идёт по старому флагу `is_subscribed`.
   После него — **только тем, кто нажал кнопку в боте**; пока никто не нажал,
   получателей ноль (это ожидаемо и законно).

## A. Заявки: `wwc-website-leads-p0` («WWC Website Leads P0.3 · country gate»)

```bash
cd n8n/current
python patch_wwc_website_leads_marketing_consent_2026-09-26.py --dry-run
python patch_wwc_website_leads_marketing_consent_2026-09-26.py
```

Скрипт: логин через `publish_and_run_whieda_sync_2026-07-13.py`, бэкап живого
воркфлоу в `n8n/backups/wwc-website-leads-p0-before-marketing-consent-<stamp>.json`,
патч двух узлов, PATCH + activate + `n8n publish:workflow`. Повторный запуск
отказывает («already patched»).

Узел **«Code: validate and assign owner»** — после `const honey = …`:

```js
// Галочка «Хочу получать новости и предложения» (38-ФЗ ст. 18, 26.09.2026):
// только явное «да», снятая галочка или чужое значение — false.
const marketingConsent = [true, 'true', '1', 1, 'on', 'yes'].includes(body.marketing_consent);
```

и в объекте `return [{ json: { … } }]` после `idempotency_key: idempotency,`:

```js
  marketing_consent: marketingConsent,
```

Узел **«Postgres: save lead and audit»** — в `INSERT INTO website_leads (…)` список
столбцов заканчивается так:

```sql
    ref_profile_version, service_location_id, country_code, city, idempotency_key, metadata,
    marketing_consent, marketing_consent_at
  )
```

а в `SELECT` после строки с `metadata` (`…jsonb_build_object('routing_error', routing.routing_error)))`)
добавляются два значения:

```sql
    ('{{ JSON.stringify($json.metadata).replace(/'/g, '') }}'::jsonb || jsonb_strip_nulls(jsonb_build_object('routing_error', routing.routing_error))),
    {{ $json.marketing_consent === true ? 'true' : 'false' }}::boolean,
    {{ $json.marketing_consent === true ? 'now()' : 'NULL' }}
  FROM routing
```

Проверка после патча: отправить тестовую заявку с галочкой и без —
`select public_id, marketing_consent, marketing_consent_at from website_leads order by created_at desc limit 2`.

## B. Рассылка: «WHIEDA Broadcast Delivery Worker»

```bash
cd n8n/current
python patch_whieda_broadcast_marketing_consent_2026-09-26.py --dry-run
python patch_whieda_broadcast_marketing_consent_2026-09-26.py
```

Воркфлоу ищется по имени (id в репозитории не зафиксирован). Бэкап —
`n8n/backups/whieda-broadcast-delivery-before-marketing-consent-<stamp>.json`.

Узел **«Postgres: Queue Broadcast Deliveries»**, CTE `recipients`:

```sql
  JOIN advisor_structured_users_access a ON a.client_id = s.client_id AND a.telegram_user_id = s.telegram_user_id
  JOIN telegram_marketing_consents mc ON mc.tenant_id = s.client_id AND mc.telegram_user_id::text = s.telegram_user_id AND mc.opted_in IS TRUE
  WHERE s.blocked_at IS NULL
```

вместо

```sql
  JOIN advisor_structured_users_access a ON a.client_id = s.client_id AND a.telegram_user_id = s.telegram_user_id
  WHERE s.is_subscribed IS TRUE AND s.blocked_at IS NULL
```

Почему `is_subscribed` убран, а не добавлен к условию: флаг ставил `/subscribe`
в старом n8n-советнике, по умолчанию false, «не спрашивали» и «отказался» в нём
не различимы; Core эту команду не знает, новые люди флаг не получают. Единственное
действительное согласие — явное, из `telegram_marketing_consents`. Отказ там тоже
записан (`opted_in = false`), поэтому в фильтр он не попадает.

То же изменение внесено в `publish_whieda_broadcast_delivery_worker_2026-07-14.py`
(QUEUE_SQL): если воркер когда-нибудь переопубликуют целиком, фильтр не откатится.

Не трогал: счётчики `*_subscriber_count` в советнике (предпросмотр владельца
перед рассылкой) — они по-прежнему считают по `is_subscribed`, это только число на
экране. Если нужно, чтобы предпросмотр совпадал с фактом, заменить в том запросе
`s.is_subscribed IS TRUE` на такой же join к `telegram_marketing_consents`.

## RLS и роль n8n

`telegram_marketing_consents` под RLS `platform_current_tenant_id()`, как
`telegram_consents` и `website_leads`. n8n уже пишет в `website_leads` и читает
`telegram_*` под своей ролью; если для новой таблицы окажется, что роль n8n
не владелец и не bypassrls, символ проблемы — пустой список получателей при
существующих `opted_in = true`. Тогда: `grant select on telegram_marketing_consents to <роль n8n>`
и `set_config('app.tenant_id', 'whieda', true)` в начале запроса, или политика
для роли n8n — как решено для остальных таблиц с RLS.

## Как люди дают согласие (чтобы список получателей не был пустым)

- Новые: на первом `/start` после уведомления 152-ФЗ — сообщение с кнопками
  «✅ Хочу получать новости и предложения» / «Не сейчас».
- Существующие: вопрос задаётся на любом `/start`, пока человек ни разу не ответил
  (в таблице нет строки). Плюс команды `/news` (кнопки), `/news_on`, `/news_off`,
  слово «рассылка». Одна служебная рассылка «нажмите /news, если хотите получать
  новости» — не реклама; решение за владельцем.
