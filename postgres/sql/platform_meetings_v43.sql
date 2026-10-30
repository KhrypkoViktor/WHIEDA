-- WWC: таблица встреч — живые встречи и консультации V43 (30.10.2026).
-- Код — app/meetings/service.py (список, создание, ссылка, отмена, «прошла»),
-- app/telegram/meetings_admin.py (команды владельца в боте), app/meetings/routes.py (публичный список).
--
--   meetings — одна строка на одну встречу.
--   kind        — 'live' (живая встреча для всех) или 'consult' (консультация).
--   starts_at   — момент начала, всегда timestamptz (хранится в UTC); показ и разбор команд — по Москве.
--   duration_min — длительность в минутах.
--   join_url    — ссылка входа в Zoom; наружу, в публичный список, НЕ отдаётся никогда.
--   hosts       — имена ведущих (массив текста), для публичной карточки.
--   status      — 'planned' (по умолчанию), 'done' (прошла), 'cancelled' (отменена).
--   recording_url, notes — запись и заметки владельца; в публичный список не попадают.
--   created_by  — кто создал: 'telegram:<id>' из бота.
--
-- Один арендатор видит только свои строки (RLS, как у соседних таблиц).
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists meetings (
  meeting_id bigserial primary key,
  tenant_id text not null,
  kind text not null check (kind in ('live', 'consult')),
  title text,
  starts_at timestamptz not null,
  duration_min int not null default 60 check (duration_min between 1 and 1440),
  join_url text,
  hosts text[] not null default '{}',
  status text not null default 'planned' check (status in ('planned', 'done', 'cancelled')),
  recording_url text,
  notes text,
  created_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_meetings_tenant_starts
  on meetings (tenant_id, starts_at);

alter table meetings enable row level security;

drop policy if exists meetings_tenant_isolation on meetings;
create policy meetings_tenant_isolation on meetings
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
