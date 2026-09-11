-- WWC partner site request and payment-proof queue V4.

begin;

create table if not exists partner_site_requests (
  request_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null references lead_actors (actor_id),
  status text not null check (status in (
    'awaiting_country', 'awaiting_subdomain', 'awaiting_photo', 'awaiting_text',
    'awaiting_payment', 'pending_confirmation', 'pending_provisioning',
    'provisioned', 'rejected', 'cancelled'
  )),
  country_code text check (country_code in ('BY', 'RU')),
  requested_subdomain text,
  profile_photo_file_id text,
  intro_text text,
  total_amount_minor bigint,
  subscription_amount_minor bigint,
  currency text check (currency in ('RUB', 'WUSD')),
  proof_chat_id bigint,
  proof_message_id bigint,
  proof_file_id text,
  subscription_payment_id uuid references partner_payment_ledger (payment_id),
  confirmed_by_telegram_user_id bigint,
  confirmed_at timestamptz,
  rejected_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (requested_subdomain is null or requested_subdomain ~ '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$'),
  check (intro_text is null or length(intro_text) <= 3000)
);

create unique index if not exists uq_partner_site_requests_open_actor
  on partner_site_requests (tenant_id, actor_id)
  where status not in ('provisioned', 'rejected', 'cancelled');

create unique index if not exists uq_partner_site_requests_open_subdomain
  on partner_site_requests (tenant_id, requested_subdomain)
  where requested_subdomain is not null
    and status not in ('rejected', 'cancelled');

create index if not exists idx_partner_site_requests_queue
  on partner_site_requests (tenant_id, status, updated_at);

create or replace function partner_site_request_tenant_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1 from public.lead_actors actor
    where actor.actor_id = new.actor_id and actor.tenant_id = new.tenant_id
  ) then
    raise exception 'site request actor does not belong to tenant' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_partner_site_request_tenant_guard on partner_site_requests;
create trigger trg_partner_site_request_tenant_guard
before insert or update of tenant_id, actor_id on partner_site_requests
for each row execute function partner_site_request_tenant_guard();

alter table partner_site_requests enable row level security;

drop policy if exists partner_site_requests_tenant_isolation on partner_site_requests;
create policy partner_site_requests_tenant_isolation on partner_site_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
