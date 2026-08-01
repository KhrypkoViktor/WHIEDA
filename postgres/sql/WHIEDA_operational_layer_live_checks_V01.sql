-- WHIEDA operational layer live checks
-- Date: 2026-07-10
-- Purpose:
--   1. Check current state before applying the operational layer patch.
--   2. Re-check structure after apply.
--   3. Support a minimal smoke audit without changing workflow logic.

-- =========================================================
-- 1. PRE-CHECK: do the target tables already exist?
-- =========================================================

select
  table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'advisor_users',
    'advisor_surface_accounts',
    'advisor_conversations',
    'advisor_conversation_context',
    'advisor_events',
    'advisor_review_queue'
  )
order by table_name;

-- =========================================================
-- 2. PRE-CHECK: if any of them exist, inspect columns
-- =========================================================

select
  table_name,
  ordinal_position,
  column_name,
  data_type,
  is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in (
    'advisor_users',
    'advisor_surface_accounts',
    'advisor_conversations',
    'advisor_conversation_context',
    'advisor_events',
    'advisor_review_queue'
  )
order by table_name, ordinal_position;

-- =========================================================
-- 3. POST-CHECK: verify the exact critical columns
-- =========================================================

select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and (
    (table_name = 'advisor_events' and column_name in (
      'id',
      'event_type',
      'surface',
      'surface_message_id',
      'conversation_id',
      'raw_input',
      'normalized_input',
      'answer_text',
      'sidecar',
      'status',
      'error_text',
      'created_at'
    ))
    or
    (table_name = 'advisor_review_queue' and column_name in (
      'client_id',
      'source_type',
      'source_ref',
      'source_title',
      'source_payload',
      'status',
      'created_at',
      'updated_at'
    ))
    or
    (table_name = 'advisor_conversation_context' and column_name in (
      'client_id',
      'conversation_id',
      'context',
      'updated_at'
    ))
  )
order by table_name, column_name;

-- =========================================================
-- 4. POST-CHECK: verify indexes and primary keys
-- =========================================================

select
  schemaname,
  tablename,
  indexname,
  indexdef
from pg_indexes
where schemaname = 'public'
  and tablename in (
    'advisor_users',
    'advisor_surface_accounts',
    'advisor_conversations',
    'advisor_conversation_context',
    'advisor_events',
    'advisor_review_queue'
  )
order by tablename, indexname;

-- =========================================================
-- 5. POST-CHECK: row counters after first live tests
-- =========================================================

select 'advisor_users' as table_name, count(*) as rows_count from advisor_users
union all
select 'advisor_surface_accounts', count(*) from advisor_surface_accounts
union all
select 'advisor_conversations', count(*) from advisor_conversations
union all
select 'advisor_conversation_context', count(*) from advisor_conversation_context
union all
select 'advisor_events', count(*) from advisor_events
union all
select 'advisor_review_queue', count(*) from advisor_review_queue;

-- =========================================================
-- 6. POST-CHECK: latest events after Telegram smoke test
-- =========================================================

select
  id,
  event_type,
  surface,
  surface_message_id,
  conversation_id,
  status,
  created_at,
  left(coalesce(answer_text, ''), 120) as answer_preview,
  left(coalesce(error_text, ''), 120) as error_preview
from advisor_events
order by created_at desc
limit 20;

-- =========================================================
-- 7. POST-CHECK: latest review queue entries
-- =========================================================

select
  client_id,
  source_type,
  source_ref,
  source_title,
  status,
  created_at,
  updated_at
from advisor_review_queue
order by created_at desc
limit 20;

-- =========================================================
-- 8. POST-CHECK: latest stored conversation context
-- =========================================================

select
  client_id,
  conversation_id,
  context,
  updated_at
from advisor_conversation_context
order by updated_at desc
limit 20;
