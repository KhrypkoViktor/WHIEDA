-- WWC: коды привязки мессенджера к человеку (V30, 11.10.2026).
--
-- Владелец: людям из РФ трудно войти на сайт через Telegram — «авторизация на сайте: выбор Telegram / Max».
-- Вход через Max — мини-приложение бота Max (подпись initData), сессии сайта по-прежнему на
-- telegram_user_id. Чтобы Max-пользователь стал тем же человеком, что в Telegram, он один раз открывает
-- ссылку https://max.ru/<бот>?startapp=link_<код>; код выдаёт Telegram-бот (/max) тому, кого он уже знает.
-- Храним только хеш кода; код одноразовый и короткоживущий. Пишет: app/max/webapp.py.

begin;

create table if not exists channel_link_codes (
  code_hash text primary key,
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null,
  channel text not null check (channel in ('max')),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  used_channel_user_id text
);

create index if not exists channel_link_codes_actor_idx on channel_link_codes (tenant_id, actor_id);

alter table channel_link_codes enable row level security;
drop policy if exists channel_link_codes_tenant_isolation on channel_link_codes;
create policy channel_link_codes_tenant_isolation on channel_link_codes
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
