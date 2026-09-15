-- Олеся: ref_code сайта = поддомен = 'olesya'. Runtime знал её как
-- 'olesya-vselennaya' без поддомена — из-за этого заявка с её домена
-- 14.09.2026 ушла другому партнёру.
--
-- Актор (lead_actors, chat id 5497008018) и подписка остаются на
-- 'olesya-vselennaya': переименовывать PK актора не нужно, маршрутизация
-- идёт через owner_id. FK partner_subscriptions.ref_code -> referral_profiles
-- без каскада, поэтому порядок: новая строка -> перевод подписки -> удаление
-- старой. Всё в одной транзакции.
--
-- Запуск:  python wwc_sql.py --file fix_olesya_ref_code_2026-09-14.sql
-- Проверка после: curl https://wwc.best/api/v1/public/ref/olesya  -> 200,
--                 public_site_url = https://olesya.wwc.best/

BEGIN;

INSERT INTO referral_profiles
  (ref_code, tenant_id, owner_id, display_mode, public_profile, country_code, region_code, enabled, profile_version, publication_consent_at)
SELECT 'olesya', tenant_id, owner_id, display_mode,
       public_profile || '{"partner_id":"olesya","page_mode":"subdomain_site","site_type":"subdomain_site","public_site_url":"https://olesya.wwc.best/","display_name":"Олеся Вселенная","leads_access":"partner"}'::jsonb,
       country_code, region_code, enabled, profile_version + 1, publication_consent_at
FROM referral_profiles
WHERE tenant_id = 'whieda' AND ref_code = 'olesya-vselennaya';

UPDATE partner_subscriptions SET ref_code = 'olesya'
WHERE tenant_id = 'whieda' AND ref_code = 'olesya-vselennaya';

DELETE FROM referral_profiles
WHERE tenant_id = 'whieda' AND ref_code = 'olesya-vselennaya';

-- Натали: Core перебивает локальное имя сайта, фамилия должна быть и здесь.
UPDATE referral_profiles
SET public_profile = public_profile || '{"display_name":"Натали Шуляковская"}'::jsonb,
    profile_version = profile_version + 1
WHERE tenant_id = 'whieda' AND ref_code = 'natali';

COMMIT;

SELECT ref_code, owner_id,
       public_profile->>'public_site_url' AS site_url,
       public_profile->>'display_name'    AS display_name
FROM referral_profiles
WHERE tenant_id = 'whieda' AND ref_code IN ('olesya', 'olesya-vselennaya', 'natali');
