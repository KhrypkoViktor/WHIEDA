-- WWC partner site request: основной язык сайта и визитки V28 (10.10.2026).
--
-- Владелец, 10.10.2026: «немцам — перевод на немецкий всех визиток, Ольге Манько —
-- на английском; нужно просто при регистрации в боте давать выбор основного языка для
-- визитки и сайта». Новый шаг анкеты awaiting_lang — после адреса, до фото: Русский,
-- English, Deutsch. Язык уходит в реестр сайта (src/data/referrals.js, поле lang):
-- поддомен партнёра открывается на нём, визитка и ссылки «отправить» — тоже.
--
-- site_lang — ru | en | de, по умолчанию ru (все прежние заявки — русский сайт).
-- Аддитивно: старый код колонку не трогает и шаг не ставит.

begin;

alter table partner_site_requests
  add column if not exists site_lang text not null default 'ru';

alter table partner_site_requests
  drop constraint if exists partner_site_requests_site_lang_check;
alter table partner_site_requests
  add constraint partner_site_requests_site_lang_check
  check (site_lang in ('ru', 'en', 'de'));

alter table partner_site_requests
  drop constraint if exists partner_site_requests_status_check;
alter table partner_site_requests
  add constraint partner_site_requests_status_check check (status in (
    'awaiting_country', 'awaiting_name', 'awaiting_subdomain', 'awaiting_lang', 'awaiting_photo', 'awaiting_text',
    'awaiting_contacts', 'awaiting_plan', 'awaiting_payment', 'pending_confirmation',
    'pending_provisioning', 'provisioned', 'rejected', 'cancelled'
  ));

commit;
