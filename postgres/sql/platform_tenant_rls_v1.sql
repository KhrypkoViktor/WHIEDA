-- WHIEDA Platform Tenant RLS V1 (core helpers + registry tables only)
-- Legacy leads RLS: platform_tenant_rls_legacy_leads_v1.sql (after leads schema).

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

alter table if exists tenant_usage_ledger enable row level security;

drop policy if exists tenant_usage_ledger_tenant_isolation on tenant_usage_ledger;
create policy tenant_usage_ledger_tenant_isolation on tenant_usage_ledger
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

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
