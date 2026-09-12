-- Idempotent delivery log for partner subscription reminders.

begin;

create table if not exists partner_subscription_reminder_log (
  delivery_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  event_type text not null check (event_type in ('due_7d', 'grace_start', 'grace_last')),
  paid_until timestamptz not null,
  telegram_chat_id bigint not null,
  status text not null check (status in ('sending', 'sent', 'failed')),
  attempt_count integer not null default 1 check (attempt_count > 0),
  telegram_message_id bigint,
  error_text text,
  last_attempt_at timestamptz not null default now(),
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (tenant_id, ref_code, event_type, paid_until)
);

create index if not exists idx_partner_subscription_reminder_retry
  on partner_subscription_reminder_log (tenant_id, status, last_attempt_at);

alter table partner_subscription_reminder_log enable row level security;

drop policy if exists partner_subscription_reminder_log_tenant_isolation
  on partner_subscription_reminder_log;
create policy partner_subscription_reminder_log_tenant_isolation
  on partner_subscription_reminder_log
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
