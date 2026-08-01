-- Telegram bindings for lead actors (replaces workflow hardcode).
-- Safe additive upserts only.
begin;

insert into lead_actors (actor_id, tenant_id, display_name, telegram_username, telegram_chat_id)
values
  ('harold', 'whieda', 'Harold', 'haroldsvetoch', null),
  ('viktor-test', 'whieda', 'Viktor Test', 'khrypko_pro', '1147735602')
on conflict (actor_id) do update
set display_name = excluded.display_name,
    telegram_username = coalesce(excluded.telegram_username, lead_actors.telegram_username),
    telegram_chat_id = coalesce(excluded.telegram_chat_id, lead_actors.telegram_chat_id),
    active = true,
    updated_at = now();

insert into lead_actor_roles (tenant_id, actor_id, role, country_code, region_code)
values
  ('whieda', 'harold', 'referral_owner', '', ''),
  ('whieda', 'viktor-test', 'platform_owner', '', ''),
  ('whieda', 'viktor-test', 'tenant_admin', '', '')
on conflict do nothing;

commit;
