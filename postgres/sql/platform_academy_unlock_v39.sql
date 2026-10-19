-- WWC Academy: мини-курс открывается за «Запуск» V39 (19.10.2026) — тест сдан + приложение стоит.
-- ТЗ: PROCESS/wwc-mini-courses-20261016/TZ_UNLOCK.md (пункты 2-5, Core).
--
--   academy_app_installs   — человек открыл сайт как установленное приложение WWC (PWA, standalone):
--                            сайт сам шлёт отметку (POST /academy/app/installed), раз в сутки на
--                            устройство. first_seen_at — первая отметка, last_seen_at — последняя,
--                            platform — android | ios | desktop или null (неизвестно). Человек —
--                            telegram_user_id, как в academy_progress.
--   academy_unlock_notices — сообщение в бот «мини-курс открыт бесплатно» уже ушло: одна строка на
--                            (курс, человек), пишется только после успешной постановки сообщения
--                            в очередь отправки, поэтому второго сообщения не будет.
--
-- Правило открытия живёт в academy_courses.free_for (V35, jsonb без новых колонок):
--   {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}, "app": true}}
-- Форму проверяет код (app.academy.author.parse_free_for), база - только что это объект.
-- Пауза пересдачи теста (ключ retry_after_min в quiz_json) тоже без колонок: academy_quiz_attempts
-- уже хранит created_at каждой попытки (V35).
--
-- Только create if not exists и пересоздание своих проверок и политик: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists academy_app_installs (
  tenant_id text not null,
  telegram_user_id bigint not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  platform text,
  primary key (tenant_id, telegram_user_id)
);

alter table academy_app_installs drop constraint if exists academy_app_installs_platform_check;
alter table academy_app_installs add constraint academy_app_installs_platform_check
  check (platform is null or platform in ('android', 'ios', 'desktop'));

alter table academy_app_installs enable row level security;

drop policy if exists academy_app_installs_tenant_isolation on academy_app_installs;
create policy academy_app_installs_tenant_isolation on academy_app_installs
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

create table if not exists academy_unlock_notices (
  tenant_id text not null,
  course_id uuid not null,
  telegram_user_id bigint not null,
  sent_at timestamptz not null default now(),
  primary key (tenant_id, course_id, telegram_user_id),
  foreign key (tenant_id, course_id) references academy_courses (tenant_id, course_id) on delete cascade
);

alter table academy_unlock_notices enable row level security;

drop policy if exists academy_unlock_notices_tenant_isolation on academy_unlock_notices;
create policy academy_unlock_notices_tenant_isolation on academy_unlock_notices
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
