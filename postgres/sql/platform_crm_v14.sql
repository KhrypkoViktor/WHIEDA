-- WWC CRM «Ежедневник партнёра» V14 (25.09.2026).
-- ТЗ: PROCESS/wwc-crm-20260925/TZ_IMPLEMENTER_V1.md, архитектура — решения 6 и 8
-- в PROCESS/wwc-platform-architecture-20260925/ARCHITECTURE_V1.md.
--
-- platform_accounts — человек как пользователь продуктов (не партнёр и не лид):
--   одна строка на (tenant_id, telegram_user_id), своя таймзона для утреннего
--   сообщения. На неё же позже ключуется прогресс Академии.
-- crm_contacts — знакомые партнёра: статус, следующий шаг и дата. Удаление
--   настоящее (заметки уходят каскадом): данные принадлежат партнёру.
-- crm_notes — лента заметок по контакту.
-- platform_outbox.due_at — плановые уведомления (утро CRM в 09:00 по времени
--   аккаунта). Таблицу раньше создавал только код (app/jobs/outbox.py); теперь
--   она создаётся и здесь (если её нет), чтобы колонка и индекс были до выката кода.
--
-- Идемпотентно. Знака доллара в файле нет: боевые правки идут через n8n.

begin;

create table if not exists platform_accounts (
  tenant_id text not null,
  account_id uuid not null default gen_random_uuid(),
  telegram_user_id bigint not null,
  timezone text not null default 'Europe/Moscow',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (account_id),
  unique (tenant_id, account_id),
  unique (tenant_id, telegram_user_id)
);

create table if not exists crm_contacts (
  tenant_id text not null,
  contact_id uuid not null default gen_random_uuid(),
  account_id uuid not null,
  name text not null check (length(btrim(name)) between 1 and 200),
  -- null, если номер не распознан; исходная строка — в phone_raw.
  phone_e164 text check (phone_e164 is null or phone_e164 ~ '^[+][0-9]{10,15}\Z'),
  phone_raw text,
  -- откуда знакомы, одна строка
  source text not null default '',
  status text not null default 'new'
    check (status in ('new', 'invited', 'presented', 'deciding', 'client', 'partner', 'paused')),
  next_step text check (next_step in ('invite', 'result', 'decide', 'ping', 'resume')),
  next_at date,
  meeting_at timestamptz,
  -- заявка с сайта (website_leads.lead_id) → одна карточка
  lead_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (contact_id),
  unique (tenant_id, contact_id),
  unique (tenant_id, lead_id),
  foreign key (tenant_id, account_id)
    references platform_accounts (tenant_id, account_id) on delete cascade
);

create index if not exists crm_contacts_account_next_at
  on crm_contacts (tenant_id, account_id, next_at);
create index if not exists crm_contacts_account_phone
  on crm_contacts (tenant_id, account_id, phone_e164);

create table if not exists crm_notes (
  tenant_id text not null,
  note_id uuid not null default gen_random_uuid(),
  contact_id uuid not null,
  body text not null check (length(btrim(body)) between 1 and 4000),
  created_at timestamptz not null default now(),
  primary key (note_id),
  foreign key (tenant_id, contact_id)
    references crm_contacts (tenant_id, contact_id) on delete cascade
);

create index if not exists crm_notes_contact_created
  on crm_notes (tenant_id, contact_id, created_at desc);

alter table platform_accounts enable row level security;
alter table crm_contacts enable row level security;
alter table crm_notes enable row level security;

drop policy if exists platform_accounts_tenant_isolation on platform_accounts;
create policy platform_accounts_tenant_isolation on platform_accounts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists crm_contacts_tenant_isolation on crm_contacts;
create policy crm_contacts_tenant_isolation on crm_contacts
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists crm_notes_tenant_isolation on crm_notes;
create policy crm_notes_tenant_isolation on crm_notes
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Плановые уведомления: та же таблица, что создаёт app/jobs/outbox.ensure_outbox_table.
create table if not exists platform_outbox (
  outbox_id bigserial primary key,
  tenant_id text not null,
  event_type text not null,
  idempotency_key text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending', 'processing', 'done', 'failed', 'dead')),
  attempts integer not null default 0,
  last_error text,
  due_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

alter table platform_outbox add column if not exists due_at timestamptz;

create index if not exists platform_outbox_due
  on platform_outbox (status, due_at)
  where due_at is not null;

-- CRM входит в продукты WHIEDA. Уже выключенную строку не включаем обратно.
insert into tenant_entitlements (tenant_id, feature_key, enabled)
values ('whieda', 'crm', true)
on conflict do nothing;

commit;
