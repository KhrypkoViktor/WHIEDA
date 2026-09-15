-- Additive durable Telegram delivery outbox (photo then text, lease, unknown).
-- Does not recreate telegram_delivery_outbox; extends Gate C table.
-- No seed, no NSP rows, no production apply.

begin;

alter table telegram_delivery_outbox
  add column if not exists telegram_update_id bigint,
  add column if not exists sequence_no integer not null default 1,
  add column if not exists kind text not null default 'text',
  add column if not exists payload_json jsonb not null default '{}'::jsonb,
  add column if not exists status text not null default 'pending',
  add column if not exists lease_until timestamptz,
  add column if not exists lease_owner text,
  add column if not exists attempt_count integer not null default 0,
  add column if not exists last_error_code text,
  add column if not exists updated_at timestamptz not null default now();

update telegram_delivery_outbox o
set telegram_update_id = i.telegram_update_id,
    status = case o.state
      when 'sent' then 'sent'
      when 'failed' then 'dead'
      else coalesce(nullif(o.status, ''), 'pending')
    end,
    updated_at = now()
from telegram_update_inbox i
where o.inbox_id = i.inbox_id
  and o.telegram_update_id is null;

delete from telegram_delivery_outbox where telegram_update_id is null;

alter table telegram_delivery_outbox
  alter column telegram_update_id set not null;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'telegram_delivery_outbox_kind_check'
  ) then
    alter table telegram_delivery_outbox
      add constraint telegram_delivery_outbox_kind_check
      check (kind in ('text', 'photo'));
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'telegram_delivery_outbox_status_check'
  ) then
    alter table telegram_delivery_outbox
      add constraint telegram_delivery_outbox_status_check
      check (status in (
        'pending', 'leased', 'sent', 'retryable_failed', 'unknown_delivery', 'dead'
      ));
  end if;
end $$;

alter table telegram_delivery_outbox drop constraint if exists telegram_delivery_outbox_state_check;
alter table telegram_delivery_outbox
  add constraint telegram_delivery_outbox_state_check
  check (state in (
    'pending', 'leased', 'sent', 'failed', 'retryable_failed', 'unknown_delivery', 'dead'
  ));

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'telegram_delivery_outbox_binding_update_seq'
  ) then
    alter table telegram_delivery_outbox
      add constraint telegram_delivery_outbox_binding_update_seq
      unique (binding_id, telegram_update_id, sequence_no);
  end if;
end $$;

create index if not exists telegram_delivery_outbox_claim_idx
  on telegram_delivery_outbox (binding_id, created_at)
  where status in ('pending', 'leased', 'retryable_failed');

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
  v_update_id bigint;
  v_tenant text;
