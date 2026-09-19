-- 19.09.2026. Зоя (profit, РБ): пакет «Платформа + Клуб» 105 W$ → до 21.12 (PRO продлевается от
-- бесплатного входа 21.09). Реферер по таблице Partner_Subscriptions — onlineelena (Елена Дацкевич):
-- связь в Core заводим, бонус +6 W$ за первую оплату PRO начисляем.
-- Запуск: python wwc_sql.py --file payments_profit_2026-09-19.sql

BEGIN;

INSERT INTO partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
VALUES ('whieda', 'profit', 'onlineelena', NULL, 'admin_manual')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919b001', 'whieda', 'profit', 10500, 'WUSD', 'telegram_manual', '688931415', '-2026091906', '688931415', md5('profit:bundle_pro_club_3m:2026-09-19'), 'Пакет PRO + клуб 105 WWC$ (владелец, чат 19.09)')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, promo_note, list_price_minor)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919b002', 'whieda', 'profit', 3000, 'WUSD', 3, '2026-09-21 21:00:00+00', '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091906', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919b001', NULL, 3000),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919b003', 'whieda', 'profit', 7500, 'WUSD', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091906', '688931415', 'club_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919b001', 'пакет PRO + клуб 105 WWC$ (первый поток)', 12000)
ON CONFLICT DO NOTHING;

UPDATE partner_subscriptions SET paid_until = '2026-12-21 20:59:59+00', updated_at = now()
WHERE tenant_id = 'whieda' AND ref_code = 'profit';

INSERT INTO partner_product_access (tenant_id, ref_code, product_code, paid_until)
VALUES ('whieda', 'profit', 'club_subscription', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code, product_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

INSERT INTO partner_bonus_ledger
  (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, source_payment_id, idempotency_key, rule_snapshot, description)
VALUES
  ('whieda', 'onlineelena', 'credit', 600, 'WUSD', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919b002', 'payment:7a2c4e60-1b3d-4f5a-9c8e-20260919b002:referral_credit',
   '{"basis_points": 2000, "base_wusd_minor": 3000, "plan_code": "bundle_pro_club_3m", "payment_kind": "first", "manual": "владелец 19.09.2026: Зоя от Елены"}'::jsonb, 'Referral bonus: first payment')
ON CONFLICT DO NOTHING;

COMMIT;

SELECT s.ref_code, s.paid_until::text AS pro_until, pa.paid_until::text AS club_until,
       (SELECT sum(amount_minor) FROM partner_bonus_ledger WHERE actor_id = 'onlineelena')::text AS elena_bonus_minor
FROM partner_subscriptions s
LEFT JOIN partner_product_access pa ON pa.tenant_id = s.tenant_id AND pa.ref_code = s.ref_code AND pa.product_code = 'club_subscription'
WHERE s.tenant_id = 'whieda' AND s.ref_code = 'profit';
