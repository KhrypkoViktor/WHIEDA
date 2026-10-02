-- V18, 02.10.2026 — цены с акции октября (владелец). 1 WWC$ = 100 ₽, суммы в minor (×100).
--   подключение сайта (site_setup)      20 → 30 WWC$
--   пакет «Платформа + Клуб», 3 мес     105 → 150 WWC$: новому партнёру — сайт 30 + клуб 100
--                                       + подключение 20 (акция), при продлении — сайт 30 + клуб 120
--   клуб, пробный месяц (club_1m)       новый тариф 40 WWC$; поэтому access_months допускает 1
-- Повторный запуск ничего не ломает. Применить к общей базе один раз, записать в APPLIED.log.

alter table partner_subscription_plans drop constraint if exists partner_subscription_plans_access_months_check;
alter table partner_subscription_plans add constraint partner_subscription_plans_access_months_check
  check (access_months in (0, 1, 3, 6, 12));
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_access_months_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_access_months_check
  check (access_months in (0, 1, 3, 6, 12));
alter table partner_payment_intents drop constraint if exists partner_payment_intents_access_months_check;
alter table partner_payment_intents add constraint partner_payment_intents_access_months_check
  check (access_months in (0, 1, 3, 6, 12));

update partner_subscription_plans set price_wusd_minor = 3000, price_rub_minor = 300000
 where tenant_id = 'whieda' and plan_code = 'site_setup';
update partner_subscription_plans set price_wusd_minor = 15000, price_rub_minor = 1500000
 where tenant_id = 'whieda' and plan_code = 'bundle_pro_club_3m';
insert into partner_subscription_plans (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor)
values ('whieda', 'club_1m', 'club_subscription', 1, 4000, 400000)
on conflict (tenant_id, plan_code) do update
  set price_wusd_minor = excluded.price_wusd_minor, price_rub_minor = excluded.price_rub_minor;
