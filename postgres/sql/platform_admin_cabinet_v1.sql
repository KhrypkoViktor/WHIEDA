-- WWC Owner Cabinet — admin identity, sessions, audit (P0.1)
-- Additive only. Do not apply to production without owner review.

begin;

create table if not exists platform_admin_principals (
  principal_id uuid primary key default gen_random_uuid(),
  telegram_user_id bigint not null,
  role text not null
    check (role in ('super_admin', 'admin', 'viewer')),
  status text not null default 'active'
    check (status in ('active', 'suspended')),
  display_name text,
  allowed_tenant_ids text[],
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (telegram_user_id)
);

create index if not exists idx_platform_admin_principals_role
  on platform_admin_principals (role, status)
  where status = 'active';

create table if not exists platform_admin_login_challenges (
  challenge_id uuid primary key default gen_random_uuid(),
  challenge_hash text not null,
  browser_nonce_hash text not null,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'expired', 'used', 'rejected')),
  principal_id uuid references platform_admin_principals (principal_id),
  expires_at timestamptz not null,
  approved_at timestamptz,
  used_at timestamptz,
  created_at timestamptz not null default now(),
  unique (challenge_hash)
);

create index if not exists idx_platform_admin_login_challenges_status
  on platform_admin_login_challenges (status, expires_at desc);

create table if not exists platform_admin_sessions (
  session_id uuid primary key default gen_random_uuid(),
  session_hash text not null,
  principal_id uuid not null references platform_admin_principals (principal_id),
  active_tenant_id text,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (session_hash)
);

create index if not exists idx_platform_admin_sessions_principal
  on platform_admin_sessions (principal_id, expires_at desc)
  where revoked_at is null;

create table if not exists platform_admin_audit_log (
  audit_id bigserial primary key,
  principal_id uuid references platform_admin_principals (principal_id),
  action text not null,
  target_tenant_id text,
  object_type text,
  object_id text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_platform_admin_audit_log_principal
  on platform_admin_audit_log (principal_id, created_at desc);

create index if not exists idx_platform_admin_audit_log_tenant
  on platform_admin_audit_log (target_tenant_id, created_at desc)
  where target_tenant_id is not null;

-- RLS: deny by default; app sets app.admin_service=true on admin_connection()
alter table platform_admin_principals enable row level security;
alter table platform_admin_login_challenges enable row level security;
alter table platform_admin_sessions enable row level security;
alter table platform_admin_audit_log enable row level security;

drop policy if exists platform_admin_principals_service on platform_admin_principals;
create policy platform_admin_principals_service on platform_admin_principals
  using (current_setting('app.admin_service', true) = 'true')
  with check (current_setting('app.admin_service', true) = 'true');

drop policy if exists platform_admin_login_challenges_service on platform_admin_login_challenges;
create policy platform_admin_login_challenges_service on platform_admin_login_challenges
  using (current_setting('app.admin_service', true) = 'true')
  with check (current_setting('app.admin_service', true) = 'true');

drop policy if exists platform_admin_sessions_service on platform_admin_sessions;
create policy platform_admin_sessions_service on platform_admin_sessions
  using (current_setting('app.admin_service', true) = 'true')
  with check (current_setting('app.admin_service', true) = 'true');

drop policy if exists platform_admin_audit_log_service on platform_admin_audit_log;
create policy platform_admin_audit_log_service on platform_admin_audit_log
  using (current_setting('app.admin_service', true) = 'true')
  with check (current_setting('app.admin_service', true) = 'true');

commit;
