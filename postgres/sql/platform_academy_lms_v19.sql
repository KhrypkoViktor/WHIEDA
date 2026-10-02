-- WWC Academy V19 «LMS» (02.10.2026): модули и правила открытия, домашки,
-- эфиры, медиа (картинки, файлы, видео на нашем сервере).
-- ТЗ: PROCESS/wwc-academy-v2-20261002/TASK.md, раздел 4.
--
-- Что здесь:
--   academy_courses     + описание (md и html), обложка, валюта показа цены, вид;
--                          price_wusd_minor — сумма в сотых price_currency (только показ:
--                          ученик платит автору напрямую, доступ — ключом);
--   academy_modules     — модули курса с правилом открытия unlock:
--                          open | after_prev | date {"at": ts} | days_after_start {"days": n};
--   academy_lessons     + модуль, вид (урок или эфир), эфир, своё правило открытия
--                          (перекрывает правило модуля), файлы (список media_id), текст в md;
--   academy_assignments — домашка урока (одна на урок);
--   academy_submissions — сдачи: одна активная на (урок, ученик), после «вернули» — новая строка;
--   academy_access      + started_at (старт для days_after_start; старым строкам — granted_at),
--                          expires_at (срок доступа, пока никто не ставит);
--   academy_media       — загрузки: uploading → processing (видео) → ready | failed.
--
-- Только create/add if not exists и пересоздание своих проверок: повторный прогон
-- безопасен. Знака доллара в файле нет: боевые правки идут через n8n.

begin;

-- ALTER ждёт за читающими транзакциями: не висеть на блокировке дольше 5 секунд.
set local lock_timeout = '5s';

-- 1. Медиа (раньше курсов: на неё ссылается обложка).
create table if not exists academy_media (
  tenant_id text not null,
  media_id uuid not null default gen_random_uuid(),
  owner_actor_id text,
  owner_telegram_user_id bigint,
  kind text not null check (kind in ('image', 'file', 'video')),
  original_name text not null check (length(original_name) between 1 and 255),
  mime text not null check (length(mime) between 3 and 120),
  size_bytes bigint not null check (size_bytes > 0),
  chunk_size int not null default 5242880 check (chunk_size > 0),
  storage_key text not null,
  status text not null default 'uploading'
    check (status in ('uploading', 'processing', 'ready', 'failed')),
  -- видео: {"mp4_720": key, "poster": key, "duration_sec": n}
  variants jsonb not null default '{}'::jsonb check (jsonb_typeof(variants) = 'object'),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (media_id),
  unique (tenant_id, media_id)
);

create index if not exists academy_media_owner
  on academy_media (tenant_id, owner_telegram_user_id, created_at desc);

-- 2. Курсы.
alter table academy_courses add column if not exists description_md text;
alter table academy_courses add column if not exists description_html text;
alter table academy_courses add column if not exists cover_media_id uuid;
alter table academy_courses add column if not exists price_currency text not null default 'WUSD';
alter table academy_courses add column if not exists kind text not null default 'course';

alter table academy_courses drop constraint if exists academy_courses_price_currency_check;
alter table academy_courses add constraint academy_courses_price_currency_check
  check (price_currency ~ '^[A-Z]{3,4}\Z');
alter table academy_courses drop constraint if exists academy_courses_kind_check;
alter table academy_courses add constraint academy_courses_kind_check
  check (kind ~ '^[a-z][a-z_]{0,31}\Z');
alter table academy_courses drop constraint if exists academy_courses_cover_media_fk;
alter table academy_courses add constraint academy_courses_cover_media_fk
  foreign key (tenant_id, cover_media_id) references academy_media (tenant_id, media_id);

-- 3. Модули.
create table if not exists academy_modules (
  tenant_id text not null,
  module_id uuid not null default gen_random_uuid(),
  course_id uuid not null,
  position int not null,
  title text not null check (length(btrim(title)) between 1 and 200),
  unlock jsonb not null default '{"type":"open"}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (module_id),
  unique (tenant_id, module_id),
  foreign key (tenant_id, course_id) references academy_courses (tenant_id, course_id) on delete cascade
);

alter table academy_modules drop constraint if exists academy_modules_unlock_check;
alter table academy_modules add constraint academy_modules_unlock_check
  check (
    jsonb_typeof(unlock) = 'object'
    and unlock ->> 'type' in ('open', 'after_prev', 'date', 'days_after_start')
  );

