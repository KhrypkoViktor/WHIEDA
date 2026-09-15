-- Local Telegram canary overlay only. Not an APPLY_ORDER migration. No WHIEDA/NSP catalog.
begin;

alter table advisor_structured_products
  add column if not exists retail_prices jsonb;

insert into tenants (tenant_id, display_name, status, default_locale, default_country)
values
  ('tenant-north', 'North Lab', 'active', 'ru', 'US'),
  ('tenant-south', 'South Lab', 'active', 'ru', 'US')
on conflict (tenant_id) do update
set display_name = excluded.display_name,
    status = excluded.status,
    default_country = excluded.default_country,
    updated_at = now();

insert into tenant_entitlements (tenant_id, feature_key, enabled)
values
  ('tenant-north', 'structure_basic', true),
  ('tenant-south', 'structure_basic', true)
on conflict (tenant_id, feature_key) do update
set enabled = excluded.enabled;

insert into tenant_advisor_profile (tenant_id, advisor_signature, empty_catalog_guidance)
values
  (
    'tenant-north',
    'советник',
    'Каталог North Lab пуст. Товары другого проекта не подставляются. N-EMPTY'
  ),
  (
    'tenant-south',
    'советник',
    'Каталог South Lab пуст. Товары другого проекта не подставляются. S-EMPTY'
  )
on conflict (tenant_id) do update
set advisor_signature = excluded.advisor_signature,
    empty_catalog_guidance = excluded.empty_catalog_guidance,
    updated_at = now();

insert into tenant_bot_bindings (
  binding_id, tenant_id, webhook_secret_ref, status,
  bot_token_ref, bot_username, processing_mode
)
values
  (
    'north-canary-bot', 'tenant-north', 'env:NORTH_CANARY_WEBHOOK_SECRET', 'active',
    'env:NORTH_CANARY_BOT_TOKEN', 'north_canary_bot', 'core'
  ),
  (
    'south-canary-bot', 'tenant-south', 'env:SOUTH_CANARY_WEBHOOK_SECRET', 'active',
    'env:SOUTH_CANARY_BOT_TOKEN', 'south_canary_bot', 'core'
  ),
  (
    'north-disabled-bot', 'tenant-north', 'env:NORTH_CANARY_WEBHOOK_SECRET', 'disabled',
    'env:NORTH_CANARY_BOT_TOKEN', 'north_disabled_bot', 'core'
  )
on conflict (binding_id) do update
set tenant_id = excluded.tenant_id,
    webhook_secret_ref = excluded.webhook_secret_ref,
    status = excluded.status,
    bot_token_ref = excluded.bot_token_ref,
    bot_username = excluded.bot_username,
    processing_mode = excluded.processing_mode,
    updated_at = now();

insert into advisor_structured_products (
  client_id, sku, canonical_name, retail_price_rub, retail_price_byn, retail_prices
) values
  (
    'tenant-north', 'SHARE-01', 'North shared tonic', null, null,
    '[{"kind":"retail","amount":"21.00","currency":"USD"}]'::jsonb
  ),
  (
    'tenant-south', 'SHARE-01', 'South shared tonic', null, null,
    '[{"kind":"retail","amount":"34.00","currency":"USD"}]'::jsonb
  ),
  (
    'tenant-north', 'NORTH-ONLY', 'North exclusive blend', null, null,
    '[{"kind":"retail","amount":"55.00","currency":"USD"}]'::jsonb
  )
on conflict (client_id, sku) do update set
  canonical_name = excluded.canonical_name,
  retail_prices = excluded.retail_prices;

insert into advisor_structured_aliases (
  client_id, alias, canonical_sku, canonical_name, priority, active
) values
  ('tenant-north', 'общий тоник', 'SHARE-01', 'North shared tonic', 100, true),
  ('tenant-south', 'общий тоник', 'SHARE-01', 'South shared tonic', 100, true),
  ('tenant-north', 'северная смесь', 'NORTH-ONLY', 'North exclusive blend', 100, true)
on conflict (client_id, alias) do update set
  canonical_sku = excluded.canonical_sku,
  canonical_name = excluded.canonical_name,
  priority = excluded.priority,
  active = true;

insert into advisor_structured_product_cards (
  client_id, sku, canonical_name, what_it_is, who_asks_about_it, common_use_cases,
  how_to_use_short, primary_image_url
) values
  (
    'tenant-north', 'SHARE-01', 'North shared tonic',
    'North tonic card N-CARD. Retail 21.00 USD', 'North lab', 'Daily tonic.', 'As directed.',
    'media/tenant-north/SHARE-01/north.webp'
  ),
  (
    'tenant-south', 'SHARE-01', 'South shared tonic',
    'South tonic card S-CARD. Retail 34.00 USD', 'South lab', 'Daily tonic.', 'As directed.',
    'media/tenant-south/SHARE-01/south.webp'
  ),
  (
    'tenant-north', 'NORTH-ONLY', 'North exclusive blend',
    'North exclusive card NX-CARD', 'North lab only', 'Exclusive blend.', 'As directed.',
    'media/tenant-north/NORTH-ONLY/exclusive.webp'
  )
on conflict (client_id, sku) do update set
  canonical_name = excluded.canonical_name,
  what_it_is = excluded.what_it_is,
  who_asks_about_it = excluded.who_asks_about_it,
  common_use_cases = excluded.common_use_cases,
  how_to_use_short = excluded.how_to_use_short,
  primary_image_url = excluded.primary_image_url;

insert into advisor_structured_resources (
  client_id, resource_id, sku, canonical_name, resource_type, title, url, priority, active
) values
  (
    'tenant-north', 'north-share-photo', 'SHARE-01', 'North shared tonic',
    'image', 'North photo', 'media/tenant-north/SHARE-01/north.webp', 100, true
  ),
  (
    'tenant-south', 'south-share-photo', 'SHARE-01', 'South shared tonic',
    'image', 'South photo', 'media/tenant-south/SHARE-01/south.webp', 100, true
  ),
  (
    'tenant-north', 'north-only-photo', 'NORTH-ONLY', 'North exclusive blend',
    'image', 'North exclusive photo', 'media/tenant-north/NORTH-ONLY/exclusive.webp', 100, true
  )
on conflict (client_id, resource_id) do update set
  sku = excluded.sku,
  url = excluded.url,
  resource_type = excluded.resource_type,
  active = true;

insert into advisor_structured_business_faq (
  client_id, faq_id, title, answer_text, aliases, priority, active
) values
  (
    'tenant-north', 'north-warehouse-rule', 'правило склада',
    'North FAQ N-FAQ: склад только северный.',
    'правило склада,доставка склада north', 100, true
  ),
  (
    'tenant-south', 'south-warehouse-rule', 'правило склада',
    'South FAQ S-FAQ: склад только южный.',
    'правило склада,доставка склада south', 100, true
  )
on conflict (client_id, faq_id) do update set
  title = excluded.title,
  answer_text = excluded.answer_text,
  aliases = excluded.aliases,
  active = true;

commit;
