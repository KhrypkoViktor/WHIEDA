-- Support: a second forum per bot for «site» tickets (owner, 26.09.2026).
--
-- «Поддержка» from the bot menu (/support, the word, the cabinet button) opens a
-- ticket of channel 'site' that goes to the owner and lives in its own topic
-- group where the partner's name and site are visible. Gemini («services»)
-- tickets keep the administrator's forum and stay anonymous (15.09.2026).
--
-- support_forums gets `kind` ('services' | 'site'): one row per bot and kind.
-- Existing rows (the administrator's forum) become kind = 'services'.
-- Additive, idempotent (the drop + add pairs re-create the same constraints).
-- No dollar signs in this file: production applies it through n8n.
begin;
set local lock_timeout = '5s';

alter table support_forums add column if not exists kind text not null default 'services';

alter table support_forums drop constraint if exists support_forums_kind_check;
alter table support_forums add constraint support_forums_kind_check
  check (kind in ('services', 'site'));

-- Primary key (tenant_id, binding_id) -> (tenant_id, binding_id, kind).
-- Nothing references this key; the table holds a handful of rows.
alter table support_forums drop constraint if exists support_forums_pkey;
alter table support_forums drop constraint if exists support_forums_binding_kind_pkey;
alter table support_forums add constraint support_forums_binding_kind_pkey
  primary key (tenant_id, binding_id, kind);

commit;
