-- WWC partner subscriptions, manual payment ledger and tenant isolation.
-- Additive migration. Apply after tenant registry, leads/referrals and RLS helpers.

begin;

alter table if exists lead_actors
  add column if not exists telegram_user_id bigint;

create unique index if not exists idx_lead_actors_tenant_telegram_user
  on lead_actors (tenant_id, telegram_user_id)
  where telegram_user_id is not null;

create or replace function partner_subscription_grace_until(p_paid_until timestamptz)
returns timestamptz
language sql
immutable
parallel safe
as $$
  select case
    when p_paid_until is null then null
    else p_paid_until + interval '3 days'
  end;
$$;

create or replace function partner_subscription_state(
  p_paid_until timestamptz,
  p_at timestamptz default now()
)
returns text
language sql
stable
parallel safe
as $$
  select case
    when p_paid_until is null then 'no_subscription'
    when p_at < p_paid_until then 'active'
    when p_at < partner_subscription_grace_until(p_paid_until) then 'grace'
    else 'suspended'
  end;
$$;

create table if not exists partner_subscriptions (
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null references referral_profiles (ref_code),
  paid_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, ref_code)
);

create index if not exists idx_partner_subscriptions_due
  on partner_subscriptions (tenant_id, paid_until);

create table if not exists partner_payment_ledger (
  payment_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ref_code text not null,
  amount_minor bigint not null check (amount_minor > 0),
  currency text not null check (currency in ('RUB', 'WUSD')),
  access_months smallint not null default 3 check (access_months = 3),
  period_start timestamptz not null,
  period_end timestamptz not null,
  previous_paid_until timestamptz,
  source text not null check (source = 'telegram_manual'),
  telegram_chat_id bigint not null,
  telegram_message_id bigint not null,
  telegram_user_id bigint not null,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, ref_code)
    references partner_subscriptions (tenant_id, ref_code),
  unique (tenant_id, source, telegram_chat_id, telegram_message_id),
  check (period_end > period_start)
);

create index if not exists idx_partner_payment_ledger_partner_time
  on partner_payment_ledger (tenant_id, ref_code, created_at desc);

create table if not exists partner_payment_intents (
  intent_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ref_code text not null,
  amount_minor bigint not null check (amount_minor > 0),
  currency text not null check (currency in ('RUB', 'WUSD')),
  telegram_chat_id bigint not null,
  telegram_message_id bigint not null,
  telegram_user_id bigint not null,
  expires_at timestamptz not null,
  consumed_payment_id uuid references partner_payment_ledger (payment_id),
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, ref_code)
    references partner_subscriptions (tenant_id, ref_code),
  unique (tenant_id, telegram_chat_id, telegram_message_id),
  check (not (consumed_payment_id is not null and cancelled_at is not null))
);

create index if not exists idx_partner_payment_intents_expiry
  on partner_payment_intents (tenant_id, expires_at)
  where consumed_payment_id is null and cancelled_at is null;

create or replace function partner_subscription_ref_tenant_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1
    from public.referral_profiles rp
    where rp.ref_code = new.ref_code
      and rp.tenant_id = new.tenant_id
  ) then
    raise exception 'referral profile does not belong to subscription tenant'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_partner_subscription_ref_tenant_guard
  on partner_subscriptions;
create trigger trg_partner_subscription_ref_tenant_guard
before insert or update of tenant_id, ref_code on partner_subscriptions
for each row execute function partner_subscription_ref_tenant_guard();

alter table partner_subscriptions enable row level security;
alter table partner_payment_ledger enable row level security;
alter table partner_payment_intents enable row level security;

drop policy if exists partner_subscriptions_tenant_isolation
  on partner_subscriptions;
create policy partner_subscriptions_tenant_isolation on partner_subscriptions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_payment_ledger_tenant_isolation
  on partner_payment_ledger;
create policy partner_payment_ledger_tenant_isolation on partner_payment_ledger
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists partner_payment_intents_tenant_isolation
  on partner_payment_intents;
create policy partner_payment_intents_tenant_isolation on partner_payment_intents
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
