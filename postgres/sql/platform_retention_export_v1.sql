-- Retention hooks: export/delete metadata only — NO auto-deletion cron
begin;

create table if not exists data_retention_registry (
  registry_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  data_class text not null check (data_class in (
    'profile', 'operational', 'raw_interaction', 'aggregate', 'audit'
  )),
  table_name text not null,
  retention_days int,
  export_enabled boolean not null default true,
  delete_enabled boolean not null default false,
  notes text,
  updated_at timestamptz not null default now(),
  unique (tenant_id, table_name)
);

create table if not exists data_export_requests (
  export_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  requester_scope text not null,
  data_class text not null,
  status text not null default 'pending' check (status in ('pending', 'ready', 'failed')),
  idempotency_key text not null,
  row_count int,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (tenant_id, idempotency_key)
);

alter table if exists data_retention_registry enable row level security;
alter table if exists data_export_requests enable row level security;

drop policy if exists data_retention_registry_tenant on data_retention_registry;
create policy data_retention_registry_tenant on data_retention_registry
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists data_export_requests_tenant on data_export_requests;
create policy data_export_requests_tenant on data_export_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

insert into data_retention_registry (tenant_id, data_class, table_name, retention_days, delete_enabled, notes)
values
  ('whieda', 'operational', 'visitor_sessions', 365, false, 'pilot: export only'),
  ('whieda', 'operational', 'interaction_events', 180, false, 'pilot: export only'),
  ('whieda', 'profile', 'user_memory_facts', 730, false, 'confirmed facts only'),
  ('whieda', 'aggregate', 'pilot_daily_metrics', 1095, false, 'leader analytics'),
  ('whieda', 'audit', 'identity_link_tokens', 90, false, 'hashed tokens')
on conflict (tenant_id, table_name) do nothing;

commit;
