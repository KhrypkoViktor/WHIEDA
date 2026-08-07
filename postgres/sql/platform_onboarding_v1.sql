-- WHIEDA Platform Onboarding V1 (Stage 5 — 7-day SQL launch)
-- Status: staging-first DDL; do NOT apply to production without owner review.
-- Spec: WHIEDA_CURRENT_BUILD_PLAN_SITE_TELEGRAM_ONBOARDING_V1_2026-08-07.md §5

begin;

create table if not exists onboarding_programs (
  tenant_id text not null,
  program_id uuid not null default gen_random_uuid(),
  program_key text not null,
  title text not null,
  version int not null default 1,
  status text not null default 'draft'
    check (status in ('draft', 'active', 'archived')),
  retention_class text not null default 'operational',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, program_id),
  unique (tenant_id, program_key, version)
);

create table if not exists onboarding_steps (
  tenant_id text not null,
  step_id uuid not null default gen_random_uuid(),
  program_id uuid not null,
  day_number int not null check (day_number between 1 and 30),
  title text not null,
  body_text text not null,
  next_hint text,
  sort_order int not null default 0,
  created_at timestamptz not null default now(),
  primary key (tenant_id, step_id),
  foreign key (tenant_id, program_id)
    references onboarding_programs (tenant_id, program_id)
    on delete cascade,
  unique (tenant_id, program_id, day_number)
);

create table if not exists onboarding_enrollments (
  tenant_id text not null,
  enrollment_id uuid not null default gen_random_uuid(),
  program_id uuid not null,
  session_id uuid,
  telegram_user_id bigint,
  first_ref text,
  assigned_owner_id text,
  status text not null default 'active'
    check (status in ('active', 'paused', 'completed')),
  current_day int not null default 1,
  reminders_paused boolean not null default false,
  idempotency_key text not null,
  started_at timestamptz not null default now(),
  paused_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, enrollment_id),
  foreign key (tenant_id, program_id)
    references onboarding_programs (tenant_id, program_id),
  unique (tenant_id, idempotency_key),
  unique (tenant_id, telegram_user_id)
);

create index if not exists idx_onboarding_enrollments_owner
  on onboarding_enrollments (tenant_id, assigned_owner_id, status);

create table if not exists onboarding_progress (
  tenant_id text not null,
  progress_id uuid not null default gen_random_uuid(),
  enrollment_id uuid not null,
  step_id uuid not null,
  day_number int not null,
  status text not null default 'pending'
    check (status in ('pending', 'done', 'skipped')),
  completed_at timestamptz,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, progress_id),
  foreign key (tenant_id, enrollment_id)
    references onboarding_enrollments (tenant_id, enrollment_id)
    on delete cascade,
  foreign key (tenant_id, step_id)
    references onboarding_steps (tenant_id, step_id),
  unique (tenant_id, idempotency_key),
  unique (tenant_id, enrollment_id, day_number)
);

create table if not exists onboarding_reminders (
  tenant_id text not null,
  reminder_id uuid not null default gen_random_uuid(),
  enrollment_id uuid not null,
  day_number int not null,
  scheduled_for timestamptz not null,
  sent_at timestamptz,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, reminder_id),
  foreign key (tenant_id, enrollment_id)
    references onboarding_enrollments (tenant_id, enrollment_id)
    on delete cascade,
  unique (tenant_id, idempotency_key)
);

create table if not exists mentor_escalations (
  tenant_id text not null,
  escalation_id uuid not null default gen_random_uuid(),
  enrollment_id uuid not null,
  day_number int not null,
  question_text text not null,
  status text not null default 'open'
    check (status in ('open', 'closed')),
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  closed_at timestamptz,
  primary key (tenant_id, escalation_id),
  foreign key (tenant_id, enrollment_id)
    references onboarding_enrollments (tenant_id, enrollment_id)
    on delete cascade,
  unique (tenant_id, idempotency_key)
);

-- RLS
alter table if exists onboarding_programs enable row level security;
alter table if exists onboarding_steps enable row level security;
alter table if exists onboarding_enrollments enable row level security;
alter table if exists onboarding_progress enable row level security;
alter table if exists onboarding_reminders enable row level security;
alter table if exists mentor_escalations enable row level security;

drop policy if exists onboarding_programs_tenant on onboarding_programs;
create policy onboarding_programs_tenant on onboarding_programs
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists onboarding_steps_tenant on onboarding_steps;
create policy onboarding_steps_tenant on onboarding_steps
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists onboarding_enrollments_tenant on onboarding_enrollments;
create policy onboarding_enrollments_tenant on onboarding_enrollments
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists onboarding_progress_tenant on onboarding_progress;
create policy onboarding_progress_tenant on onboarding_progress
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists onboarding_reminders_tenant on onboarding_reminders;
create policy onboarding_reminders_tenant on onboarding_reminders
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists mentor_escalations_tenant on mentor_escalations;
create policy mentor_escalations_tenant on mentor_escalations
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Seed draft program for whieda tenant (idempotent)
insert into onboarding_programs (tenant_id, program_id, program_key, title, version, status)
values (
  'whieda',
  'a1000000-0000-4000-8000-000000000001'::uuid,
  'first_week_sql_v1',
  'Первые 7 дней WHIEDA (draft)',
  1,
  'active'
)
on conflict (tenant_id, program_key, version) do nothing;

insert into onboarding_steps (tenant_id, step_id, program_id, day_number, title, body_text, next_hint, sort_order)
values
  ('whieda', 'b1000000-0000-4000-8000-000000000001'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 1,
   'День 1. Цель и наставник',
   'Определи одну личную цель на неделю. Запомни, кто твой наставник по ref-ссылке. Открой три ключевых ресурса в каталоге.',
   'Завтра: три ключевых продукта.', 1),
  ('whieda', 'b1000000-0000-4000-8000-000000000002'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 2,
   'День 2. Три ключевых продукта',
   'Выбери три товара, которые готов объяснить своими словами. Прочитай карточку, цену и ограничения по каждому.',
   'Завтра: короткая личная история.', 2),
  ('whieda', 'b1000000-0000-4000-8000-000000000003'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 3,
   'День 3. Короткое объяснение',
   'Сформулируй по одному предложению — зачем каждый из трёх товаров может быть полезен. Без обещаний результата.',
   'Завтра: первые приглашения.', 3),
  ('whieda', 'b1000000-0000-4000-8000-000000000004'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 4,
   'День 4. Первые приглашения',
   'Составь список из трёх человек для спокойного разговора. Не сохраняй чужую адресную книгу в системе.',
   'Завтра: типовые возражения.', 4),
  ('whieda', 'b1000000-0000-4000-8000-000000000005'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 5,
   'День 5. Типовые возражения',
   'Разбери два частых сомнения из базы FAQ. Сначала признай вопрос, потом дай факт из карточки.',
   'Завтра: встреча или презентация.', 5),
  ('whieda', 'b1000000-0000-4000-8000-000000000006'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 6,
   'День 6. Встреча и помощь',
   'Запланируй короткую встречу или презентацию. Если нужна помощь наставника — напиши «нужна помощь».',
   'Завтра: итоги недели.', 6),
  ('whieda', 'b1000000-0000-4000-8000-000000000007'::uuid, 'a1000000-0000-4000-8000-000000000001'::uuid, 7,
   'День 7. Итоги и план на 30 дней',
   'Отметь: с кем поговорил, какие вопросы повторялись, где не хватило ответа. Это основа следующего плана.',
   'Программа завершена — можно повторить или углубиться.', 7)
on conflict (tenant_id, program_id, day_number) do nothing;

commit;
