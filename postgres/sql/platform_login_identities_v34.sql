-- WWC: вход на сайт без Telegram — Google и Max как дополнительные способы входа (V34, 15.10.2026).
--
-- Владелец (15.10.2026): человек в ядре по-прежнему = telegram_user_id; Google и Max — слой сверху.
-- Новый способ входа привязывается только по коду, который владелец выдаёт поимённо в Telegram-боте
-- («код входа volkova»). Саморегистрации нет. Telegram-путь входа не меняется.
--
-- account_identities — привязки: google (sub Google, e-mail), max (id пользователя Max) → telegram_user_id.
--   Отвязка — revoked_at (строка остаётся для истории; активных на (provider, external_id) не больше одной).
--   Существующие привязки Max (lead_actor_channels, V30/V31) переносятся сюда бэкфиллом как
--   linked_by 'telegram:/max'; lead_actor_channels не трогаем — по нему живёт переписка с ботом Max.
-- login_link_codes — коды владельца: 6 цифр, только хеш, 24 часа, один раз; новый код гасит прежний
--   (revoked_at), активный код на человека — один.
-- login_pending — чужая личность, которая ждёт код: для Google — state/nonce/PKCE до callback и sub/e-mail
--   после; для Max — счётчик попыток. 15 минут, одноразово. Токены — только хешами.
-- Пишет: backend/platform-api/app/login_identities/. Идемпотентно. Знака доллара в файле нет
-- (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

create table if not exists account_identities (
  tenant_id text not null references tenants (tenant_id),
  identity_id uuid not null default gen_random_uuid(),
  telegram_user_id bigint not null,
  provider text not null check (provider in ('google', 'max')),
  external_id text not null,
  email text,
  display_name text,
  linked_at timestamptz not null default now(),
  linked_by text not null default '',
  revoked_at timestamptz,
  revoked_by text,
  primary key (identity_id)
);

create unique index if not exists account_identities_active_external_uq
  on account_identities (tenant_id, provider, external_id)
  where revoked_at is null;

create index if not exists account_identities_person_idx
  on account_identities (tenant_id, telegram_user_id);

create table if not exists login_link_codes (
  tenant_id text not null references tenants (tenant_id),
  code_id uuid not null default gen_random_uuid(),
  code_hash text not null,
  telegram_user_id bigint not null,
  issued_by bigint not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  used_provider text check (used_provider in ('google', 'max')),
  revoked_at timestamptz,
  primary key (code_id)
);

-- Один активный код на человека и один активный код на значение (новый код гасит прежний).
create unique index if not exists login_link_codes_active_person_uq
  on login_link_codes (tenant_id, telegram_user_id)
  where used_at is null and revoked_at is null;

create unique index if not exists login_link_codes_active_hash_uq
  on login_link_codes (tenant_id, code_hash)
  where used_at is null and revoked_at is null;

create table if not exists login_pending (
  tenant_id text not null references tenants (tenant_id),
  pending_id uuid not null default gen_random_uuid(),
  provider text not null check (provider in ('google', 'max')),
  status text not null default 'started'
    check (status in ('started', 'awaiting_code', 'linked', 'failed')),
  -- Google OAuth до callback: state и nonce хешами, PKCE verifier открытым (одноразовый, 15 минут).
  state_hash text,
  nonce_hash text,
  code_verifier text,
  host text not null default '',
  return_to text not null default '/',
  visitor_session_id uuid,
  -- Кто пришёл (после callback Google / из initData Max) и токен для «Введите код от Виктора».
  external_id text,
  email text,
  display_name text,
  link_token_hash text,
  attempts integer not null default 0,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  primary key (pending_id)
);

create unique index if not exists login_pending_state_uq
  on login_pending (tenant_id, state_hash)
  where state_hash is not null;

create unique index if not exists login_pending_link_token_uq
  on login_pending (tenant_id, link_token_hash)
  where link_token_hash is not null;

create index if not exists login_pending_subject_idx
  on login_pending (tenant_id, provider, external_id);

alter table account_identities enable row level security;
alter table login_link_codes enable row level security;
alter table login_pending enable row level security;

drop policy if exists account_identities_tenant_isolation on account_identities;
create policy account_identities_tenant_isolation on account_identities
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists login_link_codes_tenant_isolation on login_link_codes;
create policy login_link_codes_tenant_isolation on login_link_codes
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists login_pending_tenant_isolation on login_pending;
create policy login_pending_tenant_isolation on login_pending
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Бэкфилл: Max, привязанный к человеку Telegram через /max (V30) или одним нажатием (V31),
-- становится max-identity. Отозванную ранее привязку (revoked_at) повторный прогон не воскрешает.
insert into account_identities (tenant_id, telegram_user_id, provider, external_id, display_name, linked_at, linked_by)
select c.tenant_id, la.telegram_user_id, 'max', c.channel_user_id, nullif(c.display_name, ''), c.created_at, 'telegram:/max'
from lead_actor_channels c
join lead_actors la on la.actor_id = c.actor_id and la.tenant_id = c.tenant_id
where c.channel = 'max'
  and la.telegram_user_id is not null
  and not exists (
    select 1 from account_identities ai
    where ai.tenant_id = c.tenant_id and ai.provider = 'max' and ai.external_id = c.channel_user_id
  );

commit;
