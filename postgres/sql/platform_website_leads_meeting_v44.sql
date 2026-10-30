-- WWC: заявка на Живую встречу — поля заявки v2 V44 (30.10.2026).
-- Код — app/leads/service.py (kind = meeting в POST /api/v1/leads), app/leads/meeting_leads.py
-- (поиск заявки по public_id и отметка «открыл бота» для /start lead_<public_id>).
--
-- Поля заявки website_leads (старые заявки остаются как были: lead_kind = 'request'):
--   lead_kind        — 'request' (заявка на товар/услугу, как раньше) или 'meeting' (запись на встречу).
--   phone_e164       — телефон человека в виде +79991234567; по нему ищется «первая заявка того же человека».
--   messenger_pref   — где человек хочет получить ссылку на встречу: telegram, max или whatsapp.
--   utm              — метки рекламы (source, medium, campaign, content, term), jsonb.
--   source_tag       — метка площадки из ссылки (латиница, цифры, - и _), до 64 знаков.
--   meeting_id       — встреча из таблицы meetings (V43); без внешнего ключа: встреча может быть
--                      стёрта, а заявка останется. Только если встреча живая, запланированная и в будущем.
--   person_lead_id   — первая заявка этого человека (тот же телефон у того же арендатора).
--                      Тип uuid, как website_leads.lead_id (lead_id у заявок — uuid, не bigint).
--   telegram_user_id — кто открыл бота по ссылке lead_<public_id> в Telegram.
--   max_user_id      — то же в Max.
--   bot_opened_at    — когда человек впервые открыл бота по этой ссылке.
--
-- website_leads — через `alter table if exists`: в testkit этой таблицы нет, на staging/бою она есть
-- (как в V17). Индексы создаются только если таблица есть. Идемпотентно: add column if not exists,
-- create index if not exists. Знака доллара в файле нет: боевые правки идут из контейнера Core
-- (/tmp/apply_sql.py) и через n8n, а он режет этот знак; поэтому блок do — обычной строкой.

begin;

set local lock_timeout = '5s';

alter table if exists website_leads
  add column if not exists lead_kind text not null default 'request'
    check (lead_kind in ('request', 'meeting'));
alter table if exists website_leads
  add column if not exists phone_e164 text;
alter table if exists website_leads
  add column if not exists messenger_pref text
    check (messenger_pref is null or messenger_pref in ('telegram', 'max', 'whatsapp'));
alter table if exists website_leads
  add column if not exists utm jsonb;
alter table if exists website_leads
  add column if not exists source_tag text;
alter table if exists website_leads
  add column if not exists meeting_id bigint;
alter table if exists website_leads
  add column if not exists person_lead_id uuid;
alter table if exists website_leads
  add column if not exists telegram_user_id bigint;
alter table if exists website_leads
  add column if not exists max_user_id bigint;
alter table if exists website_leads
  add column if not exists bot_opened_at timestamptz;

do 'begin
  if to_regclass(''website_leads'') is not null then
    create index if not exists idx_website_leads_phone
      on website_leads (tenant_id, phone_e164)
      where phone_e164 is not null;
    create index if not exists idx_website_leads_meeting
      on website_leads (tenant_id, meeting_id)
      where meeting_id is not null;
  end if;
end';

commit;
