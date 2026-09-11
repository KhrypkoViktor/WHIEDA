-- WWC Platform referral-admin intents V3.
-- Owner-only operations are previewed and confirmed; raw commands never mutate state.

begin;

create table if not exists partner_referral_admin_intents (
  intent_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  operation text not null check (operation in ('assign_referrer', 'adjust_bonus')),
  payload jsonb not null,
  telegram_chat_id bigint not null,
  telegram_user_id bigint not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  check (not (consumed_at is not null and cancelled_at is not null))
);

create index if not exists idx_partner_referral_admin_intents_expiry
  on partner_referral_admin_intents (tenant_id, expires_at)
  where consumed_at is null and cancelled_at is null;

alter table partner_referral_admin_intents enable row level security;

drop policy if exists partner_referral_admin_intents_tenant_isolation on partner_referral_admin_intents;
create policy partner_referral_admin_intents_tenant_isolation on partner_referral_admin_intents
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
