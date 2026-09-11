-- WWC Platform referral bonuses V1.
-- Additive schema only. No existing subscription, payment or referral row is changed.
-- Apply after platform_partner_subscriptions_v1.sql and currency V2.

begin;

-- Keep the current three-month payment flow working while allowing later plan selection.
alter table if exists partner_payment_ledger
  add column if not exists product_code text not null default 'platform_subscription';
alter table if exists partner_payment_ledger
  drop constraint if exists partner_payment_ledger_access_months_check;
alter table if exists partner_payment_ledger
  add constraint partner_payment_ledger_access_months_check
  check (access_months in (3, 6, 12));

alter table if exists partner_payment_intents
  add column if not exists product_code text not null default 'platform_subscription',
  add column if not exists access_months smallint not null default 3;
alter table if exists partner_payment_intents
  drop constraint if exists partner_payment_intents_access_months_check;
alter table if exists partner_payment_intents
  add constraint partner_payment_intents_access_months_check
  check (access_months in (3, 6, 12));

create table if not exists partner_subscription_plans (
  tenant_id text not null references tenants (tenant_id),
  plan_code text not null,
  product_code text not null,
  access_months smallint not null check (access_months in (3, 6, 12)),
  price_wusd_minor bigint not null check (price_wusd_minor > 0),
  price_rub_minor bigint not null check (price_rub_minor > 0),
  active boolean not null default true,
  valid_from timestamptz not null default now(),
  valid_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, plan_code),
  check (valid_until is null or valid_until > valid_from)
);

create index if not exists idx_partner_subscription_plans_available
  on partner_subscription_plans (tenant_id, product_code, valid_from desc)
  where active = true;

create table if not exists referral_invite_codes (
  tenant_id text not null references tenants (tenant_id),
  invite_code text not null,
  inviter_actor_id text not null references lead_actors (actor_id),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key (tenant_id, invite_code),
  check (invite_code ~ '^[A-Za-z0-9_-]{8,64}$'),
  check (not active or revoked_at is null)
);

create unique index if not exists uq_referral_invite_codes_active_actor
  on referral_invite_codes (tenant_id, inviter_actor_id)
  where active = true;

create table if not exists partner_referral_attributions (
  tenant_id text not null references tenants (tenant_id),
  invitee_actor_id text not null references lead_actors (actor_id),
  inviter_actor_id text not null references lead_actors (actor_id),
  invite_code text,
  source text not null check (source in ('telegram_deeplink', 'admin_manual')),
  created_at timestamptz not null default now(),
  created_by_actor_id text references lead_actors (actor_id),
  primary key (tenant_id, invitee_actor_id),
  check (invitee_actor_id <> inviter_actor_id)
);

create index if not exists idx_partner_referral_attributions_inviter
  on partner_referral_attributions (tenant_id, inviter_actor_id, created_at desc);

create table if not exists partner_referral_attribution_audit (
  audit_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  invitee_actor_id text not null references lead_actors (actor_id),
  old_inviter_actor_id text references lead_actors (actor_id),
  new_inviter_actor_id text references lead_actors (actor_id),
  action text not null check (action in ('created', 'admin_reassigned')),
  reason text,
  changed_by_actor_id text references lead_actors (actor_id),
  created_at timestamptz not null default now(),
  check (old_inviter_actor_id is null or old_inviter_actor_id <> new_inviter_actor_id)
);

create index if not exists idx_partner_referral_attribution_audit_invitee
  on partner_referral_attribution_audit (tenant_id, invitee_actor_id, created_at desc);

create table if not exists referral_reward_rules (
  rule_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  product_code text not null,
  first_payment_bps integer not null check (first_payment_bps between 0 and 10000),
  renewal_payment_bps integer not null check (renewal_payment_bps between 0 and 10000),
  reward_currency text not null check (reward_currency = 'WUSD'),
  active boolean not null default true,
  valid_from timestamptz not null default now(),
  valid_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, product_code, valid_from),
  check (valid_until is null or valid_until > valid_from)
);

create unique index if not exists uq_referral_reward_rules_current
  on referral_reward_rules (tenant_id, product_code)
  where active = true and valid_until is null;

create table if not exists partner_bonus_ledger (
  entry_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null references lead_actors (actor_id),
  entry_type text not null check (entry_type in ('credit', 'debit', 'reversal', 'admin_adjustment')),
  amount_minor bigint not null check (amount_minor <> 0),
  currency text not null check (currency = 'WUSD'),
  product_code text not null,
  source_payment_id uuid references partner_payment_ledger (payment_id),
  related_entry_id uuid references partner_bonus_ledger (entry_id),
  idempotency_key text not null,
  rule_snapshot jsonb not null default '{}'::jsonb,
  description text,
  created_by_actor_id text references lead_actors (actor_id),
  created_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key),
  check (
    (entry_type in ('credit', 'admin_adjustment') and amount_minor > 0)
    or (entry_type in ('debit', 'reversal') and amount_minor < 0)
  )
);

