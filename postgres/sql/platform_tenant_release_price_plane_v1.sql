-- Additive generic retail prices for tenant release packages.
-- No FX conversion, no partner formula, no runtime catalog writes.

begin;

alter table tenant_release_staging_product
  add column if not exists retail_prices jsonb not null default '[]'::jsonb;

alter table tenant_release_candidate_product
  add column if not exists retail_prices jsonb not null default '[]'::jsonb;

create table if not exists tenant_release_candidate_price (
  candidate_id uuid not null references tenant_release_candidate (candidate_id) on delete cascade,
  tenant_id text not null,
  sku text not null,
  kind text not null check (kind in ('retail', 'partner')),
  amount numeric not null check (amount > 0),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  source text not null,
  source_version text,
  amount_sha256 text not null,
  primary key (candidate_id, sku, kind, currency)
);

alter table tenant_release_candidate_price enable row level security;

drop policy if exists tenant_release_candidate_price_tenant_isolation
  on tenant_release_candidate_price;
create policy tenant_release_candidate_price_tenant_isolation
  on tenant_release_candidate_price
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
