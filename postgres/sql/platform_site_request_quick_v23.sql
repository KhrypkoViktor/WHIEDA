-- WWC partner site request: short path V23 (08.10.2026).
--
-- Владелец, 08.10.2026: анкета «Заказать сайт» застревала — Наталья (sabimama)
-- не хотела писать о себе, на «Наталья» бот снова и снова просил 2–3 предложения,
-- голосовое не принимал. Обязательны только имя с фамилией и адрес сайта; фото,
-- текст и контакты — кнопкой «Пропустить» (стандартное описание, фото позже).
--
-- partner_name         — имя и фамилия для сайта (новый шаг awaiting_name после страны);
-- intro_voice_file_id  — голосовое вместо текста о себе: текст расшифруем сами.
-- Аддитивно: старый код новые колонки и статус не трогает.

begin;

alter table partner_site_requests
  add column if not exists partner_name text,
  add column if not exists intro_voice_file_id text;

alter table partner_site_requests
  drop constraint if exists partner_site_requests_partner_name_check;
alter table partner_site_requests
  add constraint partner_site_requests_partner_name_check
  check (partner_name is null or length(partner_name) between 2 and 120);

alter table partner_site_requests
  drop constraint if exists partner_site_requests_status_check;
alter table partner_site_requests
  add constraint partner_site_requests_status_check check (status in (
    'awaiting_country', 'awaiting_name', 'awaiting_subdomain', 'awaiting_photo', 'awaiting_text',
    'awaiting_contacts', 'awaiting_plan', 'awaiting_payment', 'pending_confirmation',
    'pending_provisioning', 'provisioned', 'rejected', 'cancelled'
  ));

commit;
