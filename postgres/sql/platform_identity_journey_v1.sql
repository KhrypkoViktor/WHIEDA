-- WHIEDA Platform Identity & Journey V1 (Stage 3 — site ↔ Telegram)
-- Status: staging-first DDL; do NOT apply to production without owner review.
-- Spec: WHIEDA_CURRENT_BUILD_PLAN_SITE_TELEGRAM_ONBOARDING_V1_2026-08-07.md §3

begin;

-- ---------------------------------------------------------------------------
-- visitor_sessions — stable browser/session identity per tenant
-- ---------------------------------------------------------------------------
create table if not exists visitor_sessions (
  tenant_id text not null,
  session_id uuid not null default gen_random_uuid(),
  first_ref text,
  current_ref text,
  attributed_owner_id text,
  assigned_owner_id text,
  journey_type text not null default 'organic'
    check (journey_type in ('product', 'business', 'organic', 'direct')),
  campaign text,
  source text,
  content text,
  consent_scope text,
  retention_class text not null default 'operational',
  context jsonb not null default '{}'::jsonb,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, session_id)
);

create index if not exists idx_visitor_sessions_tenant_updated
  on visitor_sessions (tenant_id, updated_at desc);

create index if not exists idx_visitor_sessions_first_ref
  on visitor_sessions (tenant_id, first_ref)
  where first_ref is not null;

-- ---------------------------------------------------------------------------
-- journey_attributions — immutable first-touch + subsequent ref changes
-- ---------------------------------------------------------------------------
create table if not exists journey_attributions (
  attribution_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  session_id uuid not null,
  ref_code text,
  attributed_owner_id text,
  journey_type text not null default 'organic'
    check (journey_type in ('product', 'business', 'organic', 'direct')),
  campaign text,
  source text,
  content text,
  is_first_touch boolean not null default false,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, session_id)
    references visitor_sessions (tenant_id, session_id)
    on delete cascade
);

create index if not exists idx_journey_attributions_session
  on journey_attributions (tenant_id, session_id, created_at desc);

-- ---------------------------------------------------------------------------
-- identity_link_tokens — one-time opaque site → Telegram bridge
-- ---------------------------------------------------------------------------
create table if not exists identity_link_tokens (
  token_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  session_id uuid not null,
  token_hash text not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  used_by_telegram_user_id bigint,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, session_id)
    references visitor_sessions (tenant_id, session_id)
    on delete cascade,
  unique (token_hash)
);

create index if not exists idx_identity_link_tokens_session
  on identity_link_tokens (tenant_id, session_id, created_at desc);

create index if not exists idx_identity_link_tokens_expires
  on identity_link_tokens (tenant_id, expires_at)
  where used_at is null;

-- ---------------------------------------------------------------------------
-- telegram_identity_links — bound Telegram user ↔ visitor session
-- ---------------------------------------------------------------------------
create table if not exists telegram_identity_links (
  link_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  session_id uuid not null,
  telegram_user_id bigint not null,
  telegram_chat_id bigint,
  first_ref text,
  attributed_owner_id text,
  assigned_owner_id text,
  journey_type text,
  context_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, session_id)
    references visitor_sessions (tenant_id, session_id)
    on delete cascade,
  unique (tenant_id, telegram_user_id)
);

create index if not exists idx_telegram_identity_links_session
  on telegram_identity_links (tenant_id, session_id);

-- ---------------------------------------------------------------------------
-- interaction_events — route / CTA / link telemetry (idempotent)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- RLS (defense in depth; API sets app.tenant_id via tenant_connection)
-- ---------------------------------------------------------------------------
alter table if exists visitor_sessions enable row level security;
alter table if exists journey_attributions enable row level security;
alter table if exists identity_link_tokens enable row level security;
alter table if exists telegram_identity_links enable row level security;
alter table if exists interaction_events enable row level security;

drop policy if exists visitor_sessions_tenant_isolation on visitor_sessions;
create policy visitor_sessions_tenant_isolation on visitor_sessions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists journey_attributions_tenant_isolation on journey_attributions;
create policy journey_attributions_tenant_isolation on journey_attributions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists identity_link_tokens_tenant_isolation on identity_link_tokens;
create policy identity_link_tokens_tenant_isolation on identity_link_tokens
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists telegram_identity_links_tenant_isolation on telegram_identity_links;
create policy telegram_identity_links_tenant_isolation on telegram_identity_links
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists interaction_events_tenant_isolation on interaction_events;
create policy interaction_events_tenant_isolation on interaction_events
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
