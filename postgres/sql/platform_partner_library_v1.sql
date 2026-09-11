begin;

create table if not exists partner_library_items (
  item_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  slug text not null,
  category text not null,
  title text not null,
  description text,
  kind text not null,
  storage_key text not null,
  mime_type text not null,
  size_bytes bigint check (size_bytes is null or size_bytes >= 0),
  status text not null check (status in ('draft', 'published', 'archived')),
  sort_order integer not null default 0,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, slug)
);

create index if not exists idx_partner_library_items_listing
  on partner_library_items (tenant_id, status, category, sort_order, title);

alter table partner_library_items enable row level security;

drop policy if exists partner_library_items_tenant_isolation
  on partner_library_items;
create policy partner_library_items_tenant_isolation on partner_library_items
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
