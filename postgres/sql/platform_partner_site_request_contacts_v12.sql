-- WWC partner site request: contacts step V12 (24.09.2026).
--
-- Партнёры присылали контакты для сайта отдельно, в личку владельцу, одним
-- сообщением: Telegram, WhatsApp, MAX, телефон, иногда почту, ВКонтакте,
-- ссылки на свои каналы и группы. Анкета в боте их не спрашивала. Новый шаг
-- «awaiting_contacts» идёт после текста о себе и перед выбором пакета.
--
-- contacts_text — сообщение как есть (источник правды для владельца);
-- contacts      — разбор по полям (телефон, WhatsApp, MAX, e-mail, ссылки);
--                 разбор подсказка, а не замена исходника.

begin;

alter table partner_site_requests
  add column if not exists contacts_text text,
  add column if not exists contacts jsonb not null default '{}'::jsonb;

alter table partner_site_requests
  drop constraint if exists partner_site_requests_contacts_text_check;
alter table partner_site_requests
  add constraint partner_site_requests_contacts_text_check
  check (contacts_text is null or length(contacts_text) <= 2000);

alter table partner_site_requests
  drop constraint if exists partner_site_requests_status_check;
alter table partner_site_requests
  add constraint partner_site_requests_status_check check (status in (
    'awaiting_country', 'awaiting_subdomain', 'awaiting_photo', 'awaiting_text',
    'awaiting_contacts', 'awaiting_plan', 'awaiting_payment', 'pending_confirmation',
    'pending_provisioning', 'provisioned', 'rejected', 'cancelled'
  ));

commit;
