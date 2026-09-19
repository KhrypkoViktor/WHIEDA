-- 19.09.2026. Оплаты, которые владелец назвал в чате; записаны в форме бота
-- (partner_payments → partner_payment_ledger → сроки → бонус пригласившему).
--   fedorov: 3 000 ₽, PRO 3 мес. Сайт был по бесплатному входу до 21.09 → продление
--            от 21.09 до 21.12. Пригласила Олеся → +6 W$ (первая оплата, 20 % от 30 W$).
--   rufa:    10 500 ₽, пакет «Платформа + Клуб» 3 мес (первый поток → до 21.12,
--            как у Софии/Зинаиды). Пригласивший неизвестен — бонус не начисляем.
-- Запуск: python wwc_sql.py --file payments_fedorov_rufa_2026-09-19.sql

BEGIN;

-- Фёдоров ---------------------------------------------------------------
INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919f001', 'whieda', 'fedorov', 300000, 'RUB', 'telegram_manual', '688931415', '-2026091901', '688931415', md5('fedorov:platform_3m:2026-09-19'), 'PRO 3 мес, 3 000 ₽ (владелец, чат 19.09)')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, list_price_minor)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919f002', 'whieda', 'fedorov', 300000, 'RUB', 3, '2026-09-21 21:00:00+00', '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091901', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919f001', 300000)
ON CONFLICT DO NOTHING;

UPDATE partner_subscriptions SET paid_until = '2026-12-21 20:59:59+00', updated_at = now()
WHERE tenant_id = 'whieda' AND ref_code = 'fedorov';

INSERT INTO partner_bonus_ledger
  (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, source_payment_id, idempotency_key, rule_snapshot, description)
VALUES
  ('whieda', 'olesya-vselennaya', 'credit', 600, 'WUSD', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919f002', 'payment:7a2c4e60-1b3d-4f5a-9c8e-20260919f002:referral_credit',
   '{"basis_points": 2000, "base_wusd_minor": 3000, "plan_code": "platform_3m", "payment_kind": "first", "manual": "владелец 19.09.2026"}'::jsonb, 'Referral bonus: first payment')
ON CONFLICT DO NOTHING;

-- Руфия --------------------------------------------------------------------
INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919e001', 'whieda', 'rufa', 1050000, 'RUB', 'telegram_manual', '688931415', '-2026091902', '688931415', md5('rufa:bundle_pro_club_3m:2026-09-19'), 'Пакет PRO + клуб 105 WWC$ = 10 500 ₽ (владелец, чат 19.09)')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, promo_note, list_price_minor)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919e002', 'whieda', 'rufa', 300000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091902', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919e001', NULL, 300000),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919e003', 'whieda', 'rufa', 750000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091902', '688931415', 'club_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919e001', 'пакет PRO + клуб 105 WWC$ (первый поток)', 1200000)
ON CONFLICT DO NOTHING;

INSERT INTO partner_subscriptions (tenant_id, ref_code, paid_until)
VALUES ('whieda', 'rufa', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

INSERT INTO partner_product_access (tenant_id, ref_code, product_code, paid_until)
VALUES ('whieda', 'rufa', 'club_subscription', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code, product_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

COMMIT;

SELECT s.ref_code, s.paid_until::text AS pro_until, pa.paid_until::text AS club_until,
       (SELECT sum(b.amount_minor) FROM partner_bonus_ledger b JOIN referral_profiles p ON p.owner_id = b.actor_id AND p.ref_code = s.ref_code)::text AS bonus_minor
FROM partner_subscriptions s
LEFT JOIN partner_product_access pa ON pa.tenant_id = s.tenant_id AND pa.ref_code = s.ref_code AND pa.product_code = 'club_subscription'
WHERE s.tenant_id = 'whieda' AND s.ref_code IN ('fedorov', 'rufa', 'olesya') ORDER BY 1;
