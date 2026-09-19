-- WWC partner site requests V10: выбор пакета в диалоге заказа (19.09.2026).
-- Владелец: в боте предлагался только «сайт + настройка»; пакет «Платформа + Клуб»
-- 105 WWC$ продавался вручную. Теперь после текста «о себе» — шаг awaiting_plan:
--   site   — PRO 3 мес + настройка сайта (30 + 20 WWC$ / 3 000 + 2 000 ₽)
--   bundle — PRO 3 мес + клуб 3 мес (105 WWC$ / 10 500 ₽, настройка в подарок)
-- Идемпотентно: повторный запуск ничего не меняет.

begin;

alter table partner_site_requests
  add column if not exists plan_code text not null default 'site';

alter table partner_site_requests drop constraint if exists partner_site_requests_plan_code_check;
alter table partner_site_requests
  add constraint partner_site_requests_plan_code_check check (plan_code in ('site', 'bundle'));

alter table partner_site_requests drop constraint if exists partner_site_requests_status_check;
alter table partner_site_requests
  add constraint partner_site_requests_status_check check (status in (
    'awaiting_country', 'awaiting_subdomain', 'awaiting_photo', 'awaiting_text', 'awaiting_plan',
    'awaiting_payment', 'pending_confirmation', 'pending_provisioning',
    'provisioned', 'rejected', 'cancelled'
  ));

commit;
