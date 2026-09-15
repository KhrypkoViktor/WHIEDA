-- Production-safe event journal for advisor gaps and future Core telemetry.
-- Idempotent: creates only the missing table, indexes, and tenant RLS policy.

create table if not exists interaction_events (
  event_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  session_id uuid,
  event_type text not null,
  idempotency_key text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

create index if not exists idx_interaction_events_tenant_type
  on interaction_events (tenant_id, event_type, created_at desc);

create index if not exists idx_interaction_events_session
  on interaction_events (tenant_id, session_id, created_at desc)
  where session_id is not null;

alter table interaction_events enable row level security;

drop policy if exists interaction_events_tenant_isolation on interaction_events;
create policy interaction_events_tenant_isolation on interaction_events
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());
