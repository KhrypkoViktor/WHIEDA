-- WWC: пометки владельца о людях (V26, 09.10.2026).
--
-- Владелец: Тотолина и Суслова — возврат 100 %, «помечай, что они вместе с Софией в
-- списке без скидок». Флаг — на человека (actor), не на сайт: у Сусловой сайта нет.
-- Таблица учёта показывает пометки вкладкой «Без скидок».

begin;

create table if not exists partner_flags (
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null,
  flag text not null check (flag in ('no_discounts')),
  note text,
  created_by text not null default 'viktor',
  created_at timestamptz not null default now(),
  primary key (tenant_id, actor_id, flag)
);

alter table partner_flags enable row level security;
drop policy if exists partner_flags_tenant_isolation on partner_flags;
create policy partner_flags_tenant_isolation on partner_flags
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
