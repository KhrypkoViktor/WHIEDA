-- Durable Telegram inbox/outbox for Core webhook ACK-before-process.
-- Additive, idempotent. No seed, no NSP rows, no backfill.

begin;

create table if not exists telegram_update_inbox (
  inbox_id uuid primary key default gen_random_uuid(),
  binding_id text not null references tenant_bot_bindings (binding_id),
  tenant_id text not null references tenants (tenant_id),
  telegram_update_id bigint not null,
  payload jsonb not null,
  state text not null default 'pending'
    check (state in ('pending', 'leased', 'processed', 'failed')),
  received_at timestamptz not null default now(),
  lease_owner text,
  lease_until timestamptz,
  processed_at timestamptz,
  retry_count integer not null default 0,
  last_error text,
  unique (binding_id, telegram_update_id)
);

create table if not exists telegram_delivery_outbox (
  outbox_id uuid primary key default gen_random_uuid(),
  inbox_id uuid not null references telegram_update_inbox (inbox_id),
  binding_id text not null,
  tenant_id text not null,
  idempotency_key text not null,
  delivery_kind text not null default 'business_reply',
  state text not null default 'pending'
    check (state in ('pending', 'sent', 'failed')),
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  last_error text,
  unique (inbox_id, idempotency_key)
);

create index if not exists telegram_update_inbox_pending_idx
  on telegram_update_inbox (received_at)
  where state = 'pending';

create index if not exists telegram_update_inbox_lease_idx
  on telegram_update_inbox (lease_until)
  where state = 'leased';

alter table telegram_update_inbox enable row level security;
alter table telegram_delivery_outbox enable row level security;

drop policy if exists telegram_update_inbox_tenant_isolation on telegram_update_inbox;
create policy telegram_update_inbox_tenant_isolation on telegram_update_inbox
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists telegram_delivery_outbox_tenant_isolation on telegram_delivery_outbox;
create policy telegram_delivery_outbox_tenant_isolation on telegram_delivery_outbox
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

