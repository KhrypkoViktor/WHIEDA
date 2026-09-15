-- WWC P0.1 — tenant-aware leads, attribution, assignment and watchers.
-- Safe for the live runtime DB: only additive changes and explicit backfill.
begin;

create extension if not exists pgcrypto;

alter table website_leads
  add column if not exists first_ref_code text,
  add column if not exists active_ref_code text,
  add column if not exists attributed_owner_id text,
  add column if not exists delivery_status text not null default 'pending',
  add column if not exists first_touch_at timestamptz,
  add column if not exists last_touch_at timestamptz,
  add column if not exists ref_profile_version integer,
  add column if not exists service_location_id uuid,
  add column if not exists country_code text,
  add column if not exists city text,
  add column if not exists deleted_at timestamptz;

update website_leads
set
  first_ref_code = coalesce(nullif(first_ref_code, ''), nullif(initial_ref_code, ''), ''),
  active_ref_code = coalesce(nullif(active_ref_code, ''), nullif(metadata->>'active_ref', ''), nullif(initial_ref_code, ''), ''),
  attributed_owner_id = coalesce(nullif(attributed_owner_id, ''), assigned_owner_id),
  first_touch_at = coalesce(first_touch_at, created_at),
  last_touch_at = coalesce(last_touch_at, created_at)
where first_ref_code is null
   or active_ref_code is null
   or attributed_owner_id is null
   or first_touch_at is null
   or last_touch_at is null;

create index if not exists idx_website_leads_attributed_owner
  on website_leads (tenant_id, attributed_owner_id, created_at desc);
create index if not exists idx_website_leads_delivery
  on website_leads (tenant_id, delivery_status, created_at desc);

create table if not exists lead_actors (
  actor_id text primary key,
  tenant_id text not null,
  display_name text not null,
  telegram_chat_id text,
  telegram_username text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, telegram_chat_id)
);

create table if not exists lead_actor_roles (
  tenant_id text not null,
  actor_id text not null references lead_actors(actor_id) on delete cascade,
  role text not null check (role in ('platform_owner', 'tenant_admin', 'market_admin', 'referral_owner', 'lead_watcher')),
  country_code text,
  region_code text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (tenant_id, actor_id, role, country_code, region_code)
);