create index if not exists academy_modules_course_position
  on academy_modules (tenant_id, course_id, position);

-- 4. Уроки.
alter table academy_lessons add column if not exists module_id uuid;
alter table academy_lessons add column if not exists kind text not null default 'lesson';
alter table academy_lessons add column if not exists live_at timestamptz;
alter table academy_lessons add column if not exists live_url text;
alter table academy_lessons add column if not exists unlock jsonb;
alter table academy_lessons add column if not exists files jsonb not null default '[]'::jsonb;
alter table academy_lessons add column if not exists body_md text;

alter table academy_lessons drop constraint if exists academy_lessons_kind_check;
alter table academy_lessons add constraint academy_lessons_kind_check
  check (kind in ('lesson', 'live'));
alter table academy_lessons drop constraint if exists academy_lessons_unlock_check;
alter table academy_lessons add constraint academy_lessons_unlock_check
  check (
    unlock is null
    or (
      jsonb_typeof(unlock) = 'object'
      and unlock ->> 'type' in ('open', 'after_prev', 'date', 'days_after_start')
    )
  );
alter table academy_lessons drop constraint if exists academy_lessons_files_check;
alter table academy_lessons add constraint academy_lessons_files_check
  check (jsonb_typeof(files) = 'array');
-- Удаление модуля сначала отвязывает его уроки (код), каскада здесь нет.
alter table academy_lessons drop constraint if exists academy_lessons_module_fk;
alter table academy_lessons add constraint academy_lessons_module_fk
  foreign key (tenant_id, module_id) references academy_modules (tenant_id, module_id);

create index if not exists academy_lessons_module
  on academy_lessons (tenant_id, module_id, position);

-- 5. Домашки.
create table if not exists academy_assignments (
  tenant_id text not null,
  lesson_id uuid not null,
  prompt_md text,
  prompt_html text not null default '',
  required boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, lesson_id),
  foreign key (tenant_id, lesson_id) references academy_lessons (tenant_id, lesson_id) on delete cascade
);

create table if not exists academy_submissions (
  tenant_id text not null,
  submission_id uuid not null default gen_random_uuid(),
  lesson_id uuid not null,
  telegram_user_id bigint not null,
  text text not null default '' check (length(text) <= 20000),
  media jsonb not null default '[]'::jsonb check (jsonb_typeof(media) = 'array'),
  status text not null default 'submitted' check (status in ('submitted', 'accepted', 'returned')),
  author_comment text check (author_comment is null or length(author_comment) <= 4000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  reviewed_at timestamptz,
  -- telegram_user_id проверившего (автор или владелец)
  reviewed_by bigint,
  primary key (tenant_id, submission_id),
  foreign key (tenant_id, lesson_id) references academy_lessons (tenant_id, lesson_id) on delete cascade
);

-- Одна активная сдача (на проверке или принята) на урок и ученика.
create unique index if not exists academy_submissions_one_active
  on academy_submissions (tenant_id, lesson_id, telegram_user_id)
  where status <> 'returned';

create index if not exists academy_submissions_inbox
  on academy_submissions (tenant_id, status, created_at);

create index if not exists academy_submissions_student
  on academy_submissions (tenant_id, telegram_user_id, lesson_id, created_at desc);

-- 6. Доступ: старт и срок. Старым строкам старт — момент выдачи доступа.
alter table academy_access add column if not exists started_at timestamptz;
update academy_access set started_at = granted_at where started_at is null;
alter table academy_access alter column started_at set default now();
alter table academy_access add column if not exists expires_at timestamptz;

-- 7. Изоляция тенантов.
alter table academy_media enable row level security;
alter table academy_modules enable row level security;
alter table academy_assignments enable row level security;
alter table academy_submissions enable row level security;

drop policy if exists academy_media_tenant_isolation on academy_media;
create policy academy_media_tenant_isolation on academy_media
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_modules_tenant_isolation on academy_modules;
create policy academy_modules_tenant_isolation on academy_modules
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_assignments_tenant_isolation on academy_assignments;
create policy academy_assignments_tenant_isolation on academy_assignments
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_submissions_tenant_isolation on academy_submissions;
create policy academy_submissions_tenant_isolation on academy_submissions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
