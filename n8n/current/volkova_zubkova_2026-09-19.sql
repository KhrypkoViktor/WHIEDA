-- 19.09.2026 (владелец в чате).
-- 1) Евгения Волкова, volkova.wwc.best — новая, из гугл-формы 17.09 (telegram 1258961551 уже в lead_actors
--    как telegram:whieda:1258961551 — она нажимала бота). Пакет «Платформа + Клуб» 10 500 ₽ → до 21.12.
--    Москва → РФ. Пригласивший неизвестен.
-- 2) Людмила Зубкова (zubkovaludmila, РБ): оплатила 30 W$ — PRO 3 мес «по скидке» (владелец); срок от
--    бесплатного входа 21.09 → 21.12. Реферер в таблице пуст — бонус не начисляем.
-- Запуск: python wwc_sql.py --file volkova_zubkova_2026-09-19.sql

BEGIN;

UPDATE lead_actors SET display_name = 'Евгения Волкова', telegram_username = coalesce(nullif(telegram_username, ''), 'Zhenechka71'), updated_at = now()
WHERE tenant_id = 'whieda' AND actor_id = 'telegram:whieda:1258961551';

INSERT INTO referral_profiles (ref_code, tenant_id, owner_id, display_mode, country_code, enabled, profile_version, public_profile)
VALUES ('volkova', 'whieda', 'telegram:whieda:1258961551', 'named', 'RU', true, 1,
  '{"page_mode": "subdomain_site", "plan_code": "partner_subscription", "site_type": "subdomain_site",
    "partner_id": "volkova", "access_tier": "test_pilot", "focus_group": false, "plan_status": "active",
    "display_name": "Евгения Волкова", "leads_access": "partner", "owner_actor_id": "telegram:whieda:1258961551",
    "public_site_url": "https://volkova.wwc.best/", "watcher_actor_id": "", "referrer_ref_code": ""}'::jsonb)
ON CONFLICT (ref_code) DO NOTHING;

INSERT INTO referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
VALUES ('whieda', 'BID3diqdZ72dwUt6', 'telegram:whieda:1258961551')
ON CONFLICT DO NOTHING;

INSERT INTO partner_subscriptions (tenant_id, ref_code, paid_until)
VALUES ('whieda', 'volkova', '2026-12-21 20:59:59+00'), ('whieda', 'zubkovaludmila', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

INSERT INTO partner_payments
  (received_payment_id, tenant_id, ref_code, received_amount_minor, currency, source, telegram_chat_id, telegram_message_id, telegram_user_id, lines_fingerprint, note)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919c001', 'whieda', 'volkova', 1050000, 'RUB', 'telegram_manual', '688931415', '-2026091904', '688931415', md5('volkova:bundle_pro_club_3m:2026-09-19'), 'Пакет PRO + клуб 105 WWC$ = 10 500 ₽ (владелец, чат 19.09)'),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919d001', 'whieda', 'zubkovaludmila', 3000, 'WUSD', 'telegram_manual', '688931415', '-2026091905', '688931415', md5('zubkovaludmila:platform_3m:2026-09-19'), 'PRO 3 мес, 30 WWC$ по скидке (владелец, чат 19.09)')
ON CONFLICT DO NOTHING;

INSERT INTO partner_payment_ledger
  (payment_id, tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, received_payment_id, promo_note, list_price_minor)
VALUES
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919c002', 'whieda', 'volkova', 300000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091904', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919c001', NULL, 300000),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919c003', 'whieda', 'volkova', 750000, 'RUB', 3, now(), '2026-12-21 20:59:59+00', NULL, 'telegram_manual', '688931415', '-2026091904', '688931415', 'club_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919c001', 'пакет PRO + клуб 105 WWC$ (первый поток)', 1200000),
  ('7a2c4e60-1b3d-4f5a-9c8e-20260919d002', 'whieda', 'zubkovaludmila', 3000, 'WUSD', 3, '2026-09-21 21:00:00+00', '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091905', '688931415', 'platform_subscription', '7a2c4e60-1b3d-4f5a-9c8e-20260919d001', 'по скидке (владелец, 19.09)', 3000)
ON CONFLICT DO NOTHING;

INSERT INTO partner_product_access (tenant_id, ref_code, product_code, paid_until)
VALUES ('whieda', 'volkova', 'club_subscription', '2026-12-21 20:59:59+00')
ON CONFLICT (tenant_id, ref_code, product_code) DO UPDATE SET paid_until = EXCLUDED.paid_until, updated_at = now();

COMMIT;

SELECT s.ref_code, s.paid_until::text AS pro_until, pa.paid_until::text AS club_until
FROM partner_subscriptions s
LEFT JOIN partner_product_access pa ON pa.tenant_id = s.tenant_id AND pa.ref_code = s.ref_code AND pa.product_code = 'club_subscription'
WHERE s.tenant_id = 'whieda' AND s.ref_code IN ('volkova', 'zubkovaludmila') ORDER BY 1;
