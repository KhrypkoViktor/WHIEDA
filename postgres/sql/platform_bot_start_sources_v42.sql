-- WWC: откуда человек пришёл в бота — метка в ссылке /start V42 (30.10.2026).
-- Код — app/leads/start_sources.py, app/telegram/processor.py, app/max/processor.py.
--
--   bot_start_sources — одна строка на каждый старт с параметром: Telegram t.me/<бот>?start=src_<метка>[_<код>]
--                       и Max max.ru/<бот>?start=src_<метка>[_<код>]. Незнакомый параметр без src_ пишется
--                       с tag = 'unknown' (само значение не хранится).
--   tag               — строчные латинские буквы, цифры, дефис, до 32 знаков (vk, ok, dzen, max, youtube, instagram …).
--   ref_code          — код партнёра из ссылки, если он найден (referral_profiles.ref_code); иначе пусто.
--   first_seen        — true, если бот не знал этого человека до старта (нет строки в lead_actors /
--                       lead_actor_channels и нет прежней строки в этой таблице). «Новые люди по метке» = first_seen.
--   actor_id          — lead_actors.actor_id, если человек уже был известен или создан на этом старте; иначе пусто.
--
-- Персональных данных нет: только id в канале, метка и время. Имена и ники сюда не пишутся.
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists bot_start_sources (
  start_id bigserial primary key,
  tenant_id text not null,
  channel text not null check (channel in ('telegram', 'max')),
  channel_user_id text not null,
  actor_id text,
  tag text not null check (char_length(tag) between 1 and 32 and tag !~ '[^a-z0-9-]'),
  ref_code text,
  first_seen boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists idx_bot_start_sources_tag_time
  on bot_start_sources (tenant_id, tag, created_at);

create index if not exists idx_bot_start_sources_person
  on bot_start_sources (tenant_id, channel, channel_user_id, created_at);

alter table bot_start_sources enable row level security;

drop policy if exists bot_start_sources_tenant_isolation on bot_start_sources;
create policy bot_start_sources_tenant_isolation on bot_start_sources
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
