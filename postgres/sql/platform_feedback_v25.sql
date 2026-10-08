-- WWC: пожелания от всех — в бота, тему «Пожелания» и таблицу учёта (V25, 08.10.2026).
--
-- Владелец, 06–08.10.2026: «нужно в боте собирать обратную связь, пожелания…
-- выведи в основную таблицу»; «открой всем» — партнёрам и покупателям.
-- Человек жмёт «Пожелание» (Telegram или Max), пишет текстом, голосом или со
-- скриншотом — пожелание уходит в тему «Пожелания» группы WWC Support с кнопками
-- «В работу» / «Сделано» / «Не будем»; на «Сделано» человек получает ответ.
-- Таблица учёта читает partner_feedback (вкладка «Пожелания»).

begin;

create table if not exists partner_feedback (
  feedback_id uuid primary key default gen_random_uuid(),
  feedback_no bigint generated always as identity,
  tenant_id text not null references tenants (tenant_id),
  channel text not null default 'telegram' check (channel in ('telegram', 'max')),
  user_id bigint not null,
  chat_id bigint not null,
  user_display text,
  ref_code text,
  status text not null default 'awaiting'
    check (status in ('awaiting', 'new', 'in_progress', 'done', 'declined', 'expired')),
  text text check (text is null or length(text) <= 4000),
  file_id text,
  media_kind text,
  source text not null default 'bot' check (source in ('bot', 'support', 'manual')),
  forum_chat_id bigint,
  forum_message_id bigint,
  owner_note text,
  decided_by bigint,
  decided_at timestamptz,
  notified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists partner_feedback_no_idx on partner_feedback (feedback_no);
-- Одно ожидание на человека в канале: «Пожелание» нажали ещё раз — то же ожидание.
create unique index if not exists partner_feedback_awaiting_idx
  on partner_feedback (tenant_id, channel, user_id) where status = 'awaiting';
create index if not exists partner_feedback_status_idx on partner_feedback (tenant_id, status, created_at desc);

alter table support_forums add column if not exists wishes_thread_id bigint;

alter table partner_feedback enable row level security;
drop policy if exists partner_feedback_tenant_isolation on partner_feedback;
create policy partner_feedback_tenant_isolation on partner_feedback
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
