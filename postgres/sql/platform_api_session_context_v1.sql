-- Platform API session context (additive, Core API owned)
begin;

create table if not exists platform_session_context (
  tenant_id text not null,
  session_id text not null,
  first_ref text,
  active_ref text,
  context jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, session_id)
);

create index if not exists idx_platform_session_context_updated
  on platform_session_context (tenant_id, updated_at desc);

commit;
