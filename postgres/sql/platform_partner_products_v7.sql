-- Партнёрские продукты помимо PRO: клуб, настройка сайта, пакет PRO+клуб;
-- персональные цены; один полученный платёж на несколько строк.
-- Платформа (PRO) остаётся в partner_subscriptions. Аддитивно, идемпотентно.
--
-- Цены владельца (14–15.09.2026): PRO 3 мес 30 WWC$ / 3 000 ₽; клуб 40 WWC$/мес →
-- 3 мес 120 WWC$ / 12 000 ₽; настройка сайта 20 WWC$ / 2 000 ₽ разово; пакет
-- PRO + клуб 105 WWC$ / 10 500 ₽ (клуб в пакете 75 — акционная цена).
-- 1 WWC$ = 100 ₽. Бонус рефереру только со строки PRO (правило reward_rules).
begin;

-- 1. Планы: разовая услуга = access_months 0.
alter table partner_subscription_plans drop constraint if exists partner_subscription_plans_access_months_check;
alter table partner_subscription_plans add constraint partner_subscription_plans_access_months_check
  check (access_months in (0, 3, 6, 12));
insert into partner_subscription_plans (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor)
values
  ('whieda', 'club_3m', 'club_subscription', 3, 12000, 1200000),
  ('whieda', 'site_setup', 'site_setup', 0, 2000, 200000),
  ('whieda', 'bundle_pro_club_3m', 'bundle_pro_club', 3, 10500, 1050000)
on conflict (tenant_id, plan_code) do nothing;

-- 2. Срок доступа к продуктам со сроком, кроме PRO (сейчас — клуб).
create table if not exists partner_product_access (
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  product_code text not null check (product_code in ('club_subscription')),
  paid_until timestamptz,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, ref_code, product_code),
  foreign key (tenant_id, ref_code) references partner_subscriptions (tenant_id, ref_code)
);
alter table partner_product_access enable row level security;
drop policy if exists partner_product_access_tenant_isolation on partner_product_access;
create policy partner_product_access_tenant_isolation on partner_product_access
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 3. Персональная цена: действует до отзыва. Одна активная на партнёра и продукт.
create table if not exists partner_price_overrides (
  override_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  product_code text not null,
  price_wusd_minor bigint not null check (price_wusd_minor >= 0),
  reason text not null,
  approved_by_telegram_user_id bigint not null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
-- ВНИМАНИЕ: частичный индекс. Любой ON CONFLICT по нему обязан повторять
-- предикат `where revoked_at is null` (урок 2026-09-12).
create unique index if not exists uq_partner_price_overrides_active
  on partner_price_overrides (tenant_id, ref_code, product_code) where revoked_at is null;
alter table partner_price_overrides enable row level security;
drop policy if exists partner_price_overrides_tenant_isolation on partner_price_overrides;
create policy partner_price_overrides_tenant_isolation on partner_price_overrides
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 4. Заголовок платежа: сколько реально получено одним переводом.
create table if not exists partner_payments (
  received_payment_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  received_amount_minor bigint not null check (received_amount_minor >= 0),
  currency text not null check (currency in ('RUB', 'WUSD')),
  source text not null default 'telegram_manual',
  telegram_chat_id bigint not null,
  telegram_message_id bigint not null,
  telegram_user_id bigint not null,
  lines_fingerprint text not null,
  note text,
  created_at timestamptz not null default now(),
  unique (tenant_id, source, telegram_chat_id, telegram_message_id)
);
alter table partner_payments enable row level security;
drop policy if exists partner_payments_tenant_isolation on partner_payments;
create policy partner_payments_tenant_isolation on partner_payments
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 5. Строки платежа: одна запись ledger на продукт внутри одного сообщения.
alter table partner_payment_ledger
  add column if not exists received_payment_id uuid references partner_payments (received_payment_id),
  add column if not exists promo_note text,
  add column if not exists list_price_minor bigint;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_tenant_id_source_telegram_chat_id_te_key;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_tenant_id_source_telegram_chat_id_telegram_message_id_key;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_one_line_per_product;
alter table partner_payment_ledger add constraint partner_payment_ledger_one_line_per_product
  unique (tenant_id, source, telegram_chat_id, telegram_message_id, product_code);
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_amount_minor_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_amount_minor_check check (amount_minor >= 0);
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_access_months_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_access_months_check check (access_months in (0, 3, 6, 12));
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_check;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_period_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_period_check check (period_end >= period_start);

-- 6. Разобранные строки многострочной команды хранятся в intent до подтверждения.
alter table partner_payment_intents add column if not exists lines jsonb;
alter table partner_payment_intents drop constraint if exists partner_payment_intents_access_months_check;
alter table partner_payment_intents add constraint partner_payment_intents_access_months_check check (access_months in (0, 3, 6, 12));

-- 7. Напоминания клуба используют тот же журнал.
alter table partner_subscription_reminder_log drop constraint if exists partner_subscription_reminder_log_event_type_check;
alter table partner_subscription_reminder_log add constraint partner_subscription_reminder_log_event_type_check
  check (event_type in ('due_7d', 'grace_start', 'grace_last', 'club_due_7d', 'club_due_3d', 'club_due_1d'));

commit;
