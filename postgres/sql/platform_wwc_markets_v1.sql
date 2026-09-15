-- WWC markets, prices, service centers — Platform API runtime (V1)
-- Apply after platform_whieda_telegram_binding_v1.sql on staging first.

begin;

create table if not exists wwc_markets_sync_registry (
  tenant_id text primary key,
  source_updated_at timestamptz,
  synced_at timestamptz,
  status text not null default 'idle'
    check (status in ('idle', 'syncing', 'ok', 'error')),
  last_error text,
  first_manual_sync_at timestamptz,
  scheduler_enabled boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists wwc_markets (
  tenant_id text not null,
  market_id text not null check (market_id in ('ru', 'by', 'global')),
  country_iso text not null,
  country_name text not null,
  currency_code text not null,
  price_visibility text not null default 'full'
    check (price_visibility in ('full', 'consultation')),
  is_active boolean not null default true,
  is_default boolean not null default false,
  primary key (tenant_id, market_id)
);

create table if not exists wwc_ref_structures (
  tenant_id text not null,
  ref_code text not null,
  structure_id text not null,
  is_active boolean not null default true,
  primary key (tenant_id, ref_code)
);

create index if not exists idx_wwc_ref_structures_structure
  on wwc_ref_structures (tenant_id, structure_id)
  where is_active = true;

create table if not exists wwc_service_centers (
  tenant_id text not null,
  center_id text not null,
  structure_id text not null,
  country_iso text not null,
  city text not null,
  region text not null default '',
  title text not null,
  manager_name text not null,
  photo_url text,
  telegram text,
  phone text,
  address text not null,
  working_hours text,
  map_url_yandex text,
  map_url_google text,
  notes text,
  is_active boolean not null default true,
  priority integer not null default 100,
  primary key (tenant_id, center_id)
);

create index if not exists idx_wwc_service_centers_lookup
  on wwc_service_centers (tenant_id, structure_id, country_iso, city)
  where is_active = true;

create table if not exists wwc_service_center_coverage (
  tenant_id text not null,
  structure_id text not null,
  country_iso text not null,
  city_alias text not null,
  center_id text not null,
  is_active boolean not null default true,
  priority integer not null default 100,
  primary key (tenant_id, structure_id, country_iso, city_alias)
);

create index if not exists idx_wwc_coverage_lookup
  on wwc_service_center_coverage (tenant_id, structure_id, country_iso, city_alias)
  where is_active = true;

create table if not exists wwc_product_prices (
  tenant_id text not null,
  sku text not null,
  market_id text not null check (market_id in ('ru', 'by', 'global')),
  currency_code text not null,
  amount numeric(14, 2),
  formatted text,
  price_state text not null default 'active'
    check (price_state in ('active', 'consultation', 'unavailable')),
  is_active boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, sku, market_id)
);

create unique index if not exists idx_wwc_product_prices_active_sku_market
  on wwc_product_prices (tenant_id, sku, market_id)
  where is_active = true;

-- Staging tables (atomic publish from sync)
create table if not exists wwc_markets_staging (like wwc_markets including all);
create table if not exists wwc_ref_structures_staging (like wwc_ref_structures including all);
create table if not exists wwc_service_centers_staging (like wwc_service_centers including all);
create table if not exists wwc_service_center_coverage_staging (like wwc_service_center_coverage including all);
create table if not exists wwc_product_prices_staging (like wwc_product_prices including all);

-- Tenant RLS: runtime + staging tables (requires platform_tenant_rls_v1.sql)
alter table wwc_markets enable row level security;
alter table wwc_ref_structures enable row level security;
alter table wwc_service_centers enable row level security;
alter table wwc_service_center_coverage enable row level security;
alter table wwc_product_prices enable row level security;
alter table wwc_markets_sync_registry enable row level security;
alter table wwc_markets_staging enable row level security;
alter table wwc_ref_structures_staging enable row level security;
alter table wwc_service_centers_staging enable row level security;
alter table wwc_service_center_coverage_staging enable row level security;
alter table wwc_product_prices_staging enable row level security;

drop policy if exists wwc_markets_tenant_isolation on wwc_markets;
create policy wwc_markets_tenant_isolation on wwc_markets
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_ref_structures_tenant_isolation on wwc_ref_structures;
create policy wwc_ref_structures_tenant_isolation on wwc_ref_structures
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_service_centers_tenant_isolation on wwc_service_centers;
create policy wwc_service_centers_tenant_isolation on wwc_service_centers
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_service_center_coverage_tenant_isolation on wwc_service_center_coverage;
create policy wwc_service_center_coverage_tenant_isolation on wwc_service_center_coverage
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_product_prices_tenant_isolation on wwc_product_prices;
create policy wwc_product_prices_tenant_isolation on wwc_product_prices
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_markets_sync_registry_tenant_isolation on wwc_markets_sync_registry;
create policy wwc_markets_sync_registry_tenant_isolation on wwc_markets_sync_registry
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_markets_staging_tenant_isolation on wwc_markets_staging;
create policy wwc_markets_staging_tenant_isolation on wwc_markets_staging
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_ref_structures_staging_tenant_isolation on wwc_ref_structures_staging;
create policy wwc_ref_structures_staging_tenant_isolation on wwc_ref_structures_staging
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_service_centers_staging_tenant_isolation on wwc_service_centers_staging;
create policy wwc_service_centers_staging_tenant_isolation on wwc_service_centers_staging
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_service_center_coverage_staging_tenant_isolation on wwc_service_center_coverage_staging;
create policy wwc_service_center_coverage_staging_tenant_isolation on wwc_service_center_coverage_staging
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists wwc_product_prices_staging_tenant_isolation on wwc_product_prices_staging;
create policy wwc_product_prices_staging_tenant_isolation on wwc_product_prices_staging
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
