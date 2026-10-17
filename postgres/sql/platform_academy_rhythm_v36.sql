-- WWC Academy: ритм курса V36 (17.10.2026) — карточка «Сегодня» в боте и табло недели.
-- Решение лида: Obsidian Online_start/RITM_resheniye.md §2–3; код — app/academy/rhythm.py,
-- app/telegram/rhythm.py.
--
--   academy_daily_checkins — одна строка на ученика в день (день по Минску): три галочки
--                            «пост или видео», «один человек», «пятёрка»; done_at — когда
--                            стали все три (снятая галочка возвращает null). Серия и табло
--                            считаются из этих строк. Человек — telegram_user_id, как в
--                            academy_progress.
--
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists academy_daily_checkins (
  tenant_id text not null,
  telegram_user_id bigint not null,
  day date not null,
  post boolean not null default false,
  person boolean not null default false,
  five boolean not null default false,
  done_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id, day)
);

-- Табло недели: засчитанные дни всех учеников за период.
create index if not exists academy_daily_checkins_day
  on academy_daily_checkins (tenant_id, day);

alter table academy_daily_checkins enable row level security;

drop policy if exists academy_daily_checkins_tenant_isolation on academy_daily_checkins;
create policy academy_daily_checkins_tenant_isolation on academy_daily_checkins
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
