-- Additive tenant advisor display/config for Core data-plane isolation.
-- No second catalog, no NSP seed, no WHIEDA/NSP data copy.

begin;

create table if not exists tenant_advisor_profile (
  tenant_id text primary key references tenants (tenant_id),
  advisor_signature text not null default 'советник',
  empty_catalog_guidance text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table tenant_advisor_profile enable row level security;

drop policy if exists tenant_advisor_profile_tenant_isolation on tenant_advisor_profile;
create policy tenant_advisor_profile_tenant_isolation on tenant_advisor_profile
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

insert into tenant_advisor_profile (tenant_id, advisor_signature, empty_catalog_guidance)
values (
  'whieda',
  'советник WHIEDA',
  'Каталог пока пуст. Я советник WHIEDA: откройте меню или назовите товар.'
)
on conflict (tenant_id) do nothing;

insert into tenant_advisor_profile (tenant_id, advisor_signature, empty_catalog_guidance)
select
  'test-acme',
  'советник',
  'Каталог пока пуст. Товары другого проекта не подставляются.'
where exists (select 1 from tenants where tenant_id = 'test-acme')
on conflict (tenant_id) do nothing;

commit;
