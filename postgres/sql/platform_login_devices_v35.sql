-- WWC: лимит устройств, ключи доступа (passkey), Яндекс ID и VK ID как способы входа (V35, 08.11.2026).
--
-- Владелец (08.11.2026): «по входу со всем согласен». Человек в ядре по-прежнему = telegram_user_id;
-- новые способы входа привязываются к нему через account_identities (yandex, vk) и passkey_credentials.
--
-- content_access_sessions: device_label («iPhone · Safari») и created_ip_hint (префикс адреса, не полный IP) —
--   для списка «Устройства» в кабинете; revoked_by — кто закрыл сессию ('device_limit', 'self:others', …).
--   Лимит две активные сессии на человека держит приложение (create_session_for_user).
-- passkey_credentials: открытый ключ и счётчик подписей; секрет остаётся на устройстве человека.
--   Отзыв — revoked_at (credential_id уникален навсегда: старый ключ второй раз не регистрируется).
-- passkey_challenges: одноразовый challenge регистрации/входа на 10 минут.
-- account_identities / login_link_codes / login_pending: список провайдеров расширен на yandex и vk.
--
-- Применять после platform_content_access_v1.sql (сессии сайта). Идемпотентно. Старые файлы не менялись.
-- Пишет: backend/platform-api/app/content_access/devices.py, app/login_identities/passkeys.py.
-- Знака доллара в файле нет (n8n/current/wwc_sql.py).

begin;

set local lock_timeout = '5s';

alter table content_access_sessions add column if not exists device_label text;
alter table content_access_sessions add column if not exists created_ip_hint text;
alter table content_access_sessions add column if not exists revoked_by text;

create index if not exists idx_content_access_sessions_person_active
  on content_access_sessions (tenant_id, telegram_user_id, last_seen_at desc)
  where revoked_at is null;

create table if not exists passkey_credentials (
  tenant_id text not null references tenants (tenant_id),
  passkey_id uuid not null default gen_random_uuid(),
  telegram_user_id bigint not null,
  credential_id bytea not null,
  public_key bytea not null,
  sign_count bigint not null default 0,
  transports text[] not null default '{}',
  label text not null default '',
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz,
  primary key (passkey_id)
);

create unique index if not exists passkey_credentials_credential_uq
  on passkey_credentials (credential_id);

create index if not exists passkey_credentials_person_idx
  on passkey_credentials (tenant_id, telegram_user_id)
  where revoked_at is null;

create table if not exists passkey_challenges (
  tenant_id text not null references tenants (tenant_id),
  challenge_id uuid not null default gen_random_uuid(),
  kind text not null check (kind in ('passkey_reg', 'passkey_auth')),
  challenge_hash text not null,
  challenge bytea not null,
  telegram_user_id bigint,
  host text not null default '',
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  primary key (challenge_id)
);

create unique index if not exists passkey_challenges_hash_uq
  on passkey_challenges (tenant_id, challenge_hash);

alter table passkey_credentials enable row level security;
alter table passkey_challenges enable row level security;

drop policy if exists passkey_credentials_tenant_isolation on passkey_credentials;
create policy passkey_credentials_tenant_isolation on passkey_credentials
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists passkey_challenges_tenant_isolation on passkey_challenges;
create policy passkey_challenges_tenant_isolation on passkey_challenges
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- login_pending.telegram_user_id: человек, который начал привязку, будучи вошедшим (сессия сайта на start).
--   Привязка у вошедшего идёт без кода владельца: личность подтверждена его же сессией и браузером.
alter table login_pending add column if not exists telegram_user_id bigint;

alter table account_identities drop constraint if exists account_identities_provider_check;
alter table account_identities add constraint account_identities_provider_check
  check (provider in ('google', 'max', 'yandex', 'vk'));

alter table login_link_codes drop constraint if exists login_link_codes_used_provider_check;
alter table login_link_codes add constraint login_link_codes_used_provider_check
  check (used_provider in ('google', 'max', 'yandex', 'vk'));

alter table login_pending drop constraint if exists login_pending_provider_check;
alter table login_pending add constraint login_pending_provider_check
  check (provider in ('google', 'max', 'yandex', 'vk'));

commit;
