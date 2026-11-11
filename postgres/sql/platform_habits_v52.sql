-- WWC: трекер привычек в приложении /app/ V52 (11.11.2026).
-- ТЗ: Obsidian Продвижение/Готово/17_ТЗ_Трекер_привычек.md §4–7; код — app/habits/.
--
--   habits         — привычка человека: ключ шаблона, ритм (daily / weekly / monthly), цель,
--                    параметры (литры воды, страниц в день), день добавления по поясу человека,
--                    статус active / paused, мягкое удаление (deleted_at). Название шаблонной
--                    привычки не хранится — только ключ; title нужен лишь для «своей».
--   habit_marks    — отметка: есть строка — сделано в этот день. Снять отметку — удалить строку.
--   habit_pauses   — интервалы паузы: from_day включительно, to_day — день «Продолжить»
--                    (с него привычка снова идёт), null — пауза идёт.
--   habit_profile  — часовой пояс человека (IANA); больше ничего о человеке не хранится.
--
-- Серия, «прошло», «выполнено», «пропущено» не хранятся: считаются на лету из отметок и пауз.
-- Только create if not exists и пересоздание своих политик: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists habits (
  habit_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  telegram_user_id bigint not null,
  template text not null
    check (template in ('water', 'warmup', 'cardio', 'strength', 'reading', 'book', 'calls', 'meetings', 'custom')),
  title text
    check (title is null or char_length(title) between 1 and 40),
  rhythm text not null check (rhythm in ('daily', 'weekly', 'monthly')),
  goal smallint not null,
  params jsonb not null default '{}'::jsonb check (jsonb_typeof(params) = 'object'),
  start_day date not null,
  rhythm_changed_on date,
  status text not null default 'active' check (status in ('active', 'paused')),
  deleted_at timestamptz,
  position smallint not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (rhythm = 'daily' and goal = 1)
    or (rhythm = 'weekly' and goal between 1 and 7)
    or (rhythm = 'monthly' and goal = 1)
  )
);

create index if not exists habits_person
  on habits (tenant_id, telegram_user_id)
  where deleted_at is null;

create table if not exists habit_marks (
  habit_id uuid not null references habits (habit_id) on delete cascade,
  day date not null,
  tenant_id text not null,
  telegram_user_id bigint not null,
  created_at timestamptz not null default now(),
  primary key (habit_id, day)
);

create index if not exists habit_marks_habit_day
  on habit_marks (habit_id, day desc);

create table if not exists habit_pauses (
  habit_id uuid not null references habits (habit_id) on delete cascade,
  from_day date not null,
  to_day date,
  tenant_id text not null,
  primary key (habit_id, from_day)
);

create table if not exists habit_profile (
  tenant_id text not null,
  telegram_user_id bigint not null,
  tz text not null,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id)
);

alter table habits enable row level security;
alter table habit_marks enable row level security;
alter table habit_pauses enable row level security;
alter table habit_profile enable row level security;

drop policy if exists habits_tenant_isolation on habits;
create policy habits_tenant_isolation on habits
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists habit_marks_tenant_isolation on habit_marks;
create policy habit_marks_tenant_isolation on habit_marks
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists habit_pauses_tenant_isolation on habit_pauses;
create policy habit_pauses_tenant_isolation on habit_pauses
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists habit_profile_tenant_isolation on habit_profile;
create policy habit_profile_tenant_isolation on habit_profile
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