create table if not exists referral_profiles (
  ref_code text primary key,
  tenant_id text not null,
  owner_id text not null references lead_actors(actor_id),
  display_mode text not null check (display_mode in ('anonymous', 'named')),
  public_profile jsonb not null default '{}'::jsonb,
  country_code text,
  region_code text,
  enabled boolean not null default true,
  profile_version integer not null default 1,
  publication_consent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists service_locations (
  service_location_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  country_code text not null,
  city text not null,
  public_name text not null,
  address text,
  operator_actor_id text references lead_actors(actor_id),
  public_contact text,
  services jsonb not null default '[]'::jsonb,
  hours text,
  enabled boolean not null default false,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'website_leads_service_location_fk'
      and conrelid = 'website_leads'::regclass
  ) then
    alter table website_leads
      add constraint website_leads_service_location_fk
      foreign key (service_location_id) references service_locations(service_location_id)
      deferrable initially deferred;
  end if;
end $$;

create table if not exists website_lead_status_history (
  id bigserial primary key,
  lead_id uuid not null references website_leads(lead_id) on delete cascade,
  tenant_id text not null,
  old_status text,
  new_status text not null,
  changed_by_actor_id text,
  reason text,
  created_at timestamptz not null default now()
);
create index if not exists idx_lead_status_history
  on website_lead_status_history (lead_id, created_at);

alter table website_lead_owner_history
  add column if not exists old_assigned_owner_id text,
  add column if not exists new_assigned_owner_id text,
  add column if not exists old_attributed_owner_id text,
  add column if not exists new_attributed_owner_id text;

update website_lead_owner_history
set
  new_assigned_owner_id = coalesce(new_assigned_owner_id, owner_id),
  action = case when action = 'created' then 'assigned' else action end
where new_assigned_owner_id is null or action = 'created';

create table if not exists website_lead_watchers (
  id bigserial primary key,
  lead_id uuid not null references website_leads(lead_id) on delete cascade,
  tenant_id text not null,
  watcher_actor_id text not null references lead_actors(actor_id),
  scope text not null default 'lead',
  added_by_actor_id text,
  reason text,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  removed_at timestamptz,
  unique (lead_id, watcher_actor_id)
);

create table if not exists lead_visibility_rules (
  rule_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  watcher_actor_id text not null references lead_actors(actor_id),
  scope_type text not null check (scope_type in ('owner', 'ref', 'country', 'service_location', 'tenant')),
  scope_value text,
  event_type text not null default 'lead_created',
  delivery_mode text not null default 'full' check (delivery_mode in ('full', 'summary')),
  enabled boolean not null default true,
  created_by_actor_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_lead_visibility_rules_match
  on lead_visibility_rules (tenant_id, scope_type, scope_value, event_type)
  where enabled;

create table if not exists lead_delivery_attempts (
  delivery_id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references website_leads(lead_id) on delete cascade,
  tenant_id text not null,
  event_type text not null,
  channel text not null default 'telegram',
  recipient_actor_id text not null references lead_actors(actor_id),
  delivery_mode text not null default 'full',
  status text not null default 'pending' check (status in ('pending', 'sent', 'failed', 'skipped')),
  telegram_message_id text,
  error_text text,
  attempt_number integer not null default 1,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
create index if not exists idx_lead_delivery_retry
  on lead_delivery_attempts (tenant_id, status, created_at)
  where status in ('pending', 'failed');

create table if not exists website_events (
  event_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  event_type text not null check (event_type in ('visit', 'cta', 'form_start', 'submit', 'delivery', 'status_transition')),
  lead_id uuid references website_leads(lead_id) on delete set null,
  ref_code text,
  product_sku text,
  country_code text,
  city text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists referral_agreements (
  agreement_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  ref_code text references referral_profiles(ref_code),
  owner_id text references lead_actors(actor_id),
  compensation_type text,
  compensation_formula text,
  active_from date,
  active_to date,
  status text not null default 'draft',
  notes text,
  confirmed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into lead_actors (actor_id, tenant_id, display_name, telegram_username)
values
  ('viktor', 'whieda', 'Виктор Хрипко', 'sunraysword'),
  ('ladnaya', 'whieda', 'Анна Ладная', null),
  ('mariam', 'whieda', 'Mariam', null),
  ('onlineelena', 'whieda', 'Елена Дацкевич', 'onlineelena')
on conflict (actor_id) do update
set display_name = excluded.display_name,
    telegram_username = coalesce(lead_actors.telegram_username, excluded.telegram_username),
    updated_at = now();

insert into lead_actor_roles (tenant_id, actor_id, role, country_code, region_code)
values
  ('whieda', 'viktor', 'platform_owner', '', ''),
  ('whieda', 'viktor', 'tenant_admin', '', ''),
  ('whieda', 'viktor', 'referral_owner', '', ''),
  ('whieda', 'ladnaya', 'referral_owner', '', ''),
  ('whieda', 'mariam', 'referral_owner', '', ''),
  ('whieda', 'onlineelena', 'referral_owner', '', '')
on conflict do nothing;

insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled)
values
  ('nnm', 'whieda', 'viktor', 'anonymous', true),
  ('ladnaya', 'whieda', 'ladnaya', 'named', true),
  ('mariam', 'whieda', 'mariam', 'named', true),
  ('onlineelena', 'whieda', 'onlineelena', 'named', true)
on conflict (ref_code) do update
set owner_id = excluded.owner_id,
    display_mode = excluded.display_mode,
    enabled = excluded.enabled,
    updated_at = now();

commit;