create or replace function telegram_inbox_enqueue(
  p_binding_id text,
  p_tenant_id text,
  p_telegram_update_id bigint,
  p_payload jsonb
)
returns table (
  inbox_id uuid,
  inserted boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_binding_tenant text;
  v_inbox_id uuid;
  v_inserted boolean := false;
begin
  if p_binding_id is null or btrim(p_binding_id) = '' then
    raise exception 'binding_id required';
  end if;
  if p_tenant_id is null or btrim(p_tenant_id) = '' then
    raise exception 'tenant_id required';
  end if;
  if p_telegram_update_id is null then
    raise exception 'telegram_update_id required';
  end if;

  select b.tenant_id
    into v_binding_tenant
  from tenant_bot_bindings b
  where b.binding_id = p_binding_id
    and b.status = 'active';

  if v_binding_tenant is null or v_binding_tenant is distinct from p_tenant_id then
    raise exception 'inbox_binding_mismatch';
  end if;

  insert into telegram_update_inbox (
    binding_id, tenant_id, telegram_update_id, payload, state
  ) values (
    p_binding_id, p_tenant_id, p_telegram_update_id, coalesce(p_payload, '{}'::jsonb), 'pending'
  )
  on conflict (binding_id, telegram_update_id) do nothing
  returning telegram_update_inbox.inbox_id into v_inbox_id;

  v_inserted := v_inbox_id is not null;

  if v_inbox_id is null then
    select i.inbox_id
      into v_inbox_id
    from telegram_update_inbox i
    where i.binding_id = p_binding_id
      and i.telegram_update_id = p_telegram_update_id;
  end if;

  return query select v_inbox_id, v_inserted;
end;
$$;

create or replace function telegram_inbox_claim(
  p_owner text,
  p_lease_seconds integer default 30
)
returns table (
  inbox_id uuid,
  binding_id text,
  tenant_id text,
  telegram_update_id bigint,
  payload jsonb,
  retry_count integer,
  state text,
  lease_owner text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with picked as (
    select i.inbox_id
    from telegram_update_inbox i
    where i.state = 'pending'
       or (i.state = 'leased' and i.lease_until is not null and i.lease_until < now())
    order by i.received_at
    for update skip locked
    limit 1
  )
  update telegram_update_inbox i
  set state = 'leased',
      lease_owner = p_owner,
      lease_until = now() + make_interval(secs => greatest(coalesce(p_lease_seconds, 30), 1))
  from picked
  where i.inbox_id = picked.inbox_id
  returning i.inbox_id, i.binding_id, i.tenant_id, i.telegram_update_id,
            i.payload, i.retry_count, i.state, i.lease_owner;
end;
$$;

create or replace function telegram_inbox_claim_by_id(
  p_inbox_id uuid,
  p_owner text,
  p_lease_seconds integer default 30
)
returns table (
  inbox_id uuid,
  binding_id text,
  tenant_id text,
  telegram_update_id bigint,
  payload jsonb,
  retry_count integer,
  state text,
  lease_owner text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with picked as (
    select i.inbox_id
    from telegram_update_inbox i
    where i.inbox_id = p_inbox_id
      and (
        i.state = 'pending'
        or (i.state = 'leased' and i.lease_until is not null and i.lease_until < now())
      )
    for update skip locked
    limit 1
  )
  update telegram_update_inbox i
  set state = 'leased',
      lease_owner = p_owner,
      lease_until = now() + make_interval(secs => greatest(coalesce(p_lease_seconds, 30), 1))
  from picked
  where i.inbox_id = picked.inbox_id
  returning i.inbox_id, i.binding_id, i.tenant_id, i.telegram_update_id,
            i.payload, i.retry_count, i.state, i.lease_owner;
end;
$$;

create or replace function telegram_inbox_mark_processed(p_inbox_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update telegram_update_inbox
  set state = 'processed',
      processed_at = now(),
      lease_owner = null,
      lease_until = null,
      last_error = null
  where inbox_id = p_inbox_id;
end;
$$;

create or replace function telegram_inbox_release_for_retry(
  p_inbox_id uuid,
  p_error text,
  p_max_retries integer default 8
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_retries integer;
begin
  update telegram_update_inbox
  set retry_count = retry_count + 1,
      last_error = left(coalesce(p_error, 'retry'), 300),
      lease_owner = null,
      lease_until = null,
      state = case
        when retry_count + 1 >= greatest(coalesce(p_max_retries, 8), 1) then 'failed'
        else 'pending'
      end
  where inbox_id = p_inbox_id
  returning retry_count into v_retries;
end;
$$;

create or replace function telegram_outbox_reserve(
  p_inbox_id uuid,
  p_binding_id text,
  p_tenant_id text,
  p_idempotency_key text
)
returns table (
  outbox_id uuid,
  state text
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_outbox_id uuid;
  v_state text;
begin
  insert into telegram_delivery_outbox (
    inbox_id, binding_id, tenant_id, idempotency_key, delivery_kind, state
  ) values (
    p_inbox_id, p_binding_id, p_tenant_id, p_idempotency_key, 'business_reply', 'pending'
  )
  on conflict (inbox_id, idempotency_key) do nothing
  returning telegram_delivery_outbox.outbox_id, telegram_delivery_outbox.state
    into v_outbox_id, v_state;

  if v_outbox_id is null then
    select o.outbox_id, o.state
      into v_outbox_id, v_state
    from telegram_delivery_outbox o
    where o.inbox_id = p_inbox_id
      and o.idempotency_key = p_idempotency_key;
  end if;

  return query select v_outbox_id, v_state;
end;
$$;

create or replace function telegram_outbox_mark_sent(p_outbox_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update telegram_delivery_outbox
  set state = 'sent',
      sent_at = now(),
      last_error = null
  where outbox_id = p_outbox_id;
end;
$$;

commit;
