-- 19.09.2026. Анастасия Сошникова (anastasy): 10 500 ₽ — пакет «Платформа + Клуб» 3 мес,
-- до 21.12 (первый поток, как у Руфии). Пригласивший неизвестен — бонус не начисляем.
-- Запуск: python wwc_sql.py --file payments_anastasy_2026-09-19.sql

BEGIN;

INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919a001', 'whieda', 'anastasy', 1050000, 'RUB', 'telegram_manual', '688931415', '-2026091903', '688931415', md5('anastasy:bundle_pro_club_3m:2026-09-19'), 'Пакет PRO + клуб 105 WWC$ = 10 500 ₽ (владелец, чат 19.09)')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, promo_note, list_price_minor)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919a002', 'whieda', 'anastasy', 300000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091903', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919a001', NULL, 300000),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919a003', 'whieda', 'anastasy', 750000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091903', '688931415', 'club_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919a001', 'пакет PRO + клуб 105 WWC$ (первый поток)', 1200000)
ON CONFLICT DO NOTHING;

INSERT INTO partner_subscriptions (tenant_id, ref_code, paid_until)
VALUES ('whieda', 'anastasy', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

INSERT INTO partner_product_access (tenant_id, ref_code, product_code, paid_until)
VALUES ('whieda', 'anastasy', 'club_subscription', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code, product_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

COMMIT;

SELECT s.ref_code, s.paid_until::text AS pro_until, pa.paid_until::text AS club_until
FROM partner_subscriptions s
LEFT JOIN partner_product_access pa ON pa.tenant_id = s.tenant_id AND pa.ref_code = s.ref_code AND pa.product_code = 'club_subscription'
WHERE s.tenant_id = 'whieda' AND s.ref_code = 'anastasy';
