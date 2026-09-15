-- Advisor gap operator review queue (staging/local only).
-- Projection over interaction_events.advisor_gap — events stay immutable.

begin;

create table if not exists advisor_gap_review_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  dedup_key text not null,
  gap_kind text not null,
  question_normalized text not null,
  detected_product text,
  event_count integer not null default 1 check (event_count >= 1),
  first_seen_at timestamptz not null,
  last_seen_at timestamptz not null,
  status text not null default 'new'
    check (status in (
      'new', 'triaged', 'in_review', 'approved_candidate', 'rejected', 'resolved'
    )),
  priority text not null default 'p2'
    check (priority in ('p0', 'p1', 'p2', 'p3')),
  owner_role text
    check (owner_role is null or owner_role in ('owner', 'medical', 'business', 'admin')),
  owner_name text,
  operator_note text,
  candidate_type text not null default 'none'
    check (candidate_type in (
      'alias', 'clarification_rule', 'resource_link', 'business_faq',
      'medical_review', 'safety_review', 'intent_gap', 'none'
    )),
  evidence_ref text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  resolved_at timestamptz,
  unique (tenant_id, dedup_key)
);

create index if not exists idx_advisor_gap_review_items_tenant_status
  on advisor_gap_review_items (tenant_id, status, priority, last_seen_at desc);

create index if not exists idx_advisor_gap_review_items_tenant_kind
  on advisor_gap_review_items (tenant_id, gap_kind, last_seen_at desc);

create table if not exists advisor_gap_review_mutations (
  mutation_id bigserial primary key,
  tenant_id text not null,
  item_id uuid not null references advisor_gap_review_items (id) on delete cascade,
  actor_principal_id uuid,
  old_values jsonb not null default '{}'::jsonb,
  new_values jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_advisor_gap_review_mutations_item
  on advisor_gap_review_mutations (tenant_id, item_id, created_at desc);

alter table if exists advisor_gap_review_items enable row level security;
alter table if exists advisor_gap_review_mutations enable row level security;

drop policy if exists advisor_gap_review_items_tenant_isolation on advisor_gap_review_items;
create policy advisor_gap_review_items_tenant_isolation on advisor_gap_review_items
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists advisor_gap_review_mutations_tenant_isolation on advisor_gap_review_mutations;
create policy advisor_gap_review_mutations_tenant_isolation on advisor_gap_review_mutations
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
