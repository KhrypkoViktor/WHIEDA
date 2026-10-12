-- WWC: привязка Max одним нажатием (V31, 12.10.2026).
--
-- Владелец: «макс не хочет авторизовывать» — мини-приложение Max видело незнакомого человека и просило
-- отправить /max в Telegram-боте; так никто не делает. Теперь мини-приложение само выдаёт кнопку
-- t.me/<бот>?start=maxlink_<код>: Telegram-бот знает, кто нажал, привязывает к нему этот Max и сразу
-- подтверждает вход на сайт, ради которого Max открывали. Здесь — заявка со стороны Max: кто в Max и
-- какую заявку входа (хеш) подтвердить. Храним только хеши; одноразово, 30 минут. Пишет: app/max/webapp.py.

begin;

create table if not exists max_link_requests (
  code_hash text primary key,
  tenant_id text not null references tenants (tenant_id),
  max_user_id text not null,
  username text,
  display_name text not null default '',
  login_challenge_hash text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  used_by_telegram_user_id bigint
);

alter table max_link_requests enable row level security;
drop policy if exists max_link_requests_tenant_isolation on max_link_requests;
create policy max_link_requests_tenant_isolation on max_link_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
