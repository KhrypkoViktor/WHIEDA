-- User-submitted payment proofs for partner platform renewals.

begin;

create table if not exists partner_renewal_requests (
  request_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  actor_id text not null references lead_actors (actor_id),
  ref_code text not null,
  status text not null check (status in (
    'awaiting_period', 'awaiting_country', 'awaiting_payment',
    'pending_confirmation', 'confirmed', 'rejected', 'cancelled'
  )),
  access_months smallint check (access_months in (3, 6, 12)),
  country_code text check (country_code in ('BY', 'RU')),
  amount_minor bigint check (amount_minor > 0),
  currency text check (currency in ('RUB', 'WUSD')),
  proof_chat_id bigint,
  proof_message_id bigint,
  proof_file_id text,
  payment_id uuid references partner_payment_ledger (payment_id),
  confirmed_by_telegram_user_id bigint,
  confirmed_at timestamptz,
  rejected_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_partner_renewal_requests_open_actor
  on partner_renewal_requests (tenant_id, actor_id)
  where status not in ('confirmed', 'rejected', 'cancelled');

create index if not exists idx_partner_renewal_requests_queue
  on partner_renewal_requests (tenant_id, status, updated_at);

create or replace function partner_renewal_request_tenant_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if not exists (
    select 1 from public.lead_actors actor
    where actor.actor_id = new.actor_id and actor.tenant_id = new.tenant_id
  ) or not exists (
    select 1 from public.referral_profiles profile
    where profile.ref_code = new.ref_code and profile.tenant_id = new.tenant_id
      and profile.owner_id = new.actor_id
  ) then
    raise exception 'renewal request identity does not belong to tenant' using errcode = '23514';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_partner_renewal_request_tenant_guard on partner_renewal_requests;
create trigger trg_partner_renewal_request_tenant_guard
before insert or update of tenant_id, actor_id, ref_code on partner_renewal_requests
for each row execute function partner_renewal_request_tenant_guard();

alter table partner_renewal_requests enable row level security;

drop policy if exists partner_renewal_requests_tenant_isolation on partner_renewal_requests;
create policy partner_renewal_requests_tenant_isolation on partner_renewal_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
