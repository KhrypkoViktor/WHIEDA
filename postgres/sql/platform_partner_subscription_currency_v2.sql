-- Replace the obsolete BYN subscription-payment currency with WHIEDA dollars.
-- WUSD is the database-safe representation of the user-facing W$ label.

begin;

alter table if exists partner_payment_ledger
  drop constraint if exists partner_payment_ledger_currency_check;
alter table if exists partner_payment_ledger
  add constraint partner_payment_ledger_currency_check
  check (currency in ('RUB', 'WUSD'));

alter table if exists partner_payment_intents
  drop constraint if exists partner_payment_intents_currency_check;
alter table if exists partner_payment_intents
  add constraint partner_payment_intents_currency_check
  check (currency in ('RUB', 'WUSD'));

commit;
