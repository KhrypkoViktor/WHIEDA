-- WWC public content access — Telegram-verified browser session (not admin).
-- Additive only. Do not apply to production without owner review.
-- Spec: 03_Website/wwc-best/docs/WWC_CLOSED_CONTENT_ACCESS_TZ_V1_2026-08-26.md

begin;

create table if not exists content_access_challenges (
  challenge_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  visitor_session_id uuid not null,
  challenge_hash text not null,
  browser_nonce_hash text not null,
  return_to text not null,
  requested_scope text not null default 'telegram_verified'
    check (requested_scope in ('telegram_verified', 'partner_verified')),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'expired', 'used', 'rejected')),
  telegram_user_id bigint,
  expires_at timestamptz not null,
  approved_at timestamptz,
  used_at timestamptz,
  created_at timestamptz not null default now(),
  unique (challenge_hash),
  foreign key (tenant_id, visitor_session_id)
    references visitor_sessions (tenant_id, session_id)
    on delete cascade
);

create index if not exists idx_content_access_challenges_tenant_status
  on content_access_challenges (tenant_id, status, expires_at desc);

create table if not exists content_access_sessions (
  session_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  visitor_session_id uuid not null,
  session_hash text not null,
  telegram_user_id bigint not null,
  scope text not null default 'telegram_verified'
    check (scope in ('telegram_verified', 'partner_verified')),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique (session_hash),
  foreign key (tenant_id, visitor_session_id)
    references visitor_sessions (tenant_id, session_id)
    on delete cascade
);

create index if not exists idx_content_access_sessions_tenant
  on content_access_sessions (tenant_id, expires_at desc)
  where revoked_at is null;

create table if not exists content_access_materials (
  tenant_id text not null,
  content_key text not null,
  scope text not null default 'telegram_verified'
    check (scope in ('telegram_verified', 'partner_verified')),
  status text not null default 'published'
    check (status in ('draft', 'published', 'archived')),
  kind text not null
    check (kind in ('article', 'review', 'partner')),
  title text,
  body_html text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, content_key)
);

alter table content_access_challenges enable row level security;
alter table content_access_sessions enable row level security;
alter table content_access_materials enable row level security;

drop policy if exists content_access_challenges_tenant_isolation on content_access_challenges;
create policy content_access_challenges_tenant_isolation on content_access_challenges
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists content_access_sessions_tenant_isolation on content_access_sessions;
create policy content_access_sessions_tenant_isolation on content_access_sessions
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists content_access_materials_tenant_isolation on content_access_materials;
create policy content_access_materials_tenant_isolation on content_access_materials
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

insert into content_access_materials (
  tenant_id, content_key, scope, status, kind, title, body_html
) values (
  'whieda',
  'article/krasnye-sledy-na-anionnyh-stelkah/appendix',
  'telegram_verified',
  'published',
  'article',
  'Практика: как осмотреть обувь и стельку',
  '<p>Этот блок открывается после подтверждения через Telegram. Публичная статья остаётся на месте: H1, лид и структура не прячутся.</p><ol><li>Снимите стельку и сравните обе стороны: след часто идёт там, где материал трётся о шов или подкладку, а не о стопу.</li><li>Проверьте обувь на краску, пыль, влагу и грубый шов. Стелька может окрашиваться от контакта, а не «сигналить сердцем».</li><li>Если есть боль, раздражение кожи или симптомы, которые реально тревожат — это повод к врачу, а не к расшифровке цвета.</li></ol><p>Документы и инструкция по носке — на карточке продукта, не в этом приложении.</p>'
)
on conflict (tenant_id, content_key) do nothing;

commit;