create unique index if not exists uq_partner_bonus_ledger_payment_credit
  on partner_bonus_ledger (tenant_id, source_payment_id)
  where entry_type = 'credit' and source_payment_id is not null;

create index if not exists idx_partner_bonus_ledger_actor_time
  on partner_bonus_ledger (tenant_id, actor_id, created_at desc);

-- The database needs the same tenant guarantee that the application assumes.
create or replace function partner_referral_bonus_tenant_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if tg_table_name = 'referral_invite_codes' then
    if not exists (
      select 1 from public.lead_actors actor
      where actor.actor_id = new.inviter_actor_id and actor.tenant_id = new.tenant_id
    ) then
      raise exception 'invite owner does not belong to tenant' using errcode = '23514';
    end if;
  elsif tg_table_name = 'partner_referral_attributions' then
    if not exists (
      select 1 from public.lead_actors actor
      where actor.actor_id = new.invitee_actor_id and actor.tenant_id = new.tenant_id
    ) or not exists (
      select 1 from public.lead_actors actor
      where actor.actor_id = new.inviter_actor_id and actor.tenant_id = new.tenant_id
    ) then
      raise exception 'attribution actor does not belong to tenant' using errcode = '23514';
    end if;
    if new.invite_code is not null and not exists (
      select 1 from public.referral_invite_codes code
      where code.tenant_id = new.tenant_id
        and code.invite_code = new.invite_code
        and code.inviter_actor_id = new.inviter_actor_id
    ) then
      raise exception 'invite code does not belong to attribution' using errcode = '23514';
    end if;
  elsif tg_table_name = 'partner_bonus_ledger' then
    if not exists (
      select 1 from public.lead_actors actor
      where actor.actor_id = new.actor_id and actor.tenant_id = new.tenant_id
    ) then
      raise exception 'bonus owner does not belong to tenant' using errcode = '23514';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_referral_invite_code_tenant_guard on referral_invite_codes;
create trigger trg_referral_invite_code_tenant_guard
before insert or update of tenant_id, inviter_actor_id on referral_invite_codes
for each row execute function partner_referral_bonus_tenant_guard();

drop trigger if exists trg_partner_referral_attribution_tenant_guard on partner_referral_attributions;
create trigger trg_partner_referral_attribution_tenant_guard
before insert or update of tenant_id, invitee_actor_id, inviter_actor_id, invite_code on partner_referral_attributions
for each row execute function partner_referral_bonus_tenant_guard();

drop trigger if exists trg_partner_bonus_ledger_tenant_guard on partner_bonus_ledger;
create trigger trg_partner_bonus_ledger_tenant_guard
before insert or update of tenant_id, actor_id on partner_bonus_ledger
for each row execute function partner_referral_bonus_tenant_guard();

alter table partner_subscription_plans enable row level security;
alter table referral_invite_codes enable row level security;
alter table partner_referral_attributions enable row level security;
alter table partner_referral_attribution_audit enable row level security;
alter table referral_reward_rules enable row level security;
alter table partner_bonus_ledger enable row level security;

drop policy if exists partner_subscription_plans_tenant_isolation on partner_subscription_plans;
create policy partner_subscription_plans_tenant_isolation on partner_subscription_plans
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists referral_invite_codes_tenant_isolation on referral_invite_codes;
create policy referral_invite_codes_tenant_isolation on referral_invite_codes
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_referral_attributions_tenant_isolation on partner_referral_attributions;
create policy partner_referral_attributions_tenant_isolation on partner_referral_attributions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_referral_attribution_audit_tenant_isolation on partner_referral_attribution_audit;
create policy partner_referral_attribution_audit_tenant_isolation on partner_referral_attribution_audit
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists referral_reward_rules_tenant_isolation on referral_reward_rules;
create policy referral_reward_rules_tenant_isolation on referral_reward_rules
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_bonus_ledger_tenant_isolation on partner_bonus_ledger;
create policy partner_bonus_ledger_tenant_isolation on partner_bonus_ledger
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

insert into partner_subscription_plans (
  tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor
) values
  ('whieda', 'platform_3m', 'platform_subscription', 3, 3000, 300000),
  ('whieda', 'platform_6m', 'platform_subscription', 6, 5400, 540000),
  ('whieda', 'platform_12m', 'platform_subscription', 12, 9600, 960000)
on conflict (tenant_id, plan_code) do nothing;

insert into referral_reward_rules (
  tenant_id, product_code, first_payment_bps, renewal_payment_bps, reward_currency
) values ('whieda', 'platform_subscription', 2000, 1000, 'WUSD')
on conflict do nothing;

commit;
