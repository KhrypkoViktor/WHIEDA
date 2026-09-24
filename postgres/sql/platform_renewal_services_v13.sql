-- WWC partner renewal: all services, not only the site. V13 (24.09.2026).
--
-- Диалог продления в боте умел только «платформу на 3/6/12 месяцев». Партнёры
-- платят пакетом «сайт + клуб» (105 WUSD / 10 500 ₽), а бот записывал 30 WUSD
-- за сайт и не знал про клуб. Дальше появятся курсы — разные, с разной ценой.
--
-- Каталог услуг — это partner_subscription_plans. Чтобы добавить курс, хватит
-- одной строки: plan_code, product_code 'course_<slug>', access_months = 0,
-- цены и title. Код бота и схему трогать не нужно.
--
-- Клуб отдельно не продаётся (владелец: «клуб без сайта не может быть»),
-- только пакетом с сайтом.

begin;

alter table partner_subscription_plans
  add column if not exists title text,
  add column if not exists sort_order integer not null default 100;

update partner_subscription_plans set title = v.title, sort_order = v.sort_order, updated_at = now()
from (values
  ('platform_3m',        'Сайт на 3 месяца',          10),
  ('platform_6m',        'Сайт на 6 месяцев',         20),
  ('platform_12m',       'Сайт на 12 месяцев',        30),
  ('bundle_pro_club_3m', 'Сайт + Клуб на 3 месяца',   40),
  ('course_academy',     'Курс Академии WWC',         100),
  ('club_3m',            'Клуб на 3 месяца',          900),
  ('site_setup',         'Подключение сайта',         900)
) as v(plan_code, title, sort_order)
where partner_subscription_plans.plan_code = v.plan_code;

-- Что именно оплачивается в заявке. NULL — старые заявки: только сайт на access_months.
alter table partner_renewal_requests
  add column if not exists plan_code text;

-- Курс — разовая покупка без срока: access_months = 0.
alter table partner_renewal_requests
  drop constraint if exists partner_renewal_requests_access_months_check;
alter table partner_renewal_requests
  add constraint partner_renewal_requests_access_months_check
  check (access_months in (0, 3, 6, 12));

-- Доступы: клуб и любой курс вида course_<slug>, а не только course_academy.
alter table partner_product_access
  drop constraint if exists partner_product_access_product_code_check;
alter table partner_product_access
  add constraint partner_product_access_product_code_check
  check (product_code = 'club_subscription' or product_code ~ '^course_[a-z0-9_]+$');

commit;
