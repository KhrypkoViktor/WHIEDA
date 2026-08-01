-- WHIEDA review queue schema V03
-- Date: 2026-07-12
-- Purpose:
--   1. Add explicit operational fields for review queue handling
--   2. Backfill the new fields from legacy columns and source_payload
--   3. Keep source_payload/status backward-compatible for live workflow switching

begin;

create table if not exists advisor_trusted_reviewers (
  client_id text not null,
  reviewer_key text not null,
  telegram_user_id text,
  telegram_username text,
  display_name text,
  role text not null,
  active boolean not null default true,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (client_id, reviewer_key)
);

create unique index if not exists uq_advisor_trusted_reviewers_user_id
  on advisor_trusted_reviewers (client_id, telegram_user_id)
  where telegram_user_id is not null;

create unique index if not exists uq_advisor_trusted_reviewers_username
  on advisor_trusted_reviewers (client_id, lower(telegram_username))
  where telegram_username is not null;

alter table advisor_review_queue
  add column if not exists queue_status text,
  add column if not exists trust_level text,
  add column if not exists review_type text,
  add column if not exists target_layer text,
  add column if not exists priority text,
  add column if not exists owner text,
  add column if not exists trusted_reviewer boolean,
  add column if not exists trusted_reviewer_role text,
  add column if not exists trusted_reviewer_name text,
  add column if not exists external_username text,
  add column if not exists feedback_type text,
  add column if not exists feedback_text text,
  add column if not exists gap_reason text,
  add column if not exists user_text text,
  add column if not exists bot_answer text,
  add column if not exists suggested_fix text,
  add column if not exists duplicate_of_ref text,
  add column if not exists source_title_normalized text,
  add column if not exists taken_by text,
  add column if not exists taken_at timestamptz,
  add column if not exists applied_by text,
  add column if not exists applied_at timestamptz,
  add column if not exists verified_by text,
  add column if not exists verified_at timestamptz,
  add column if not exists closed_by text,
  add column if not exists closed_at timestamptz,
  add column if not exists triaged_at timestamptz,
  add column if not exists triage_note text,
  add column if not exists review_priority text,
  add column if not exists review_owner text;

