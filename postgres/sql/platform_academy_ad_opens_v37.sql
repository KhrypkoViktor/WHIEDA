-- WWC Academy: кто открыл бота витрины по ссылке с рекламы V37 (17.10.2026).
-- Код — app/academy/ad_leads.py, app/telegram/showcase_bot.py.
--
--   academy_ad_opens — одна строка на человека и витрину: /start ig_<метка> в боте витрины.
--                      Алерт администратору о заявке из таблицы Meta ставит отметку «бот открыт»
--                      по нику или имени из этой таблицы; lead_id — строка таблицы, с которой
--                      сопоставили при открытии (может быть пустым: строка пришла позже).
--
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists academy_ad_opens (
  tenant_id text not null,
  showcase_id text not null,
  telegram_user_id bigint not null,
  telegram_username text,
  name text not null default '',
  tag text not null,
  lead_id text,
  opened_at timestamptz not null default now(),
  primary key (tenant_id, showcase_id, telegram_user_id)
);

alter table academy_ad_opens enable row level security;

drop policy if exists academy_ad_opens_tenant_isolation on academy_ad_opens;
create policy academy_ad_opens_tenant_isolation on academy_ad_opens
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
