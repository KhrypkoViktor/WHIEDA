-- WHIEDA user_memory_facts V1 (Stage 3 — confirmed long-term facts only)
-- Status: staging-first; no auto-ingest from raw chat.

begin;

create table if not exists user_memory_facts (
  fact_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  subject_type text not null check (subject_type in ('visitor_session', 'telegram_user')),
  subject_id text not null,
  fact_key text not null,
  fact_value jsonb not null default '{}'::jsonb,
  source text not null default 'confirmed',
  consent_scope text,
  retention_class text not null default 'profile',
  confirmed_at timestamptz not null default now(),
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  unique (tenant_id, subject_type, subject_id, fact_key)
);

create index if not exists idx_user_memory_facts_subject
  on user_memory_facts (tenant_id, subject_type, subject_id);

alter table if exists user_memory_facts enable row level security;

drop policy if exists user_memory_facts_tenant on user_memory_facts;
create policy user_memory_facts_tenant on user_memory_facts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
