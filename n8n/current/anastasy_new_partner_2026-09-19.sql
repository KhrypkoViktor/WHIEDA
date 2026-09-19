-- Анастасия Сошникова, anastasy.wwc.best (владелец, 19.09.2026): косметолог-эстетист, +7 978 → РФ.
-- Оплата не названа — подписка NULL; продажу владелец проводит в боте: «оплата ref:anastasy …».
-- Анастасия Сошникова, anastasy.wwc.best (владелец, 19.09.2026): новый партнёр, взяла пакет
-- «Платформа + Клуб» 105 W$ = 10 500 ₽ на 3 месяца. Москва → рынок РФ.
-- Записано по образцу kira/zinaida (core-agent, 14.09): actor с @username — бот
-- сольёт его с реальным Telegram-пользователем при её первом /start (fix 55cf71d).
-- Оплату после этого владелец проводит в боте (профиль full):
--   оплата ref:rufa
--   пакет 10500 RUB
--   получено 10500 RUB
-- Пригласивший неизвестен → partner_referral_attributions не пишем; когда владелец
-- скажет, кто привёл, добавить строку (invitee 'anastasy', inviter <owner_id>, 'admin_manual').
--
-- Запуск: python wwc_sql.py --file anastasy_new_partner_2026-09-19.sql

BEGIN;

INSERT INTO lead_actors (actor_id, tenant_id, display_name, telegram_username, active)
VALUES ('anastasy', 'whieda', 'Анастасия Сошникова', 'nastya_plazma', true)
ON CONFLICT (actor_id) DO NOTHING;

INSERT INTO referral_profiles (ref_code, tenant_id, owner_id, display_mode, country_code, enabled, profile_version, public_profile)
VALUES ('anastasy', 'whieda', 'anastasy', 'named', 'RU', true, 1,
  '{"page_mode": "subdomain_site", "plan_code": "partner_subscription", "site_type": "subdomain_site",
    "partner_id": "rufa", "access_tier": "test_pilot", "focus_group": false, "plan_status": "active",
    "display_name": "Анастасия Сошникова", "leads_access": "partner", "owner_actor_id": "rufa",
    "public_site_url": "https://anastasy.wwc.best/", "watcher_actor_id": "", "referrer_ref_code": ""}'::jsonb)
ON CONFLICT (ref_code) DO NOTHING;

INSERT INTO referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
VALUES ('whieda', '5agPY8K2nJVETgrj', 'anastasy')
ON CONFLICT DO NOTHING;

-- Строка подписки без срока: бот проставит paid_until при оплате пакета.
INSERT INTO partner_subscriptions (tenant_id, ref_code, paid_until)
VALUES ('whieda', 'anastasy', NULL)
ON CONFLICT (tenant_id, ref_code) DO NOTHING;

COMMIT;

SELECT p.ref_code, p.owner_id, p.country_code, c.invite_code, s.paid_until::text
FROM referral_profiles p
LEFT JOIN referral_invite_codes c ON c.tenant_id = p.tenant_id AND c.inviter_actor_id = p.owner_id
LEFT JOIN partner_subscriptions s ON s.tenant_id = p.tenant_id AND s.ref_code = p.ref_code
WHERE p.tenant_id = 'whieda' AND p.ref_code = 'anastasy';