update advisor_review_queue
set
  queue_status = coalesce(
    nullif(queue_status, ''),
    nullif(source_payload->>'queue_status', ''),
    nullif(status, ''),
    case
      when coalesce(source_payload->>'trusted_reviewer', 'false') = 'true' then 'pending'
      else 'candidate'
    end
  ),
  trust_level = coalesce(
    nullif(trust_level, ''),
    nullif(source_payload->>'trust_level', ''),
    case
      when coalesce(source_payload->>'trusted_reviewer', 'false') = 'true' then 'trusted'
      when status in ('approved', 'verified', 'closed', 'applied', 'in_work', 'triage', 'pending') then 'candidate'
      else 'candidate'
    end
  ),
  review_type = coalesce(nullif(review_type, ''), nullif(source_payload->>'review_type', '')),
  target_layer = coalesce(nullif(target_layer, ''), nullif(source_payload->>'target_layer', '')),
  priority = coalesce(
    nullif(priority, ''),
    nullif(review_priority, ''),
    nullif(source_payload->>'priority', ''),
    nullif(source_payload->>'review_priority', '')
  ),
  owner = coalesce(
    nullif(owner, ''),
    nullif(review_owner, ''),
    nullif(source_payload->>'owner', ''),
    nullif(source_payload->>'review_owner', '')
  ),
  trusted_reviewer = coalesce(
    trusted_reviewer,
    case when coalesce(source_payload->>'trusted_reviewer', 'false') = 'true' then true else false end
  ),
  trusted_reviewer_role = coalesce(nullif(trusted_reviewer_role, ''), nullif(source_payload->>'trusted_reviewer_role', '')),
  trusted_reviewer_name = coalesce(nullif(trusted_reviewer_name, ''), nullif(source_payload->>'trusted_reviewer_name', '')),
  external_username = coalesce(nullif(external_username, ''), nullif(source_payload->>'external_username', '')),
  feedback_type = coalesce(nullif(feedback_type, ''), nullif(source_payload->>'feedback_type', '')),
  feedback_text = coalesce(nullif(feedback_text, ''), nullif(source_payload->>'feedback_text', '')),
  gap_reason = coalesce(nullif(gap_reason, ''), nullif(source_payload->>'gap_reason', '')),
  user_text = coalesce(nullif(user_text, ''), nullif(source_payload->>'user_text', '')),
  bot_answer = coalesce(nullif(bot_answer, ''), nullif(source_payload->>'bot_answer', '')),
  suggested_fix = coalesce(nullif(suggested_fix, ''), nullif(source_payload->>'suggested_fix', '')),
  duplicate_of_ref = coalesce(nullif(duplicate_of_ref, ''), nullif(source_payload->>'duplicate_of_ref', '')),
  source_title_normalized = coalesce(
    nullif(source_title_normalized, ''),
    lower(regexp_replace(coalesce(source_title, ''), '\s+', ' ', 'g'))
  ),
  taken_by = coalesce(nullif(taken_by, ''), nullif(source_payload->>'taken_by_username', '')),
  taken_at = coalesce(
    taken_at,
    nullif(source_payload->>'taken_at', '')::timestamptz,
    nullif(source_payload->>'triaged_at', '')::timestamptz
  ),
  applied_by = coalesce(nullif(applied_by, ''), nullif(source_payload->>'applied_by_username', '')),
  applied_at = coalesce(applied_at, nullif(source_payload->>'applied_at', '')::timestamptz),
  verified_by = coalesce(nullif(verified_by, ''), nullif(source_payload->>'verified_by_username', '')),
  verified_at = coalesce(verified_at, nullif(source_payload->>'verified_at', '')::timestamptz),
  closed_by = coalesce(nullif(closed_by, ''), nullif(source_payload->>'closed_by_username', '')),
  closed_at = coalesce(closed_at, nullif(source_payload->>'closed_at', '')::timestamptz),
  triaged_at = coalesce(triaged_at, nullif(source_payload->>'triaged_at', '')::timestamptz),
  triage_note = coalesce(nullif(triage_note, ''), nullif(source_payload->>'triage_note', '')),
  review_priority = coalesce(nullif(review_priority, ''), priority),
  review_owner = coalesce(nullif(review_owner, ''), owner)
where true;

update advisor_review_queue
set
  status = case
    when queue_status in (
      'candidate',
      'pending',
      'triage',
      'approved',
      'in_work',
      'applied',
      'verified',
      'closed',
      'duplicate',
      'rejected'
    ) then queue_status
    else status
  end
where queue_status is not null
  and queue_status <> status;

alter table advisor_review_queue
  alter column queue_status set default 'candidate',
  alter column trust_level set default 'candidate',
  alter column trusted_reviewer set default false;

do $$
begin
  if exists (
    select 1
    from pg_constraint
    where conname = 'advisor_review_queue_status_check'
      and conrelid = 'advisor_review_queue'::regclass
  ) then
    alter table advisor_review_queue
      drop constraint advisor_review_queue_status_check;
  end if;
end $$;

alter table advisor_review_queue
  add constraint advisor_review_queue_status_check
  check (status in (
    'candidate',
    'pending',
    'triage',
    'approved',
    'in_work',
    'applied',
    'verified',
    'closed',
    'duplicate',
    'rejected'
  ));

do $$
begin
  if exists (
    select 1 from pg_constraint
    where conname = 'advisor_review_queue_queue_status_chk'
      and conrelid = 'advisor_review_queue'::regclass
  ) then
    alter table advisor_review_queue
      drop constraint advisor_review_queue_queue_status_chk;
  end if;
end $$;

alter table advisor_review_queue
  add constraint advisor_review_queue_queue_status_chk
  check (queue_status in (
    'candidate',
    'pending',
    'triage',
    'approved',
    'in_work',
    'applied',
    'verified',
    'closed',
    'duplicate',
    'rejected'
  ));

do $$
begin
  if exists (
    select 1 from pg_constraint
    where conname = 'advisor_review_queue_trust_level_chk'
      and conrelid = 'advisor_review_queue'::regclass
  ) then
    alter table advisor_review_queue
      drop constraint advisor_review_queue_trust_level_chk;
  end if;
end $$;

