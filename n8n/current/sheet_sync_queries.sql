-- Запросы синка «Core → таблица WWC» (19.09.2026). Три вкладки, одна логика на
-- таблицу и будущий дашборд. Только чтение. Каждый запрос отделён маркером
-- «-- @tab <имя>»; publish_core_sheet_sync.py режет файл по маркерам.
-- Проверка руками: python wwc_sql.py --file sheet_sync_queries.sql  (выполнит последний).

-- @tab Партнёры
-- Одна строка на партнёра. Даты — «до» включительно по Москве. «Статус» — по сайту (PRO):
-- активен / grace (3 дня после) / истёк / нет. Клуб: поток = порядковый номер месяца
-- первой оплаты клуба (сентябрь 2026 = поток 1). Курсы — по product_code course_%.
WITH club AS (
  SELECT l.ref_code, min(l.period_start) AS first_start
  FROM partner_payment_ledger l
  WHERE l.tenant_id = 'whieda' AND l.product_code = 'club_subscription'
  GROUP BY l.ref_code
), cohorts AS (
  SELECT date_trunc('month', first_start) AS m, dense_rank() OVER (ORDER BY date_trunc('month', first_start)) AS n
  FROM club GROUP BY date_trunc('month', first_start)
), bonus AS (
  SELECT b.actor_id, sum(b.amount_minor) AS minor FROM partner_bonus_ledger b WHERE b.tenant_id = 'whieda' GROUP BY b.actor_id
), invited AS (
  SELECT a.inviter_actor_id, count(*) AS n FROM partner_referral_attributions a WHERE a.tenant_id = 'whieda' GROUP BY a.inviter_actor_id
), paid_invited AS (
  SELECT a.inviter_actor_id, count(DISTINCT p.ref_code) AS n
  FROM partner_referral_attributions a
  JOIN referral_profiles p ON p.tenant_id = a.tenant_id AND p.owner_id = a.invitee_actor_id
  JOIN partner_payment_ledger l ON l.tenant_id = p.tenant_id AND l.ref_code = p.ref_code AND l.amount_minor > 0
  WHERE a.tenant_id = 'whieda' GROUP BY a.inviter_actor_id
)
SELECT
  coalesce(la.display_name, p.public_profile->>'display_name', p.ref_code)      AS "Имя",
  p.ref_code                                                                      AS "ref",
  coalesce(p.public_profile->>'public_site_url', 'https://' || p.ref_code || '.wwc.best/') AS "Сайт",
  CASE WHEN la.telegram_username IS NOT NULL AND la.telegram_username <> '' THEN '@' || la.telegram_username ELSE '' END AS "Telegram",
  CASE p.country_code WHEN 'RU' THEN 'РФ' WHEN 'BY' THEN 'РБ' ELSE coalesce(p.country_code, '') END AS "Страна",
  CASE
    WHEN s.paid_until IS NULL THEN 'нет'
    WHEN s.paid_until >= '2099-01-01' THEN 'безлимит'
    WHEN now() < s.paid_until THEN 'активен'
    WHEN now() < s.paid_until + interval '3 days' THEN 'grace'
    ELSE 'истёк'
  END                                                                             AS "Статус",
  CASE WHEN s.paid_until IS NULL THEN '' WHEN s.paid_until >= '2099-01-01' THEN 'безлимит'
       ELSE to_char((s.paid_until AT TIME ZONE 'Europe/Moscow') - interval '1 second', 'DD.MM.YYYY') END AS "Сайт до",
  CASE WHEN pa.paid_until IS NULL THEN '' ELSE to_char((pa.paid_until AT TIME ZONE 'Europe/Moscow') - interval '1 second', 'DD.MM.YYYY') END AS "Клуб до",
  CASE WHEN c.first_start IS NULL THEN '' ELSE 'поток ' || co.n END               AS "Клуб поток",
  coalesce((SELECT string_agg(DISTINCT replace(l.product_code, 'course_', ''), ', ')
            FROM partner_payment_ledger l WHERE l.tenant_id = p.tenant_id AND l.ref_code = p.ref_code AND l.product_code LIKE 'course_%'), '') AS "Курсы",
  coalesce(round(bo.minor / 100.0, 2), 0)                                        AS "Бонусы W$",
  coalesce(inv.n, 0)                                                              AS "Приглашено",
  coalesce(pinv.n, 0)                                                             AS "Оплатили",
  coalesce((SELECT coalesce(la2.display_name, a.inviter_actor_id) FROM partner_referral_attributions a
            LEFT JOIN lead_actors la2 ON la2.actor_id = a.inviter_actor_id
            WHERE a.tenant_id = p.tenant_id AND a.invitee_actor_id = p.owner_id LIMIT 1), '') AS "Реферер",
  coalesce(round((SELECT sum(l.amount_minor) FROM partner_payment_ledger l WHERE l.tenant_id = p.tenant_id AND l.ref_code = p.ref_code AND l.currency = 'WUSD') / 100.0, 2), 0)
    + coalesce(round((SELECT sum(l.amount_minor) FROM partner_payment_ledger l WHERE l.tenant_id = p.tenant_id AND l.ref_code = p.ref_code AND l.currency = 'RUB') / 10000.0, 2), 0) AS "Оплачено всего, W$",
  la.telegram_chat_id::text                                                       AS "chat_id",
  p.owner_id                                                                      AS "owner_id",
  to_char(now() AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI')               AS "Обновлено"
FROM referral_profiles p
LEFT JOIN lead_actors la ON la.tenant_id = p.tenant_id AND la.actor_id = p.owner_id
LEFT JOIN partner_subscriptions s ON s.tenant_id = p.tenant_id AND s.ref_code = p.ref_code
LEFT JOIN partner_product_access pa ON pa.tenant_id = p.tenant_id AND pa.ref_code = p.ref_code AND pa.product_code = 'club_subscription'
LEFT JOIN club c ON c.ref_code = p.ref_code
LEFT JOIN cohorts co ON co.m = date_trunc('month', c.first_start)
LEFT JOIN bonus bo ON bo.actor_id = p.owner_id
LEFT JOIN invited inv ON inv.inviter_actor_id = p.owner_id
LEFT JOIN paid_invited pinv ON pinv.inviter_actor_id = p.owner_id
WHERE p.tenant_id = 'whieda' AND p.enabled = true AND p.ref_code NOT IN ('nnm')
ORDER BY (s.paid_until IS NULL), s.paid_until DESC NULLS LAST, p.ref_code;

-- @tab Платежи
-- Одна строка на строку платежа (продукт). Это журнал продаж: фильтр по месяцу — выручка.
SELECT
  to_char(l.created_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY')               AS "Дата",
  coalesce(la.display_name, l.ref_code)                                           AS "Партнёр",
  l.ref_code                                                                      AS "ref",
  CASE l.product_code
    WHEN 'platform_subscription' THEN 'Сайт (PRO)'
    WHEN 'club_subscription' THEN 'Клуб'
    WHEN 'site_setup' THEN 'Подключение сайта'
    ELSE CASE WHEN l.product_code LIKE 'course_%' THEN 'Курс: ' || replace(l.product_code, 'course_', '') ELSE l.product_code END
  END                                                                             AS "Что",
  CASE WHEN l.access_months > 0 THEN l.access_months::text ELSE '' END           AS "Мес.",
  CASE l.currency WHEN 'RUB' THEN round(l.amount_minor / 100.0, 2) ELSE round(l.amount_minor / 100.0, 2) END AS "Сумма",
  CASE l.currency WHEN 'RUB' THEN '₽' ELSE 'W' || chr(36) END                     AS "Вал.",  -- $ в литерале съедает узел n8n
  CASE l.currency WHEN 'RUB' THEN round(l.amount_minor / 10000.0, 2) ELSE round(l.amount_minor / 100.0, 2) END AS "W$",
  CASE WHEN l.period_end > l.period_start THEN to_char((l.period_end AT TIME ZONE 'Europe/Moscow') - interval '1 second', 'DD.MM.YYYY') ELSE '' END AS "Доступ до",
  coalesce(l.promo_note, '')                                                      AS "Акция / примечание",
  coalesce(pp.note, '')                                                           AS "Платёж",
  coalesce((SELECT string_agg(coalesce(la2.display_name, b.actor_id) || ' +' || round(b.amount_minor / 100.0, 0), '; ')
            FROM partner_bonus_ledger b LEFT JOIN lead_actors la2 ON la2.actor_id = b.actor_id
            WHERE b.tenant_id = l.tenant_id AND b.source_payment_id = l.payment_id AND b.entry_type = 'credit'), '') AS "Бонус рефереру",
  l.payment_id::text                                                              AS "payment_id"
FROM partner_payment_ledger l
LEFT JOIN lead_actors la ON la.tenant_id = l.tenant_id AND la.actor_id = (SELECT owner_id FROM referral_profiles p WHERE p.tenant_id = l.tenant_id AND p.ref_code = l.ref_code)
LEFT JOIN partner_payments pp ON pp.received_payment_id = l.received_payment_id
WHERE l.tenant_id = 'whieda'
ORDER BY l.created_at DESC, l.product_code;

-- @tab Бонусы
-- Журнал partner_bonus_ledger: начисления и списания, остаток по партнёру.
SELECT
  to_char(b.created_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY')               AS "Дата",
  coalesce(la.display_name, b.actor_id)                                           AS "Кому",
  coalesce((SELECT p.ref_code FROM referral_profiles p WHERE p.tenant_id = b.tenant_id AND p.owner_id = b.actor_id LIMIT 1), '') AS "ref",
  round(b.amount_minor / 100.0, 2)                                                AS "W$",
  CASE b.entry_type WHEN 'credit' THEN 'начисление' WHEN 'debit' THEN 'списание' WHEN 'reversal' THEN 'сторно' ELSE 'корректировка' END AS "Тип",
  CASE
    WHEN b.description LIKE 'Referral bonus: first%' THEN 'первая оплата приглашённого'
    WHEN b.description LIKE 'Referral bonus: renewal%' THEN 'продление приглашённого'
    WHEN b.description LIKE 'Bonus offset%' THEN 'в зачёт своей оплаты'
    WHEN b.description LIKE 'Automatic points%' THEN 'автопродление сайта'
    WHEN b.description LIKE 'Bonus redemption%' THEN 'обмен на подписку'
    ELSE coalesce(b.description, '')
  END                                                                             AS "За что",
  coalesce((SELECT coalesce(la2.display_name, l.ref_code) FROM partner_payment_ledger l
            LEFT JOIN lead_actors la2 ON la2.actor_id = (SELECT owner_id FROM referral_profiles p WHERE p.tenant_id = l.tenant_id AND p.ref_code = l.ref_code)
            WHERE l.payment_id = b.source_payment_id), '')                        AS "Кто оплатил",
  round(sum(b.amount_minor) OVER (PARTITION BY b.actor_id ORDER BY b.created_at, b.entry_id) / 100.0, 2) AS "Остаток W$",
  b.entry_id::text                                                                AS "entry_id"
FROM partner_bonus_ledger b
LEFT JOIN lead_actors la ON la.tenant_id = b.tenant_id AND la.actor_id = b.actor_id
WHERE b.tenant_id = 'whieda'
ORDER BY b.created_at DESC;
