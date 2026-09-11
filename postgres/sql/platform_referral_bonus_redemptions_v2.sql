-- WWC Platform referral bonus redemptions V2.
-- Additive, user-owned confirmation intents for full-plan WUSD redemption.

begin;

create table if not exists partner_bonus_redemption_intents (
  intent_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null references lead_actors (actor_id),
  plan_code text not null,
  ref_code text not null,
  cost_wusd_minor bigint not null check (cost_wusd_minor > 0),
  telegram_chat_id bigint not null,
  telegram_user_id bigint not null,
  expires_at timestamptz not null,
  consumed_entry_id uuid references partner_bonus_ledger (entry_id),
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, plan_code) references partner_subscription_plans (tenant_id, plan_code),
  foreign key (tenant_id, ref_code) references partner_subscriptions (tenant_id, ref_code),
  unique (tenant_id, telegram_chat_id, telegram_user_id, intent_id),
  check (not (consumed_entry_id is not null and cancelled_at is not null))
);

create index if not exists idx_partner_bonus_redemption_intents_expiry
  on partner_bonus_redemption_intents (tenant_id, expires_at)
  where consumed_entry_id is null and cancelled_at is null;

alter table partner_bonus_redemption_intents enable row level security;

drop policy if exists partner_bonus_redemption_intents_tenant_isolation on partner_bonus_redemption_intents;
create policy partner_bonus_redemption_intents_tenant_isolation on partner_bonus_redemption_intents
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
