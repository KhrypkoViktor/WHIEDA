-- WWC CRM v2 V20 (02.10.2026). ТЗ: PROCESS/wwc-crm-v2-20261002/TASK.md, раздел 4.
--
-- crm_activities — лента карточки: заметка, смена статуса, шаг (сделано / перенос /
--   вручную), звонок, сообщение, встреча, создание, повторная заявка с сайта.
--   Текст заметок по-прежнему в crm_notes; activity 'note' хранит только note_id.
--   Backfill: одна 'created' на каждую карточку и одна 'note' на каждую заметку;
--   уникальные частичные индексы делают его идемпотентным (on conflict do nothing).
--   Повторный прогон после выката кода добивает карточки и заметки, которые старая
--   сборка успела создать без activities.
-- crm_contacts + tags (метки), priority (0/1, «звезда»), last_touch_at (последний
--   звонок / сообщение / заметка / встреча), meeting_reminded_at (напоминание за час
--   уже поставлено в очередь), deleted_at (мягкое удаление: «Вернуть» 24 часа,
--   потом воркер стирает карточку вместе с заметками и лентой).
-- crm_templates — шаблоны сообщений партнёра; шесть по умолчанию код кладёт при
--   первом обращении (platform_accounts.crm_templates_seeded_at — чтобы удалённые
--   партнёром шаблоны не возвращались).
-- platform_accounts + crm_install_hint_dismissed_at (баннер «Установить приложение»).
-- Поиск — ILIKE без pg_trgm (общая база): индекс по lower(name) для сортировки
--   «по имени»; по phone_e164 индекс есть с V14 (crm_contacts_account_phone).
--
-- Идемпотентно. Знака доллара в файле нет: боевые правки идут через n8n.

begin;

-- ALTER crm_contacts не должен висеть за долгой транзакцией: упасть и повторить.
set local lock_timeout = '5s';

alter table crm_contacts add column if not exists tags text[] not null default '{}';
alter table crm_contacts add column if not exists priority smallint not null default 0;
alter table crm_contacts add column if not exists last_touch_at timestamptz;
alter table crm_contacts add column if not exists meeting_reminded_at timestamptz;
alter table crm_contacts add column if not exists deleted_at timestamptz;

alter table crm_contacts drop constraint if exists crm_contacts_priority_check;
alter table crm_contacts add constraint crm_contacts_priority_check check (priority in (0, 1));
-- Код пускает до 10 меток по 32 символа; база держит грубую границу.
alter table crm_contacts drop constraint if exists crm_contacts_tags_check;
alter table crm_contacts add constraint crm_contacts_tags_check check (cardinality(tags) <= 20);

alter table platform_accounts add column if not exists crm_install_hint_dismissed_at timestamptz;
alter table platform_accounts add column if not exists crm_templates_seeded_at timestamptz;

create index if not exists crm_contacts_account_name
  on crm_contacts (tenant_id, account_id, lower(name), contact_id);
-- Планировщик напоминаний о встрече (раз в 5 минут по всем аккаунтам тенанта).
create index if not exists crm_contacts_meeting_due
  on crm_contacts (tenant_id, meeting_at)
  where meeting_at is not null and meeting_reminded_at is null and deleted_at is null;
-- Очистка удалённых карточек после окна «Вернуть».
create index if not exists crm_contacts_deleted
  on crm_contacts (tenant_id, deleted_at)
  where deleted_at is not null;

create table if not exists crm_activities (
  tenant_id text not null,
  activity_id uuid not null default gen_random_uuid(),
  account_id uuid not null,
  contact_id uuid not null,
  kind text not null
    check (kind in ('note', 'status', 'step', 'call', 'message', 'meeting', 'created', 'lead')),
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (activity_id),
  foreign key (tenant_id, contact_id)
    references crm_contacts (tenant_id, contact_id) on delete cascade
);

create index if not exists crm_activities_contact_created
  on crm_activities (tenant_id, contact_id, created_at desc, activity_id desc);
create unique index if not exists crm_activities_created_once
  on crm_activities (tenant_id, contact_id)
  where kind = 'created';
create unique index if not exists crm_activities_note_once
  on crm_activities (tenant_id, (payload ->> 'note_id'))
  where kind = 'note';

create table if not exists crm_templates (
  tenant_id text not null,
  template_id uuid not null default gen_random_uuid(),
  account_id uuid not null,
  title text not null check (length(btrim(title)) between 1 and 60),
  body text not null check (length(btrim(body)) between 1 and 1000),
  position integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (template_id),
  unique (tenant_id, template_id),
  foreign key (tenant_id, account_id)
    references platform_accounts (tenant_id, account_id) on delete cascade
);

create index if not exists crm_templates_account_position
  on crm_templates (tenant_id, account_id, position, created_at);

alter table crm_activities enable row level security;
alter table crm_templates enable row level security;

drop policy if exists crm_activities_tenant_isolation on crm_activities;
create policy crm_activities_tenant_isolation on crm_activities
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

drop policy if exists crm_templates_tenant_isolation on crm_templates;
create policy crm_templates_tenant_isolation on crm_templates
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

-- Backfill ленты: «создан» на каждую карточку (заявка с сайта — source 'site').
insert into crm_activities (tenant_id, account_id, contact_id, kind, payload, created_at)
select c.tenant_id, c.account_id, c.contact_id, 'created',
       jsonb_strip_nulls(jsonb_build_object(
         'source', case when c.lead_id is not null then 'site' end,
         'backfill', true
       )),
       c.created_at
from crm_contacts c
on conflict do nothing;

-- … и 'note' на каждую существующую заметку (текст остаётся в crm_notes).
insert into crm_activities (tenant_id, account_id, contact_id, kind, payload, created_at)
select n.tenant_id, c.account_id, n.contact_id, 'note',
       jsonb_build_object('note_id', n.note_id::text, 'backfill', true),
       n.created_at
from crm_notes n
join crm_contacts c on c.tenant_id = n.tenant_id and c.contact_id = n.contact_id
on conflict do nothing;

commit;
