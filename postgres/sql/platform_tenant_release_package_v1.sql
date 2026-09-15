-- Additive tenant release-package staging. Never writes advisor runtime catalog.

begin;

create table if not exists tenant_release_run (
  run_id uuid primary key default gen_random_uuid(),
  package_id text not null,
  package_version text not null,
  tenant_id text not null,
  package_sha256 text not null,
  release_status text not null,
  reused boolean not null default false,
  created_at timestamptz not null default now(),
  unique (package_id, package_sha256)
);

create table if not exists tenant_release_staging_product (
  run_id uuid not null references tenant_release_run (run_id) on delete cascade,
  tenant_id text not null,
  sku text not null,
  canonical_name text,
  review_status text not null,
  retail_price_byn numeric,
  partner_price_byn numeric,
  partner_w numeric,
  price_missing boolean not null default false,
  media_state text,
  payload jsonb not null default '{}'::jsonb,
  primary key (run_id, sku)
);

create table if not exists tenant_release_candidate (
  candidate_id uuid primary key default gen_random_uuid(),
  run_id uuid not null references tenant_release_run (run_id),
  package_id text not null,
  package_version text not null,
  tenant_id text not null,
  package_sha256 text not null,
  status text not null default 'current'
    check (status in ('current', 'superseded')),
  created_at timestamptz not null default now()
);

create unique index if not exists tenant_release_candidate_one_current
  on tenant_release_candidate (tenant_id, package_id)
  where status = 'current';

create table if not exists tenant_release_candidate_product (
  candidate_id uuid not null references tenant_release_candidate (candidate_id) on delete cascade,
  tenant_id text not null,
  sku text not null,
  canonical_name text not null,
  review_status text not null default 'approved',
  retail_price_byn numeric,
  partner_price_byn numeric,
  partner_w numeric,
  price_missing boolean not null default false,
  media_state text not null,
  card_present boolean not null default true,
  source jsonb not null default '{}'::jsonb,
  primary key (candidate_id, sku)
);

alter table tenant_release_run enable row level security;
alter table tenant_release_staging_product enable row level security;
alter table tenant_release_candidate enable row level security;
alter table tenant_release_candidate_product enable row level security;

drop policy if exists tenant_release_run_tenant_isolation on tenant_release_run;
create policy tenant_release_run_tenant_isolation on tenant_release_run
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists tenant_release_staging_product_tenant_isolation on tenant_release_staging_product;
create policy tenant_release_staging_product_tenant_isolation on tenant_release_staging_product
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists tenant_release_candidate_tenant_isolation on tenant_release_candidate;
create policy tenant_release_candidate_tenant_isolation on tenant_release_candidate
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists tenant_release_candidate_product_tenant_isolation on tenant_release_candidate_product;
create policy tenant_release_candidate_product_tenant_isolation on tenant_release_candidate_product
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
