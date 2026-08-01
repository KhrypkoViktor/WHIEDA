-- WHIEDA review queue schema V02
-- Date: 2026-07-11
-- Purpose:
--   1. Keep current advisor_review_queue compatible with live workflow
--   2. Add trusted/candidate review operations
--   3. Prepare read-only reports and future triage commands

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
  add column if not exists review_priority text,
  add column if not exists review_owner text,
  add column if not exists trusted_reviewer boolean,
  add column if not exists trusted_reviewer_role text,
  add column if not exists trusted_reviewer_name text,
  add column if not exists external_username text,
  add column if not exists duplicate_of_ref text,
  add column if not exists verified_at timestamptz,
  add column if not exists closed_at timestamptz;

update advisor_review_queue
set
  queue_status = coalesce(queue_status, case when status = 'pending' then 'pending' else 'candidate' end),
  trust_level = coalesce(trust_level, 'unknown'),
  trusted_reviewer = coalesce(trusted_reviewer, false)
where
  queue_status is null
  or trust_level is null
  or trusted_reviewer is null;

alter table advisor_review_queue
  alter column queue_status set default 'candidate',
  alter column trust_level set default 'candidate',
  alter column trusted_reviewer set default false;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'advisor_review_queue_queue_status_chk'
  ) then
    alter table advisor_review_queue
      add constraint advisor_review_queue_queue_status_chk
      check (queue_status in (
        'candidate',
        'pending',
        'triage',
        'in_work',
        'applied',
        'verified',
        'closed',
        'duplicate',
        'rejected'
      ));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'advisor_review_queue_trust_level_chk'
  ) then
    alter table advisor_review_queue
      add constraint advisor_review_queue_trust_level_chk
      check (trust_level in ('candidate', 'trusted', 'system', 'unknown'));
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'advisor_review_queue_review_priority_chk'
  ) then
    alter table advisor_review_queue
      add constraint advisor_review_queue_review_priority_chk
      check (review_priority is null or review_priority in ('low', 'medium', 'high'));
  end if;
end $$;

create index if not exists idx_advisor_review_queue_queue_status
  on advisor_review_queue (queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_owner_status
  on advisor_review_queue (review_owner, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_type_status
  on advisor_review_queue (review_type, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_layer_status
  on advisor_review_queue (target_layer, queue_status, created_at desc);

create index if not exists idx_advisor_review_queue_trusted
  on advisor_review_queue (trusted_reviewer, queue_status, created_at desc);

create or replace view advisor_review_queue_pending_view as
select
  client_id,
  source_type,
  source_ref,
  source_title,
  queue_status,
  review_owner,
  review_priority,
  review_type,
  target_layer,
  trust_level,
  trusted_reviewer,
  trusted_reviewer_role,
  trusted_reviewer_name,
  external_username,
  created_at,
  updated_at,
  source_payload
from advisor_review_queue
where queue_status in ('pending', 'triage', 'in_work')
order by
  case review_priority
    when 'high' then 1
    when 'medium' then 2
    when 'low' then 3
    else 4
  end,
  created_at desc;

create or replace view advisor_review_candidates_view as
select
  client_id,
  source_type,
  source_ref,
  source_title,
  queue_status,
  review_owner,
  review_priority,
  review_type,
  target_layer,
  trust_level,
  trusted_reviewer,
  trusted_reviewer_role,
  trusted_reviewer_name,
  external_username,
  created_at,
  updated_at,
  source_payload
from advisor_review_queue
where queue_status = 'candidate'
order by created_at desc;

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
