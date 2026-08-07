-- Stage 7 pilot telemetry (local/staging; no auto-delete)
begin;

create table if not exists pilot_daily_metrics (
  tenant_id text not null,
  metric_date date not null,
  route_type text not null default 'all',
  route_opens int not null default 0,
  product_views int not null default 0,
  useful_actions int not null default 0,
  advisor_questions int not null default 0,
  telegram_links_created int not null default 0,
  telegram_opened int not null default 0,
  leads_created int not null default 0,
  onboarding_enrollments int not null default 0,
  onboarding_completions int not null default 0,
  sql_fallbacks int not null default 0,
  duplicate_events int not null default 0,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, metric_date, route_type)
);

create table if not exists pilot_outcome_events (
  outcome_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  session_id uuid,
  outcome_type text not null check (outcome_type in (
    'contacted', 'meeting', 'registered', 'refused', 'unknown'
  )),
  source_route text,
  idempotency_key text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

alter table if exists pilot_daily_metrics enable row level security;
alter table if exists pilot_outcome_events enable row level security;

drop policy if exists pilot_daily_metrics_tenant on pilot_daily_metrics;
create policy pilot_daily_metrics_tenant on pilot_daily_metrics
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists pilot_outcome_events_tenant on pilot_outcome_events;
create policy pilot_outcome_events_tenant on pilot_outcome_events
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
