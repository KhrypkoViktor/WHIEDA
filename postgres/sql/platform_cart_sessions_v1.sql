-- Core cart sessions (Release B). Additive. Tenant RLS required.
-- Prices are never stored; items are sku+qty only. Totals are recalculated on read.

begin;

create table if not exists platform_cart_sessions (
  tenant_id text not null,
  cart_session_id text not null,
  market_id text not null check (market_id in ('ru', 'by', 'global')),
  price_mode text not null default 'primary'
    check (price_mode in ('primary', 'repeat')),
  first_ref text,
  active_ref text,
  visitor_session_id text,
  items jsonb not null default '[]'::jsonb,
  item_idempotency jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, cart_session_id)
);

create index if not exists idx_platform_cart_sessions_updated
  on platform_cart_sessions (tenant_id, updated_at desc);

create table if not exists platform_cart_snapshots (
  tenant_id text not null,
  snapshot_token_hash text not null,
  cart_session_id text,
  payload jsonb not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, snapshot_token_hash)
);

alter table platform_cart_sessions enable row level security;
alter table platform_cart_snapshots enable row level security;

do $$
begin
  if exists (
    select 1 from pg_proc where proname = 'platform_current_tenant_id'
  ) then
    execute 'drop policy if exists platform_cart_sessions_tenant_isolation on platform_cart_sessions';
    execute $p$
      create policy platform_cart_sessions_tenant_isolation on platform_cart_sessions
        using (tenant_id = platform_current_tenant_id())
        with check (tenant_id = platform_current_tenant_id())
    $p$;
    execute 'drop policy if exists platform_cart_snapshots_tenant_isolation on platform_cart_snapshots';
    execute $p$
      create policy platform_cart_snapshots_tenant_isolation on platform_cart_snapshots
        using (tenant_id = platform_current_tenant_id())
        with check (tenant_id = platform_current_tenant_id())
    $p$;
  end if;
end $$;

commit;