alter table advisor_review_queue
  add constraint advisor_review_queue_trust_level_chk
  check (trust_level in ('candidate', 'trusted', 'system', 'unknown'));

do $$
begin
  if exists (
    select 1 from pg_constraint
    where conname = 'advisor_review_queue_priority_chk'
      and conrelid = 'advisor_review_queue'::regclass
  ) then
    alter table advisor_review_queue
      drop constraint advisor_review_queue_priority_chk;
  end if;
end $$;

alter table advisor_review_queue
  add constraint advisor_review_queue_priority_chk
  check (priority is null or priority in ('low', 'medium', 'high'));

create index if not exists idx_advisor_review_queue_status_created
  on advisor_review_queue (queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_owner_status_created
  on advisor_review_queue (owner, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_priority_status_created
  on advisor_review_queue (priority, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_type_status_created
  on advisor_review_queue (review_type, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_layer_status_created
  on advisor_review_queue (target_layer, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_trust_status_created
  on advisor_review_queue (trust_level, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_username_created
  on advisor_review_queue (external_username, created_at desc);

create or replace view advisor_review_queue_actionable_view as
select
  client_id,
  source_type,
  source_ref,
  source_title,
  queue_status,
  status as legacy_status,
  trust_level,
  review_type,
  target_layer,
  priority,
  owner,
  trusted_reviewer,
  trusted_reviewer_role,
  trusted_reviewer_name,
  external_username,
  feedback_type,
  feedback_text,
  gap_reason,
  user_text,
  bot_answer,
  suggested_fix,
  taken_by,
  taken_at,
  applied_by,
  applied_at,
  verified_by,
  verified_at,
  closed_by,
  closed_at,
  triaged_at,
  triage_note,
  created_at,
  updated_at,
  source_payload,
  priority as review_priority,
  owner as review_owner
from advisor_review_queue
where queue_status in ('pending', 'triage', 'approved', 'in_work', 'applied')
order by
  case priority
    when 'high' then 1
    when 'medium' then 2
    when 'low' then 3
    else 4
  end,
  created_at desc;

create or replace view advisor_review_queue_candidates_view as
select
  client_id,
  source_type,
  source_ref,
  source_title,
  queue_status,
  status as legacy_status,
  trust_level,
  review_type,
  target_layer,
  priority,
  owner,
  trusted_reviewer,
  trusted_reviewer_role,
  trusted_reviewer_name,
  external_username,
  feedback_type,
  feedback_text,
  gap_reason,
  user_text,
  bot_answer,
  suggested_fix,
  created_at,
  updated_at,
  source_payload,
  priority as review_priority,
  owner as review_owner
from advisor_review_queue
where queue_status = 'candidate'
order by created_at desc;

create or replace view advisor_review_queue_stats_view as
select
  client_id,
  coalesce(owner, 'unassigned') as owner,
  coalesce(review_type, 'unknown') as review_type,
  coalesce(target_layer, 'unknown') as target_layer,
  coalesce(priority, 'unknown') as priority,
  coalesce(queue_status, 'unknown') as queue_status,
  coalesce(trust_level, 'unknown') as trust_level,
  count(*) as item_count,
  max(created_at) as latest_created_at
from advisor_review_queue
group by
  client_id,
  coalesce(owner, 'unassigned'),
  coalesce(review_type, 'unknown'),
  coalesce(target_layer, 'unknown'),
  coalesce(priority, 'unknown'),
  coalesce(queue_status, 'unknown'),
  coalesce(trust_level, 'unknown');

insert into advisor_trusted_reviewers (
  client_id,
  reviewer_key,
  telegram_username,
  display_name,
  role,
  active,
  notes
)
values
  ('whieda', 'sunraysword', 'SunRaySword', 'Viktor Khripko', 'super_admin', true, 'temporary username-based match until telegram_user_id is fixed'),
  ('whieda', 'onlineelena', 'OnlineElena', 'Elena Datskevich', 'business', true, 'temporary username-based match until telegram_user_id is fixed')
on conflict (client_id, reviewer_key) do update
set
  telegram_username = excluded.telegram_username,
  display_name = excluded.display_name,
  role = excluded.role,
  active = excluded.active,
  notes = excluded.notes,
  updated_at = now();

commit;
