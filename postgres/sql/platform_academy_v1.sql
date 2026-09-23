-- WWC Academy V1 (23.09.2026): курсы, уроки, доступ, прогресс.
-- Один курс виден и на сайте (/academy/), и в боте (кабинет → «Академия»):
-- человек — это telegram_user_id, прогресс общий.
--
-- Основа под биржу курсов заложена сразу, но не используется в V1:
--   author_actor_id   — автор курса (null = платформа WWC);
--   price_wusd_minor  — цена в сотых WWC$ для access_rule = 'purchase';
--   platform_share_bp — доля платформы в базисных пунктах (10000 = 100%).
-- Выплат авторам в V1 нет (ИП на НПД не вправе работать по агентской схеме —
-- решение о модели биржи отдельно).

begin;

create table if not exists academy_courses (
  tenant_id text not null,
  course_id uuid not null default gen_random_uuid(),
  slug text not null check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  title text not null,
  subtitle text,
  access_rule text not null default 'pro'
    check (access_rule in ('pro', 'purchase', 'free')),
  author_actor_id text,
  price_wusd_minor bigint check (price_wusd_minor is null or price_wusd_minor >= 0),
  platform_share_bp int not null default 10000 check (platform_share_bp between 0 and 10000),
  status text not null default 'draft' check (status in ('draft', 'published', 'archived')),
  sort_order int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, course_id),
  unique (tenant_id, slug)
);

create table if not exists academy_lessons (
  tenant_id text not null,
  lesson_id uuid not null default gen_random_uuid(),
  course_id uuid not null,
  slug text not null check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  module_title text not null default '',
  position int not null,
  title text not null,
  short_title text,
  result_text text,
  est_minutes int,
  body_html text not null,
  checklist jsonb not null default '[]'::jsonb,
  video jsonb,
  status text not null default 'published' check (status in ('draft', 'published', 'archived')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, lesson_id),
  unique (tenant_id, course_id, slug),
  foreign key (tenant_id, course_id) references academy_courses (tenant_id, course_id) on delete cascade
);

create index if not exists academy_lessons_course_position
  on academy_lessons (tenant_id, course_id, position);

-- Явный доступ к курсу (покупка, подарок). Для access_rule = 'pro' запись не
-- нужна: доступ даёт оплаченный PRO.
create table if not exists academy_access (
  tenant_id text not null,
  course_id uuid not null,
  telegram_user_id bigint not null,
  source text not null check (source in ('purchase', 'gift', 'admin')),
  payment_ref text,
  granted_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key (tenant_id, course_id, telegram_user_id),
  foreign key (tenant_id, course_id) references academy_courses (tenant_id, course_id) on delete cascade
);

create table if not exists academy_progress (
  tenant_id text not null,
  telegram_user_id bigint not null,
  lesson_id uuid not null,
  done_at timestamptz not null default now(),
  source text not null default 'site' check (source in ('site', 'bot')),
  primary key (tenant_id, telegram_user_id, lesson_id),
  foreign key (tenant_id, lesson_id) references academy_lessons (tenant_id, lesson_id) on delete cascade
);

alter table academy_courses enable row level security;
alter table academy_lessons enable row level security;
alter table academy_access enable row level security;
alter table academy_progress enable row level security;

drop policy if exists academy_courses_tenant_isolation on academy_courses;
create policy academy_courses_tenant_isolation on academy_courses
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_lessons_tenant_isolation on academy_lessons;
create policy academy_lessons_tenant_isolation on academy_lessons
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_access_tenant_isolation on academy_access;
create policy academy_access_tenant_isolation on academy_access
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_progress_tenant_isolation on academy_progress;
create policy academy_progress_tenant_isolation on academy_progress
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
