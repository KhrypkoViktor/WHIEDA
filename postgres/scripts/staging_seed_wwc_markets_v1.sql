-- Staging-only fixtures for WWC markets API (NOT for production data).
-- Two structures (alpha/beta) + wwc-default; Minsk/Moscow; explicit coverage sample.

begin;

insert into wwc_markets_sync_registry (tenant_id, status, first_manual_sync_at, scheduler_enabled, synced_at)
values ('whieda', 'ok', now(), false, now())
on conflict (tenant_id) do update set
  status = excluded.status,
  first_manual_sync_at = coalesce(wwc_markets_sync_registry.first_manual_sync_at, excluded.first_manual_sync_at),
  synced_at = excluded.synced_at;

insert into wwc_markets (tenant_id, market_id, country_iso, country_name, currency_code, price_visibility, is_active, is_default)
values
  ('whieda', 'ru', 'RU', 'Россия', 'RUB', 'full', true, false),
  ('whieda', 'by', 'BY', 'Беларусь', 'BYN', 'full', true, false),
  ('whieda', 'global', '*', 'Другая страна', 'RUB', 'full', true, true)
on conflict (tenant_id, market_id) do nothing;

insert into wwc_ref_structures (tenant_id, ref_code, structure_id, is_active)
values
  ('whieda', 'fixture-ref-alpha', 'structure-alpha', true),
  ('whieda', 'fixture-ref-beta', 'structure-beta', true),
  ('whieda', 'harold', 'structure-alpha', true),
  ('whieda', 'orphan-structure-ref', 'structure-orphan', true)
on conflict (tenant_id, ref_code) do nothing;

insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled, public_profile)
values
  ('fixture-ref-alpha', 'whieda', 'ladnaya', 'named', true, '{"display_name":"Alpha Staging"}'::jsonb),
  ('fixture-ref-beta', 'whieda', 'mariam', 'named', true, '{"display_name":"Beta Staging"}'::jsonb)
on conflict (ref_code) do update set
  tenant_id = excluded.tenant_id,
  enabled = excluded.enabled;

insert into wwc_service_centers (
  tenant_id, center_id, structure_id, country_iso, city, region, title, manager_name,
  photo_url, telegram, phone, address, working_hours, map_url_yandex, map_url_google, notes, is_active, priority
) values
  (
    'whieda', 'minsk-alpha-01', 'structure-alpha', 'BY', 'Минск', 'Минск / Минская область',
    'Сервисный центр WWC — Минск (Alpha)', 'Менеджер Alpha', null, 'alpha_minsk', '+375290000001',
    'Staging Address Alpha, Minsk', 'Пн–Сб, 10:00–19:00',
    'https://yandex.ru/maps/-/staging-alpha-minsk', null, 'Staging center alpha', true, 100
  ),
  (
    'whieda', 'minsk-beta-01', 'structure-beta', 'BY', 'Минск', 'Минск / Минская область',
    'Сервисный центр WWC — Минск (Beta)', 'Менеджер Beta', null, 'beta_minsk', '+375290000002',
    'Staging Address Beta, Minsk', 'Пн–Пт, 11:00–18:00',
    'https://yandex.ru/maps/-/staging-beta-minsk', null, 'Staging center beta', true, 100
  ),
  (
    'whieda', 'moscow-alpha-01', 'structure-alpha', 'RU', 'Москва', 'Москва',
    'Сервисный центр WWC — Москва (Alpha)', 'Менеджер Moscow', null, 'alpha_msk', '+74950000001',
    'Staging Address Alpha, Moscow', 'Пн–Сб, 10:00–20:00',
    'https://yandex.ru/maps/-/staging-alpha-moscow', null, null, true, 100
  )
on conflict (tenant_id, center_id) do nothing;

insert into wwc_service_center_coverage (tenant_id, structure_id, country_iso, city_alias, center_id, is_active, priority)
values
  ('whieda', 'structure-alpha', 'RU', 'новосибирск', 'moscow-alpha-01', true, 50)
on conflict (tenant_id, structure_id, country_iso, city_alias) do nothing;

insert into wwc_product_prices (tenant_id, sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at)
values
  ('whieda', 'M015-00', 'ru', 'RUB', 50000, '50 000 ₽', 'active', true, now()),
  ('whieda', 'M015-00', 'by', 'BYN', 1750, '1 750 BYN', 'active', true, now()),
  ('whieda', 'D014', 'ru', 'RUB', 5500, '5 500 ₽', 'active', true, now()),
  ('whieda', 'D014', 'by', 'BYN', 192.5, '192,5 BYN', 'active', true, now())
on conflict (tenant_id, sku, market_id) do nothing;

commit;
