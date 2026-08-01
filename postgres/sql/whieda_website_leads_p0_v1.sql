-- P0 website leads for WHIEDA / WWC.
-- Apply only after a read-only schema audit of the live product database.
-- Every private entity is tenant-scoped. The original referral is immutable.

begin;

create extension if not exists pgcrypto;

create table if not exists website_leads (
  lead_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  public_id text not null unique default ('L-' || to_char(now(), 'YYYYMMDD') || '-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 7))),
  status text not null default 'new' check (status in ('new', 'contacted', 'qualified', 'won', 'lost', 'spam')),
  name text not null,
  contact text not null,
  comment text,
  product_name text not null,
  product_sku text,
  product_variant text,
  page_url text,
  initial_ref_code text,
  assigned_owner_id text not null,
  source_type text not null default 'website_order',
  idempotency_key text not null,
  consent_at timestamptz not null default now(),
  consent_version text not null default 'website-order-v1',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

create index if not exists idx_website_leads_queue on website_leads (tenant_id, status, created_at desc);
create index if not exists idx_website_leads_owner on website_leads (tenant_id, assigned_owner_id, created_at desc);
create index if not exists idx_website_leads_initial_ref on website_leads (tenant_id, initial_ref_code, created_at desc);

create table if not exists website_lead_owner_history (
  id bigserial primary key,
  lead_id uuid not null references website_leads(lead_id) on delete cascade,
  tenant_id text not null,
  owner_id text not null,
  action text not null check (action in ('created', 'reassigned')),
  changed_by text not null,
  reason text,
  created_at timestamptz not null default now()
);

create index if not exists idx_website_lead_owner_history_lead on website_lead_owner_history (lead_id, created_at);

create table if not exists website_content_register (
  content_id text primary key,
  tenant_id text not null,
  source_type text not null check (source_type in ('official', 'marketing', 'testimonial', 'research', 'internal')),
  source_url_or_file text,
  status text not null default 'draft' check (status in ('draft', 'review', 'approved', 'archived')),
  owner text,
  notes text,
  updated_at timestamptz not null default now()
);

commit;
