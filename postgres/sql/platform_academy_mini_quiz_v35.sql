-- WWC Academy: мини-курсы V35 (16.10.2026) — бесплатный доступ клубу и участникам курса,
-- тест с подсказками в конце урока.
-- ТЗ: PROCESS/wwc-mini-courses-20261016/TZ_CORE.md.
--
--   academy_courses.kind      — рубрика уже есть (V19): 'mini' — «Мини-курсы»; здесь не меняется;
--   academy_courses.free_for  — кому платный курс (access_rule = 'purchase') открыт без покупки:
--                               {"club": true, "courses": ["online-start-4w"]}, оба ключа
--                               необязательны; null — только покупка или ключ;
--   academy_lessons.quiz_json — тест урока {"pass": n, "questions": [{id, q, ref, options:
--                               [{id, text, correct, hint}]}]}; форму проверяет код
--                               (app.academy.quiz), база — только что это объект со списком;
--   academy_quiz_attempts     — попытки теста: ответы, баллы, сдан ли. Число не ограничено.
--                               Человек Академии — telegram_user_id (как в academy_progress).
--
-- Только add/create if not exists и пересоздание своих проверок: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут через n8n (n8n/current/wwc_sql.py).

begin;

-- ALTER ждёт за читающими транзакциями: не висеть на блокировке дольше 5 секунд.
set local lock_timeout = '5s';

alter table academy_courses add column if not exists free_for jsonb;
alter table academy_courses drop constraint if exists academy_courses_free_for_check;
alter table academy_courses add constraint academy_courses_free_for_check
  check (free_for is null or jsonb_typeof(free_for) = 'object');

alter table academy_lessons add column if not exists quiz_json jsonb;
alter table academy_lessons drop constraint if exists academy_lessons_quiz_json_check;
alter table academy_lessons add constraint academy_lessons_quiz_json_check
  check (
    quiz_json is null
    -- coalesce: без ключа questions -> даёт null, и проверка без него прошла бы молча
    or (jsonb_typeof(quiz_json) = 'object' and coalesce(jsonb_typeof(quiz_json -> 'questions'), '') = 'array')
  );

create table if not exists academy_quiz_attempts (
  tenant_id text not null,
  attempt_id uuid not null default gen_random_uuid(),
  lesson_id uuid not null,
  telegram_user_id bigint not null,
  -- {"q1": "b", ...}: только вопросы теста, вариант — его id
  answers jsonb not null default '{}'::jsonb check (jsonb_typeof(answers) = 'object'),
  score int not null check (score >= 0),
  passed boolean not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, attempt_id),
  foreign key (tenant_id, lesson_id) references academy_lessons (tenant_id, lesson_id) on delete cascade
);

create index if not exists academy_quiz_attempts_student
  on academy_quiz_attempts (tenant_id, lesson_id, telegram_user_id, created_at desc);

alter table academy_quiz_attempts enable row level security;

drop policy if exists academy_quiz_attempts_tenant_isolation on academy_quiz_attempts;
create policy academy_quiz_attempts_tenant_isolation on academy_quiz_attempts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
