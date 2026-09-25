-- WWC Academy «полка» V15 (25.09.2026): курсы авторов и доступ по ключу.
--
-- Решение владельца 25.09: автор платит за размещение курса («полка»),
-- ученики платят автору напрямую, доступ ученику выдаёт автор ключом.
-- Денег учеников через платформу нет (ИП на НПД, см. platform_academy_v1.sql).
--
-- Что здесь:
--   academy_access.source   + 'key' — доступ, открытый ключом автора;
--   academy_access_keys     — ключи: 12 символов без похожих o/0/l/1;
--   academy_shelf           — оплаченная полка автора (actor_id = lead_actors);
--   partner_product_access  — product_code 'academy_shelf' допустим;
--   partner_subscription_plans.course_slug — какой курс открывает course_<код>.
--
-- Строки тарифа academy_shelf_3m здесь НЕТ: цены в partner_subscription_plans
-- обязательны и > 0, а цену полки назначает владелец. Готовая вставка — в
-- PROCESS/wwc-academy-shelf-20260925/STATE.md, выполняется после ответа владельца.
--
-- Идемпотентно. В файле нет знака доллара: боевые правки идут через n8n.

begin;

-- 1. Ключ — новый источник доступа к курсу.
alter table academy_access
  drop constraint if exists academy_access_source_check;
alter table academy_access
  add constraint academy_access_source_check
  check (source in ('purchase', 'gift', 'admin', 'key'));

-- 2. Ключи доступа. Код одноразовый по умолчанию (max_uses = 1).
create table if not exists academy_access_keys (
  tenant_id text not null,
  key_id uuid primary key default gen_random_uuid(),
  course_id uuid not null,
  code text not null check (code ~ '^[a-km-np-z2-9]{12}\Z'),
  created_by_actor_id text not null,
  max_uses int not null default 1 check (max_uses >= 1),
  used_count int not null default 0 check (used_count >= 0),
  expires_at timestamptz,
  revoked_at timestamptz,
  -- Пачка на одно сообщение автора («ключи …»): повтор того же апдейта отдаёт
  -- уже выпущенные коды, а не новую пачку. Формат: <binding>:<chat>:<message>.
  issued_for_message text,
  created_at timestamptz not null default now(),
  unique (tenant_id, code),
  check (used_count <= max_uses),
  foreign key (tenant_id, course_id) references academy_courses (tenant_id, course_id) on delete cascade
);

create index if not exists academy_access_keys_course
  on academy_access_keys (tenant_id, course_id);

create index if not exists academy_access_keys_issued_for_message
  on academy_access_keys (tenant_id, issued_for_message)
  where issued_for_message is not null;

-- 3. Полка автора: пока paid_until в будущем и status = 'active', автор выдаёт ключи,
--    а ученики гасят их. Полка — у человека: любой его actor (тот же telegram_user_id).
--    Оплата продлевает срок, но status не трогает: приостановка — ручное решение.
create table if not exists academy_shelf (
  tenant_id text not null,
  actor_id text not null,
  paid_until timestamptz,
  status text not null default 'active' check (status in ('active', 'suspended')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, actor_id)
);

alter table academy_access_keys enable row level security;
alter table academy_shelf enable row level security;

drop policy if exists academy_access_keys_tenant_isolation on academy_access_keys;
create policy academy_access_keys_tenant_isolation on academy_access_keys
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists academy_shelf_tenant_isolation on academy_shelf;
create policy academy_shelf_tenant_isolation on academy_shelf
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- 4. Доступы партнёра: клуб, полка Академии и любой курс course_<slug>.
alter table partner_product_access
  drop constraint if exists partner_product_access_product_code_check;
alter table partner_product_access
  add constraint partner_product_access_product_code_check
  -- \Z вместо знака доллара: боевые правки идут через n8n, а он режет этот знак.
  check (product_code in ('club_subscription', 'academy_shelf') or product_code ~ '^course_[a-z0-9_]+\Z');

-- 5. Тариф course_<код> → курс Академии. NULL — курс ещё не создан (course_academy).
alter table partner_subscription_plans
  add column if not exists course_slug text;

commit;
