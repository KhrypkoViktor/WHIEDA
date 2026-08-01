-- WHIEDA current live operational layer
-- Date: 2026-07-10
-- Target: current VPS/Postgres for advisor-whieda-phase1
-- Status: draft, not applied
-- Correction after live smoke test:
--   this file was prepared from workflow query reading, but the live workflow
--   writes to a separate product database via credential `advisor-dev-postgres`.
--   The current live product runtime schema is not identical to this local draft.
-- Purpose:
--   1. Add the missing operational tables used by the live n8n workflow.
--   2. Match the current workflow queries as closely as possible.
--   3. Avoid speculative fields that are not needed for the first safe rollout.

create extension if not exists pgcrypto;

create table if not exists advisor_users (
  user_id uuid primary key default gen_random_uuid(),
  client_id text not null,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_advisor_users_client
  on advisor_users (client_id);

create table if not exists advisor_surface_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references advisor_users(user_id) on delete cascade,
  surface text not null,
  surface_user_id text not null,
  surface_chat_id text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Matches the live ON CONFLICT target:
-- ON CONFLICT (surface, surface_user_id, COALESCE(surface_chat_id, ''))
create unique index if not exists uq_advisor_surface_accounts_surface_expr
  on advisor_surface_accounts (surface, surface_user_id, coalesce(surface_chat_id, ''));

create index if not exists idx_advisor_surface_accounts_user
  on advisor_surface_accounts (user_id);

create table if not exists advisor_conversations (
  conversation_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references advisor_users(user_id) on delete cascade,
  surface text not null,
  surface_thread_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_advisor_conversations_surface
  on advisor_conversations (surface, surface_thread_id, user_id);

create index if not exists idx_advisor_conversations_user
  on advisor_conversations (user_id);

create table if not exists advisor_conversation_context (
  client_id text not null,
  conversation_id uuid not null references advisor_conversations(conversation_id) on delete cascade,
  context jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (client_id, conversation_id)
);

create index if not exists idx_advisor_conversation_context_updated
  on advisor_conversation_context (updated_at desc);

create table if not exists advisor_events (
  id bigserial primary key,
  event_type text not null,
  surface text not null,
  surface_message_id text,
  conversation_id uuid,
  raw_input jsonb,
  normalized_input jsonb not null default '{}'::jsonb,
  answer_text text,
  sidecar jsonb,
  status text,
  error_text text,
  created_at timestamptz not null default now()
);

create index if not exists idx_advisor_events_type_created
  on advisor_events (event_type, created_at desc);

create index if not exists idx_advisor_events_conversation
  on advisor_events (conversation_id, created_at desc);

create index if not exists idx_advisor_events_surface_message
  on advisor_events (surface, surface_message_id);

create index if not exists idx_advisor_events_status_created
  on advisor_events (status, created_at desc);

create table if not exists advisor_review_queue (
  client_id text not null,
  source_type text not null,
  source_ref text not null,
  source_title text,
  source_payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (client_id, source_type, source_ref)
);

create index if not exists idx_advisor_review_queue_status
  on advisor_review_queue (status, created_at desc);
