-- WWC: цель в трекере привычек V53 (12.11.2026).
-- ТЗ: Obsidian Продвижение/Кухня_Клод/ТЗ/17_ТЗ_Трекер_привычек.md §3.5, §5.5, §6–7; код — app/habits/.
--
--   habit_goals — цель человека: текст (до 80 знаков), день начала и срок (даты по поясу человека),
--                 ответ «Да/Нет» (outcome: achieved / missed) и день ответа (answered_on).
--                 Одна цель без ответа на человека — частичный уникальный индекс. Ответ закрывает цель,
--                 она остаётся в базе как история; «снять» цель без ответа — мягко, через deleted_at.
--
-- «Осталось», «прошло», «всего» и процент не хранятся: считаются на запросе по местному дню человека.
-- Только добавление: create if not exists и пересоздание своей политики, существующие таблицы не трогаем,
-- повторный прогон безопасен. Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists habit_goals (
  goal_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  telegram_user_id bigint not null,
  title text not null check (char_length(title) between 1 and 80),
  start_day date not null,
  due_day date not null,
  outcome text check (outcome is null or outcome in ('achieved', 'missed')),
  answered_on date,
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (due_day >= start_day),
  check ((outcome is null and answered_on is null) or (outcome is not null and answered_on is not null))
);

create unique index if not exists habit_goals_one_open
  on habit_goals (tenant_id, telegram_user_id)
  where outcome is null and deleted_at is null;

create index if not exists habit_goals_person
  on habit_goals (tenant_id, telegram_user_id, created_at desc);

alter table habit_goals enable row level security;

drop policy if exists habit_goals_tenant_isolation on habit_goals;
create policy habit_goals_tenant_isolation on habit_goals
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