begin
  select i.telegram_update_id, i.tenant_id
    into v_update_id, v_tenant
  from telegram_update_inbox i
  where i.inbox_id = p_inbox_id
    and i.binding_id = p_binding_id;

  if v_update_id is null or v_tenant is distinct from p_tenant_id then
    raise exception 'outbox_inbox_mismatch';
  end if;

  insert into telegram_delivery_outbox (
    inbox_id, binding_id, tenant_id, telegram_update_id, sequence_no,
    kind, payload_json, idempotency_key, delivery_kind, state, status
  ) values (
    p_inbox_id, p_binding_id, p_tenant_id, v_update_id, 1,
    'text', '{}'::jsonb, p_idempotency_key, 'business_reply', 'pending', 'pending'
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

create or replace function telegram_outbox_enqueue_items(
  p_inbox_id uuid,
  p_binding_id text,
  p_tenant_id text,
  p_telegram_update_id bigint,
  p_items jsonb
)
returns table (
  outbox_id uuid,
  sequence_no integer,
  inserted boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_inbox_binding text;
  v_inbox_tenant text;
  v_inbox_update bigint;
  v_item jsonb;
  v_ord integer;
  v_seq integer;
  v_kind text;
  v_key text;
  v_payload jsonb;
  v_outbox_id uuid;
  v_inserted boolean;
begin
  if p_binding_id is null or btrim(p_binding_id) = '' then
    raise exception 'binding_id required';
  end if;
  if p_tenant_id is null or btrim(p_tenant_id) = '' then
    raise exception 'tenant_id required';
  end if;

  select i.binding_id, i.tenant_id, i.telegram_update_id
    into v_inbox_binding, v_inbox_tenant, v_inbox_update
  from telegram_update_inbox i
  where i.inbox_id = p_inbox_id;

  if v_inbox_binding is distinct from p_binding_id
     or v_inbox_tenant is distinct from p_tenant_id
     or v_inbox_update is distinct from p_telegram_update_id then
    raise exception 'outbox_inbox_mismatch';
  end if;

  for v_item, v_ord in
    select t.item, t.ord::integer
    from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) with ordinality as t(item, ord)
  loop
    v_seq := coalesce(nullif(v_item->>'sequence_no', '')::integer, v_ord);
    v_kind := coalesce(nullif(v_item->>'kind', ''), 'text');
    if v_kind not in ('text', 'photo') then
      raise exception 'outbox_kind_invalid';
    end if;
    v_payload := coalesce(v_item->'payload', '{}'::jsonb);
    v_key := coalesce(
      nullif(v_item->>'idempotency_key', ''),
      p_inbox_id::text || ':' || v_seq::text || ':' || v_kind
    );
    v_inserted := false;
    insert into telegram_delivery_outbox (
      inbox_id, binding_id, tenant_id, telegram_update_id, sequence_no,
      kind, payload_json, idempotency_key, delivery_kind, state, status
    ) values (
      p_inbox_id, p_binding_id, p_tenant_id, p_telegram_update_id, v_seq,
      v_kind, v_payload, v_key, 'delivery_item', 'pending', 'pending'
    )
    on conflict on constraint telegram_delivery_outbox_binding_update_seq do nothing
    returning telegram_delivery_outbox.outbox_id into v_outbox_id;
    if v_outbox_id is null then
      select o.outbox_id
        into v_outbox_id
      from telegram_delivery_outbox o
      where o.binding_id = p_binding_id
        and o.telegram_update_id = p_telegram_update_id
        and o.sequence_no = v_seq;
    else
      v_inserted := true;
    end if;
    outbox_id := v_outbox_id;
    sequence_no := v_seq;
    inserted := v_inserted;
    return next;
  end loop;
end;
$$;

create or replace function telegram_outbox_claim_next(
  p_owner text,
  p_lease_seconds integer default 30,
  p_binding_id text default null
)
returns table (
  outbox_id uuid,
  inbox_id uuid,
  binding_id text,
  tenant_id text,
  telegram_update_id bigint,
  sequence_no integer,
  kind text,
  payload_json jsonb,
  status text,
  attempt_count integer,
  idempotency_key text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with picked as (
    select o.outbox_id
    from telegram_delivery_outbox o
    where (
        o.status = 'pending'
        or (o.status = 'retryable_failed' and (o.lease_until is null or o.lease_until <= now()))
        or (o.status = 'leased' and o.lease_until is not null and o.lease_until < now())
      )
      and (p_binding_id is null or o.binding_id = p_binding_id)
      and not exists (
        select 1
        from telegram_delivery_outbox earlier
        where earlier.binding_id = o.binding_id
          and earlier.telegram_update_id = o.telegram_update_id
          and earlier.sequence_no < o.sequence_no
          and earlier.status in ('pending', 'leased', 'retryable_failed', 'unknown_delivery')
      )
    order by o.created_at, o.sequence_no
    for update skip locked
    limit 1
  )
  update telegram_delivery_outbox o
  set status = 'leased',
      state = 'leased',
      lease_owner = p_owner,
      lease_until = now() + make_interval(secs => greatest(coalesce(p_lease_seconds, 30), 1)),
      updated_at = now()
  from picked
  where o.outbox_id = picked.outbox_id
  returning o.outbox_id, o.inbox_id, o.binding_id, o.tenant_id, o.telegram_update_id,
            o.sequence_no, o.kind, o.payload_json, o.status, o.attempt_count,
            o.idempotency_key;
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
  set status = 'sent',
      state = 'sent',
      sent_at = now(),
      lease_owner = null,
      lease_until = null,
      last_error = null,
      last_error_code = null,
      updated_at = now()
  where outbox_id = p_outbox_id;
end;
$$;

create or replace function telegram_outbox_mark_retryable(
  p_outbox_id uuid,
  p_error_code text,
  p_max_attempts integer default 8,
  p_backoff_seconds integer default 2
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_attempts integer;
  v_status text;
begin
  update telegram_delivery_outbox
  set attempt_count = attempt_count + 1,
      last_error_code = left(coalesce(p_error_code, 'retry'), 80),
      last_error = left(coalesce(p_error_code, 'retry'), 300),
      lease_owner = null,
      updated_at = now()
  where outbox_id = p_outbox_id
  returning attempt_count into v_attempts;

  if v_attempts >= greatest(coalesce(p_max_attempts, 8), 1) then
    v_status := 'dead';
  else
    v_status := 'retryable_failed';
  end if;

  update telegram_delivery_outbox
  set status = v_status,
      state = v_status,
      lease_until = case
        when v_status = 'retryable_failed'
          then now() + make_interval(secs => greatest(coalesce(p_backoff_seconds, 2), 1))
        else null
      end
  where outbox_id = p_outbox_id;

  return v_status;
end;
$$;

create or replace function telegram_outbox_mark_unknown(
  p_outbox_id uuid,
  p_error_code text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update telegram_delivery_outbox
  set status = 'unknown_delivery',
      state = 'unknown_delivery',
      attempt_count = attempt_count + 1,
      last_error_code = left(coalesce(p_error_code, 'unknown'), 80),
      last_error = left(coalesce(p_error_code, 'unknown'), 300),
      lease_owner = null,
      lease_until = null,
      updated_at = now()
  where outbox_id = p_outbox_id;
end;
$$;

commit;
