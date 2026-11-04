-- V48, 04.11.2026 — акция ноября (владелец: «продлевай до конца ноября… там же по 40 в месяц, значит сейчас стоит не 15, а 11к»).
--   Октябрьский комплект: сайт 3 мес 30 + клуб 3 мес 100 + подключение 20 = 150 WWC$. Месяц клуба прошёл, минус 40:
--   сайт 3 мес 30 + клуб 2 мес 60 (вместо 80) + подключение 20 (вместо 30) = 110 WWC$ = 11 000 ₽. Цена комплекта — в коде (site_requests PLANS).
--   Здесь только то, что нужно базе: срок 2 месяца и тариф club_2m — прайс для ручной записи «клуб 60 WWC$ 2 акция».
--   В меню продления club_2m не попадает: там только сайт, пакет и полка (_OFFER_FILTER). Продление пакета — без изменений: 150 WWC$.
-- Повторный запуск ничего не ломает. Применить к общей базе один раз, записать в APPLIED.log.

alter table partner_subscription_plans drop constraint if exists partner_subscription_plans_access_months_check;
alter table partner_subscription_plans add constraint partner_subscription_plans_access_months_check
  check (access_months in (0, 1, 2, 3, 6, 12));
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_access_months_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_access_months_check
  check (access_months in (0, 1, 2, 3, 6, 12));
alter table partner_payment_intents drop constraint if exists partner_payment_intents_access_months_check;
alter table partner_payment_intents add constraint partner_payment_intents_access_months_check
  check (access_months in (0, 1, 2, 3, 6, 12));

insert into partner_subscription_plans (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor)
values ('whieda', 'club_2m', 'club_subscription', 2, 8000, 800000)
on conflict (tenant_id, plan_code) do update
  set price_wusd_minor = excluded.price_wusd_minor, price_rub_minor = excluded.price_rub_minor;
