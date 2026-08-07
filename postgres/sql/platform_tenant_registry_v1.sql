-- WHIEDA Platform Tenant Registry V1
-- Status: architecture DDL for developer handoff (apply additively on staging first)
-- Spec: WHIEDA_PLATFORM_TENANT_CONTRACTS_V1_2026-08-02.md

begin;

create table if not exists tenants (
  tenant_id text primary key,
  tenant_uuid uuid not null default gen_random_uuid(),
  display_name text not null,
  status text not null default 'draft'
    check (status in ('draft', 'active', 'suspended', 'archived')),
  default_locale text not null default 'ru',
  default_country text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists idx_tenants_uuid
  on tenants (tenant_uuid);

create table if not exists tenant_domains (
  domain text primary key,
  tenant_id text not null references tenants (tenant_id),
  kind text not null default 'primary'
    check (kind in ('primary', 'partner_subdomain', 'custom')),
  is_active boolean not null default true,
  verified_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_tenant_domains_tenant
  on tenant_domains (tenant_id, is_active);

create table if not exists tenant_bot_bindings (
  binding_id text primary key,
  tenant_id text not null references tenants (tenant_id),
  telegram_bot_id bigint,
  webhook_secret_ref text not null,
  status text not null default 'active'
    check (status in ('active', 'disabled', 'rotated')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_tenant_bot_bindings_tenant
  on tenant_bot_bindings (tenant_id, status);

create table if not exists tenant_entitlements (
  tenant_id text not null references tenants (tenant_id),
  feature_key text not null,
  enabled boolean not null default false,
  limits_json jsonb not null default '{}'::jsonb,
  effective_from timestamptz not null default now(),
  primary key (tenant_id, feature_key)
);

create table if not exists tenant_provider_bindings (
  id bigserial primary key,
  tenant_id text not null references tenants (tenant_id),
  provider text not null,
  binding_ref text not null,
  config_json jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (tenant_id, provider, binding_ref)
);

create index if not exists idx_tenant_provider_bindings_lookup
  on tenant_provider_bindings (tenant_id, provider, is_active);

create table if not exists tenant_usage_ledger (
  id bigserial primary key,
  tenant_id text not null references tenants (tenant_id),
  metric_key text not null,
  quantity numeric not null default 0,
  unit_cost_micros bigint,
  source_request_id text,
  recorded_at timestamptz not null default now()
);

create index if not exists idx_tenant_usage_ledger_tenant_time
  on tenant_usage_ledger (tenant_id, recorded_at desc);

-- Seed WHIEDA tenant (idempotent)
insert into tenants (tenant_id, display_name, status, default_locale, default_country)
values ('whieda', 'WHIEDA / WWC', 'active', 'ru', 'BY')
on conflict (tenant_id) do update
set display_name = excluded.display_name,
    status = excluded.status,
    updated_at = now();

insert into tenant_domains (domain, tenant_id, kind, is_active, verified_at)
values
  ('wwc.best', 'whieda', 'primary', true, now()),
  ('samtsova.wwc.best', 'whieda', 'partner_subdomain', true, now())
on conflict (domain) do update
set tenant_id = excluded.tenant_id,
    kind = excluded.kind,
    is_active = excluded.is_active;

insert into tenant_entitlements (tenant_id, feature_key, enabled)
values
  ('whieda', 'structure_basic', true),
  ('whieda', 'partner_leads', true),
  ('whieda', 'deep_coach', false),
  ('whieda', 'broadcast', true)
on conflict (tenant_id, feature_key) do update
set enabled = excluded.enabled;

commit;
