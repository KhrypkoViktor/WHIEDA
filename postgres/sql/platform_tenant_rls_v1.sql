-- WHIEDA Platform Tenant RLS V1
-- Defense-in-depth: API must SET LOCAL app.tenant_id before tenant-scoped queries.
-- Apply after platform_tenant_registry_v1.sql on staging first.

begin;

create or replace function platform_current_tenant_id()
returns text
language sql
stable
as $$
  select nullif(current_setting('app.tenant_id', true), '');
$$;

create or replace function platform_set_tenant_context(p_tenant_id text)
returns void
language plpgsql
as $$
begin
  if p_tenant_id is null or btrim(p_tenant_id) = '' then
    raise exception 'tenant_id required';
  end if;
  perform set_config('app.tenant_id', p_tenant_id, true);
end;
$$;

-- Tenant registry tables: no RLS (resolved before business queries).

alter table if exists website_leads enable row level security;
alter table if exists referral_profiles enable row level security;
alter table if exists lead_actors enable row level security;
alter table if exists lead_actor_roles enable row level security;
alter table if exists lead_delivery_attempts enable row level security;
alter table if exists website_lead_status_history enable row level security;
alter table if exists tenant_usage_ledger enable row level security;

drop policy if exists website_leads_tenant_isolation on website_leads;
create policy website_leads_tenant_isolation on website_leads
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists referral_profiles_tenant_isolation on referral_profiles;
create policy referral_profiles_tenant_isolation on referral_profiles
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_actors_tenant_isolation on lead_actors;
create policy lead_actors_tenant_isolation on lead_actors
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_actor_roles_tenant_isolation on lead_actor_roles;
create policy lead_actor_roles_tenant_isolation on lead_actor_roles
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists lead_delivery_attempts_tenant_isolation on lead_delivery_attempts;
create policy lead_delivery_attempts_tenant_isolation on lead_delivery_attempts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists website_lead_status_history_tenant_isolation on website_lead_status_history;
create policy website_lead_status_history_tenant_isolation on website_lead_status_history
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists tenant_usage_ledger_tenant_isolation on tenant_usage_ledger;
create policy tenant_usage_ledger_tenant_isolation on tenant_usage_ledger
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Composite indexes for hot tenant-scoped paths (idempotent).
create index if not exists idx_referral_profiles_tenant_ref
  on referral_profiles (tenant_id, ref_code)
  where enabled = true;

create index if not exists idx_website_leads_tenant_idempotency
  on website_leads (tenant_id, idempotency_key);

-- Test tenant seed (staging only; no production secrets).
insert into tenants (tenant_id, display_name, status, default_locale, default_country)
values ('test-acme', 'Test Acme Network', 'active', 'ru', 'RU')
on conflict (tenant_id) do update
set display_name = excluded.display_name,
    status = excluded.status,
    updated_at = now();

insert into tenant_domains (domain, tenant_id, kind, is_active, verified_at)
values ('acme.test.local', 'test-acme', 'primary', true, now())
on conflict (domain) do update
set tenant_id = excluded.tenant_id,
    is_active = excluded.is_active;

insert into tenant_entitlements (tenant_id, feature_key, enabled)
values
  ('test-acme', 'structure_basic', true),
  ('test-acme', 'partner_leads', true),
  ('test-acme', 'deep_coach', false)
on conflict (tenant_id, feature_key) do update
set enabled = excluded.enabled;

insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status)
values ('test-acme-bot-binding', 'test-acme', 'env:TEST_ACME_TELEGRAM_SECRET', 'active')
on conflict (binding_id) do update
set tenant_id = excluded.tenant_id,
    status = excluded.status;

commit;
