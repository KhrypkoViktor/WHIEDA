-- WWC Academy: витрины авторов V29 (11.10.2026).
--
-- Решение владельца 11.10: одно ядро Академии, у партнёра-автора своя витрина на
-- его адресе (первая — арт-студия ALMIRA, academy.almira.by, бот @ALMIRA_I_bot).
-- Курсы, ученики, доступы, домашки и медиа остаются общими в тенанте whieda;
-- витрина меняет только оформление, порядок курсов и бота. Права доступа от неё
-- не зависят.
--
--   hosts               — адреса витрины, первый — основной (ссылки бота);
--   owner_actor_id      — автор-партнёр: его курсы «свои», «Академия автора» без
--                         оплаты (партнёрство). NULL — витрина платформы: свои там
--                         курсы авторов без собственной витрины;
--   theme / assets / links — токены цвета, логотип и иконки, сайт, контакт, политика;
--   telegram_binding_id — бот витрины: вход, ключи, уведомления (NULL — бот процесса);
--   max_binding_id      — бот витрины в Max (канал Академии в Max — следующий срез);
--   more_mode           — чужие курсы: none | no_pro | all. Купленные курсы видны всегда.
--
-- Идемпотентно. В файле нет знака доллара: боевые правки идут через n8n.

begin;

create table if not exists academy_showcases (
  tenant_id text not null,
  showcase_id text not null check (showcase_id ~ '^[a-z0-9]+(-[a-z0-9]+)*\Z'),
  status text not null default 'active' check (status in ('active', 'disabled')),
  is_default boolean not null default false,
  hosts text[] not null default '{}',
  owner_actor_id text,
  brand_name text not null,
  academy_title text not null,
  theme jsonb not null default '{}'::jsonb,
  assets jsonb not null default '{}'::jsonb,
  links jsonb not null default '{}'::jsonb,
  telegram_binding_id text,
  max_binding_id text,
  more_mode text not null default 'none' check (more_mode in ('none', 'no_pro', 'all')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, showcase_id)
);

create unique index if not exists academy_showcases_one_default
  on academy_showcases (tenant_id) where is_default;
create unique index if not exists academy_showcases_one_per_owner
  on academy_showcases (tenant_id, owner_actor_id) where owner_actor_id is not null;

alter table academy_showcases enable row level security;

drop policy if exists academy_showcases_tenant_isolation on academy_showcases;
create policy academy_showcases_tenant_isolation on academy_showcases
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Витрина платформы: wwc.best/academy/ и партнёрские поддомены, бот процесса.
insert into academy_showcases (tenant_id, showcase_id, is_default, brand_name, academy_title, more_mode)
select 'whieda', 'wwc', true, 'WWC', 'Академия WWC', 'none'
where exists (select 1 from tenants where tenant_id = 'whieda')
on conflict (tenant_id, showcase_id) do nothing;

commit;
