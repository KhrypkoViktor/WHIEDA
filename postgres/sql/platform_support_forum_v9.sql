-- Support tunnel in a Telegram forum group: one topic per ticket, so the
-- administrator answers inside the topic instead of using Reply in a single
-- crowded chat (owner, 15.09.2026). Additive, idempotent.
--
-- The forum is registered per bot binding: staging and production share this
-- database but run different bots, and each bot may sit in its own group.
begin;

create table if not exists support_forums (
  tenant_id text not null references tenants (tenant_id),
  binding_id text not null,
  chat_id bigint not null,
  title text,
  registered_by_telegram_user_id bigint not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, binding_id)
);

alter table support_tickets add column if not exists forum_chat_id bigint;
alter table support_tickets add column if not exists forum_thread_id bigint;

create index if not exists idx_support_tickets_forum_thread
  on support_tickets (tenant_id, forum_chat_id, forum_thread_id)
  where forum_thread_id is not null;

alter table support_forums enable row level security;
drop policy if exists support_forums_tenant_isolation on support_forums;
create policy support_forums_tenant_isolation on support_forums
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
