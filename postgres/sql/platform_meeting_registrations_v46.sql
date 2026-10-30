-- WWC: запись на Живую встречу V46 (30.10.2026).
-- Код — app/meetings/registrations.py (запись, перенос, отмена), app/funnel/ (старт lead_<public_id>,
-- режим гостя, кнопки «Записаться / Перенести / Отменить запись» в Telegram и Max).
--
--   meeting_registrations — одна строка на одну запись человека на одну встречу.
--   meeting_id       — встреча из таблицы meetings (V43); без внешнего ключа, как meeting_id у заявки (V44).
--   lead_id          — заявка website_leads.lead_id (uuid), если человек пришёл из заявки; пусто, если нет
--                      (например, записался из бота после показа системы). Без внешнего ключа: в testkit
--                      таблицы website_leads нет.
--   channel          — 'telegram' или 'max': где человек записался и где ему писать.
--   channel_user_id  — id человека в этом мессенджере (число).
--   status           — registered (записан) | moved (перенёс на другую встречу) | cancelled (отменил)
--                      | joined (нажал «Присоединиться») | attended (был) | no_show (не пришёл).
--   registered_at    — когда записался; joined_at — когда нажал «Присоединиться».
--   moved_from       — при переносе: registration_id прежней записи (она получает status = 'moved').
--   reminders_sent   — какие напоминания уже ушли, jsonb (ключ — окно, значение — время отправки).
--
-- Одна активная запись на (арендатор, встреча, мессенджер, человек): активная — любая, кроме moved и cancelled.
-- Отменённую или перенесённую можно создать заново на ту же встречу.
--
-- Дополнительно к таблице: два частичных индекса на website_leads (tenant_id, telegram_user_id) и
-- (tenant_id, max_user_id) — по ним режим гостя ищет заявку человека при каждом «/start» и «меню».
-- Индексы создаются только если таблица и обе колонки (V44) уже есть; в testkit таблицы нет.
--
-- Один арендатор видит только свои строки (RLS, как у соседних таблиц).
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py) и через n8n,
-- а он режет этот знак; поэтому блок do — обычной строкой.

begin;

set local lock_timeout = '5s';

create table if not exists meeting_registrations (
  registration_id bigserial primary key,
  tenant_id text not null,
  meeting_id bigint not null,
  lead_id uuid,
  channel text not null check (channel in ('telegram', 'max')),
  channel_user_id bigint not null,
  status text not null default 'registered'
    check (status in ('registered', 'moved', 'cancelled', 'joined', 'attended', 'no_show')),
  registered_at timestamptz not null default now(),
  joined_at timestamptz,
  moved_from bigint,
  reminders_sent jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_meeting_registrations_active
  on meeting_registrations (tenant_id, meeting_id, channel, channel_user_id)
  where status not in ('moved', 'cancelled');

create index if not exists idx_meeting_registrations_meeting_status
  on meeting_registrations (tenant_id, meeting_id, status);

create index if not exists idx_meeting_registrations_person
  on meeting_registrations (tenant_id, channel, channel_user_id);

alter table meeting_registrations enable row level security;

drop policy if exists meeting_registrations_tenant_isolation on meeting_registrations;
create policy meeting_registrations_tenant_isolation on meeting_registrations
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

do 'begin
  if to_regclass(''website_leads'') is not null
     and exists (
       select 1 from information_schema.columns
       where table_name = ''website_leads'' and column_name = ''telegram_user_id''
     )
     and exists (
       select 1 from information_schema.columns
       where table_name = ''website_leads'' and column_name = ''max_user_id''
     ) then
    create index if not exists idx_website_leads_tg_user
      on website_leads (tenant_id, telegram_user_id)
      where telegram_user_id is not null;
    create index if not exists idx_website_leads_max_user
      on website_leads (tenant_id, max_user_id)
      where max_user_id is not null;
  end if;
end';

commit;
