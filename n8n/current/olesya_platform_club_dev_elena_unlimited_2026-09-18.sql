-- 18.09.2026, владелец в чате:
--   olesya (Олеся Вселенная, owner olesya-vselennaya) — «закинула 10 000, зашла в
--   клуб и сайт на 3 месяца, 105 который; на балансе были бонусы — их учти».
--   Пакет bundle_pro_club_3m = 105 W$ = 10 500 ₽. Пришло 10 000 ₽ = 100 W$,
--   недостающие 5 W$ списываем с бонусного баланса (там 12 W$: два реферальных
--   кредита по 6 W$ от 14.09). Остаток бонусов после списания — 7 W$.
--   Записано по образцу sofiya/igoref: платёж + две строки ledger (PRO 30 + клуб 75),
--   клуб ещё и в partner_product_access — так делает сам бот (subscriptions/_extend_term),
--   иначе кабинет не показывает срок клуба.
--   dev и onlineelena (elena.wwc.best) — «безлимит»: paid_until далеко в будущем.
--   Попутно: у igoref клуб был записан только в ledger (ручной SQL 17.09) —
--   добавлена строка partner_product_access, чтобы кабинет показывал клуб до 21.12.
--
-- Запуск: python wwc_sql.py --file olesya_platform_club_dev_elena_unlimited_2026-09-18.sql

BEGIN;

-- 1. Платёж Олеси: 10 000 ₽ = 100 W$ (ledger ведётся в WUSD, как у остальных ручных записей)
INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('6f1e2b7a-9c3d-4e5f-8a1b-20260918a001', 'whieda', 'olesya', 10000, 'WUSD', 'telegram_manual', '688931415', '-2026091801', '688931415', md5('olesya:bundle_pro_club_3m:2026-09-18'), 'Пакет PRO + клуб 105 WWC$: 10 000 ₽ переводом + 5 WWC$ с бонусного баланса');

-- 2. (см. 3б — списание бонусов идёт после строк ledger: source_payment_id ссылается на partner_payment_ledger)

-- 3. Две строки доступа: PRO 30 W$ + клуб 75 W$, оба до 21.12.2026 включительно
INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, promo_note)
VALUES
  ('6f1e2b7a-9c3d-4e5f-8a1b-20260918b001', 'whieda', 'olesya', 3000, 'WUSD', 3, '2026-09-21 21:00:00+00', '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091801', '688931415', 'platform_subscription', '6f1e2b7a-9c3d-4e5f-8a1b-20260918a001', 'пакет PRO + клуб 105 WWC$ (10 000 ₽ + 5 WWC$ бонусов)'),
  ('6f1e2b7a-9c3d-4e5f-8a1b-20260918b002', 'whieda', 'olesya', 7500, 'WUSD', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091801', '688931415', 'club_subscription', '6f1e2b7a-9c3d-4e5f-8a1b-20260918a001', 'пакет PRO + клуб 105 WWC$ (10 000 ₽ + 5 WWC$ бонусов)');

-- 3б. Списание 5 W$ бонусов Олеси в зачёт пакета (debit — отрицательная сумма по check-ограничению;
-- source_payment_id — строка PRO в partner_payment_ledger, как у автосписаний Core)
INSERT INTO partner_bonus_ledger
  (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, source_payment_id, idempotency_key, rule_snapshot, description)
VALUES
  ('whieda', 'olesya-vselennaya', 'debit', -500, 'WUSD', 'platform_subscription', '6f1e2b7a-9c3d-4e5f-8a1b-20260918b001', 'payment:6f1e2b7a-9c3d-4e5f-8a1b-20260918b001:bonus_offset', '{"bonus_offset_minor": 500, "plan_code": "bundle_pro_club_3m", "manual": "владелец 18.09.2026"}'::jsonb, 'Bonus offset: 5 WWC$ towards bundle_pro_club_3m (10 000 ₽ received)');

-- 3а. Реферальный бонус: Олесю пригласила Марьям Шаби (ref mariam; в Core owner_id = 'mariam',
-- проверено 18.09 — в реестре сайта ownerId другой, это не ключ ledger) —
-- таблица Partner_Subscriptions, колонка «Реферер (ref)». Первая оплата PRO → +6 W$,
-- как у Ольги за Софию (правило 20 % от 30 W$). Ключ — как пишет сам Core.
INSERT INTO partner_bonus_ledger
  (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, source_payment_id, idempotency_key, rule_snapshot, description)
VALUES
  ('whieda', 'mariam', 'credit', 600, 'WUSD', 'platform_subscription', '6f1e2b7a-9c3d-4e5f-8a1b-20260918b001', 'payment:6f1e2b7a-9c3d-4e5f-8a1b-20260918b001:referral_credit',
   '{"basis_points": 2000, "base_wusd_minor": 3000, "plan_code": "bundle_pro_club_3m", "payment_kind": "first", "manual": "владелец 18.09.2026: Олеся от Марьям"}'::jsonb,
   'Referral bonus: first payment')
ON CONFLICT DO NOTHING;

UPDATE partner_subscriptions
SET paid_until = '2026-12-21 20:59:59+00', updated_at = now()
WHERE tenant_id = 'whieda' AND ref_code = 'olesya';

INSERT INTO partner_product_access (tenant_id, ref_code, product_code, paid_until)
VALUES
  ('whieda', 'olesya', 'club_subscription', '2026-12-21 20:59:59+00'),
  ('whieda', 'igoref', 'club_subscription', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code, product_code) DO UPDATE
SET paid_until = EXCLUDED.paid_until, updated_at = now();

-- 3в. Кто кого привёл — по колонке «Реферер (ref)» в Partner_Subscriptions (18.09): в Core этих связей
-- не было, поэтому бот не начислял бы бонус пригласившему. После этого оплату Фёдорова (3 000 ₽, PRO 3 мес.)
-- владелец записывает прямо в боте: «оплата ref:fedorov 3000 RUB 3» — бот сам начислит Олесе +6 W$.
INSERT INTO partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
VALUES
  ('whieda', 'fedorov', 'olesya-vselennaya', NULL, 'admin_manual'),
  ('whieda', 'olesya-vselennaya', 'mariam', NULL, 'admin_manual'),
  ('whieda', 'makarova', 'mariam', NULL, 'admin_manual')
ON CONFLICT DO NOTHING;

-- 4. Безлимит: dev (технический сайт владельца) и onlineelena (elena.wwc.best)
UPDATE partner_subscriptions
SET paid_until = '2099-12-31 20:59:59+00', updated_at = now()
WHERE tenant_id = 'whieda' AND ref_code IN ('dev', 'onlineelena');

COMMIT;

SELECT s.ref_code, s.paid_until::text AS pro_until, pa.paid_until::text AS club_until,
       (SELECT sum(b.amount_minor) FROM partner_bonus_ledger b WHERE b.tenant_id = p.tenant_id AND b.actor_id = p.owner_id)::text AS bonus_minor
FROM partner_subscriptions s
JOIN referral_profiles p ON p.tenant_id = s.tenant_id AND p.ref_code = s.ref_code
LEFT JOIN partner_product_access pa ON pa.tenant_id = s.tenant_id AND pa.ref_code = s.ref_code AND pa.product_code = 'club_subscription'
WHERE s.tenant_id = 'whieda' AND s.ref_code IN ('olesya', 'dev', 'onlineelena', 'igoref', 'mariam')
ORDER BY s.ref_code;
