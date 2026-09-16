-- Gemini (and future services) sales through the support tunnel: tariff,
-- one record per sale, the administrator's prepaid deposit, and the partner's
-- WWC$ share (owner, 15–16.09.2026). Additive, idempotent.
--
-- Money goes one way: the client pays the owner, the owner pays the service
-- administrator from a deposit. The partner who sold gets WWC$ only.
begin;

create table if not exists service_tariffs (
  tenant_id text not null references tenants (tenant_id),
  offer_code text not null,
  retail_minor bigint not null check (retail_minor > 0),
  -- what the owner owes the administrator per licence
  wholesale_direct_minor bigint not null check (wholesale_direct_minor >= 0),
  wholesale_partner_minor bigint not null check (wholesale_partner_minor >= 0),
  -- the selling partner's share, in WWC$ minor units (100 = 1 WWC$)
  partner_share_wusd_minor bigint not null check (partner_share_wusd_minor >= 0),
  updated_by_telegram_user_id bigint,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, offer_code)
);

insert into service_tariffs (tenant_id, offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor, partner_share_wusd_minor)
values
  ('whieda', 'gemini_6m', 399000, 299000, 249000, 500),
  ('whieda', 'gemini_18m', 399000, 299000, 249000, 500)
on conflict (tenant_id, offer_code) do nothing;

create table if not exists service_sales (
  sale_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ticket_id uuid not null references support_tickets (ticket_id),
  offer_code text not null,
  client_actor_id text,
  seller text not null check (seller in ('owner', 'partner')),
  partner_ref text,
  retail_minor bigint not null,
  owed_admin_minor bigint not null,
  partner_share_wusd_minor bigint not null default 0,
  paid_currency text not null default 'RUB',
  paid_at timestamptz not null default now(),
  activated_until timestamptz,
  status text not null default 'paid' check (status in ('paid', 'activated', 'closed')),
  created_by_telegram_user_id bigint,
  created_at timestamptz not null default now(),
  -- the «Оплачено» button is idempotent per ticket
  unique (tenant_id, ticket_id)
);

create index if not exists idx_service_sales_partner
  on service_sales (tenant_id, partner_ref, paid_at desc);

-- Deposit ledger with the administrator: top-ups (+, confirmed by the admin)
-- and sales (−, automatic). Balance = sum of confirmed rows.
create table if not exists service_admin_deposit (
  entry_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  admin_telegram_user_id bigint not null,
  kind text not null check (kind in ('topup', 'sale')),
  amount_minor bigint not null check (amount_minor <> 0),
  sale_id uuid references service_sales (sale_id),
  sent_by_telegram_user_id bigint,
  confirmed_by_telegram_user_id bigint,
  confirmed_at timestamptz,
  created_at timestamptz not null default now()
);

create unique index if not exists uq_service_admin_deposit_sale
  on service_admin_deposit (tenant_id, sale_id)
  where sale_id is not null;

-- «За неделю до окончания напомним» — once per sale.
alter table service_sales add column if not exists reminded_at timestamptz;

-- Daily notices (low deposit) — one per key per day.
create table if not exists service_notice_log (
  tenant_id text not null references tenants (tenant_id),
  notice_key text not null,
  sent_on date not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, notice_key, sent_on)
);

-- Partner e-mail: the nameless identifier in the administrator's group.
alter table lead_actors add column if not exists email text;

-- Service topics in the administrator's forum group.
alter table support_forums add column if not exists bonuses_thread_id bigint;
alter table support_forums add column if not exists reports_thread_id bigint;

alter table service_tariffs enable row level security;
drop policy if exists service_tariffs_tenant_isolation on service_tariffs;
create policy service_tariffs_tenant_isolation on service_tariffs
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table service_sales enable row level security;
drop policy if exists service_sales_tenant_isolation on service_sales;
create policy service_sales_tenant_isolation on service_sales
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table service_notice_log enable row level security;
drop policy if exists service_notice_log_tenant_isolation on service_notice_log;
create policy service_notice_log_tenant_isolation on service_notice_log
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table service_admin_deposit enable row level security;
drop policy if exists service_admin_deposit_tenant_isolation on service_admin_deposit;
create policy service_admin_deposit_tenant_isolation on service_admin_deposit
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
