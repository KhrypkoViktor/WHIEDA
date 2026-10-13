-- WWC Academy: домашка как переписка, работы группы, анкета ученика V32 (13.10.2026).
--
-- Решение владельца 13.10 (голосовое) и автора ALMIRA: домашка — одна нить на урок и
-- ученика, как helpdesk: ученик сдаёт и спрашивает, преподаватель отвечает текстом,
-- замечанием к конкретному фото, файлами и видео; «Принять» закрывает нить, «Вернуть» —
-- ученик присылает исправления в ту же нить. Группа видит работы и ответы преподавателя.
--
--   academy_submission_messages — сообщения нити (голова нити — сама academy_submissions);
--   academy_submissions.seen_at / student_seen_at / last_message_at — «почта» преподавателя
--                                 и непрочитанное у ученика;
--   academy_courses.works_public — работы группы видны ученикам курса;
--                 .review_days  — «ответ преподавателя — до N дней» (0 — не показывать);
--                 .teaser       — видеообращение на витрине для закрытого курса ({media_id}
--                                 или {provider,id}); .landing_url — страница курса на сайте;
--   academy_showcases.author_notify — слать ли автору витрины уведомления о домашках;
--   academy_profiles — анкета ученика: имя, город, опыт, цель, Instagram.
--
-- Только create/add if not exists: повторный прогон безопасен. Знака доллара в файле нет.

begin;

set local lock_timeout = '5s';

create table if not exists academy_submission_messages (
  tenant_id text not null,
  message_id uuid not null default gen_random_uuid(),
  submission_id uuid not null,
  sender text not null check (sender in ('student', 'author')),
  telegram_user_id bigint not null,
  text text not null default '' check (length(text) <= 4000),
  media jsonb not null default '[]'::jsonb check (jsonb_typeof(media) = 'array'),
  about_media_id uuid,
  created_at timestamptz not null default now(),
  primary key (tenant_id, message_id),
  foreign key (tenant_id, submission_id)
    references academy_submissions (tenant_id, submission_id) on delete cascade
);

create index if not exists academy_submission_messages_thread
  on academy_submission_messages (tenant_id, submission_id, created_at);

alter table academy_submissions add column if not exists seen_at timestamptz;
alter table academy_submissions add column if not exists student_seen_at timestamptz;
alter table academy_submissions add column if not exists last_message_at timestamptz;
update academy_submissions set last_message_at = coalesce(updated_at, created_at) where last_message_at is null;

alter table academy_courses add column if not exists works_public boolean not null default false;
alter table academy_courses add column if not exists review_days integer not null default 0;
alter table academy_courses drop constraint if exists academy_courses_review_days_check;
alter table academy_courses add constraint academy_courses_review_days_check check (review_days between 0 and 60);
alter table academy_courses add column if not exists teaser jsonb;
alter table academy_courses add column if not exists landing_url text;

alter table academy_showcases add column if not exists author_notify boolean not null default true;

create table if not exists academy_profiles (
  tenant_id text not null,
  telegram_user_id bigint not null,
  name text not null default '' check (length(name) <= 120),
  city text not null default '' check (length(city) <= 120),
  experience text not null default '' check (length(experience) <= 1000),
  goal text not null default '' check (length(goal) <= 1000),
  instagram text not null default '' check (length(instagram) <= 64),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id)
);

alter table academy_submission_messages enable row level security;
alter table academy_profiles enable row level security;

drop policy if exists academy_submission_messages_tenant_isolation on academy_submission_messages;
create policy academy_submission_messages_tenant_isolation on academy_submission_messages
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_profiles_tenant_isolation on academy_profiles;
create policy academy_profiles_tenant_isolation on academy_profiles
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
