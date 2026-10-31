-- WWC: заявки на личный разбор V47 (31.10.2026).
-- Код — app/funnel/consult.py (два вопроса кнопками, выбор ведущего, заявка, уведомление ведущему) и
-- app/funnel/system_word.py (кнопки в Telegram и Max). Заявка заводится после выбора ведущего: «Виктор» или «Елена».
--
--   consult_requests — одна строка на одну заявку человека к одному ведущему.
--   lead_id          — заявка website_leads.lead_id (uuid), если человек пришёл из заявки на Живую встречу; пусто, если нет.
--                      Без внешнего ключа: в testkit таблицы website_leads нет (как у meeting_registrations, V46).
--   channel          — 'telegram' или 'max': где человек нажал кнопки и где ему писать.
--   channel_user_id  — id человека в этом мессенджере (число).
--   display_name     — имя из профиля мессенджера (или имя из заявки, если в профиле пусто); только для ведущего.
--   host             — 'viktor' или 'elena': к кому человек хочет на разбор.
--   answers          — ответы на два вопроса, jsonb: {"who": "net|sell|biz|look", "goal": "money|team|product|system"}.
--   status           — new (новая) | contacted (ведущий написал) | done (разбор проведён) | cancelled (отменена).
--                      Статусы меняет ведущий вручную; повторная заявка тому же ведущему за 7 дней не заводится,
--                      пока предыдущая в статусе new или contacted.
--   notified_at      — когда сообщение ведущему поставлено в очередь platform_outbox; пусто, пока не поставлено.
--
-- Один арендатор видит только свои строки (RLS, как у соседних таблиц).
-- Только create if not exists и пересоздание своей политики: повторный прогон безопасен.
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py) и через n8n,
-- а он режет этот знак.

begin;

set local lock_timeout = '5s';

create table if not exists consult_requests (
  request_id bigserial primary key,
  tenant_id text not null,
  lead_id uuid,
  channel text not null check (channel in ('telegram', 'max')),
  channel_user_id bigint not null,
  display_name text,
  host text not null check (host in ('viktor', 'elena')),
  answers jsonb not null default '{}'::jsonb,
  status text not null default 'new' check (status in ('new', 'contacted', 'done', 'cancelled')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  notified_at timestamptz
);

create index if not exists idx_consult_requests_status
  on consult_requests (tenant_id, status, created_at);

-- Повторная заявка того же человека тому же ведущему за 7 дней.
create index if not exists idx_consult_requests_person
  on consult_requests (tenant_id, channel, channel_user_id, host, created_at);

alter table consult_requests enable row level security;

drop policy if exists consult_requests_tenant_isolation on consult_requests;
create policy consult_requests_tenant_isolation on consult_requests
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
