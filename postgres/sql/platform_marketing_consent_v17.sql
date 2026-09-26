-- WWC: согласие на рекламную рассылку V17 (26.09.2026).
--
-- ФЗ «О рекламе» ст. 18 ч. 1: реклама по сетям электросвязи — только с
-- предварительного согласия абонента; по его требованию рассылка прекращается.
-- Решение владельца 26.09.2026: отдельная необязательная галочка «Хочу получать
-- новости и предложения» на формах сайта и кнопка в боте; рассылки бота идут
-- только тем, у кого флаг стоит. Личный ответ консультанта на заявку — не рассылка.
--
-- Что здесь:
--   website_leads.marketing_consent / marketing_consent_at — галочка с формы заявки
--     (пишут n8n wwc-website-leads-p0 и Core POST /api/v1/leads);
--   telegram_marketing_consents — ответ в боте: одна строка на человека в тенанте,
--     opted_in true/false (явный отказ тоже пишется), когда и откуда.
--     Рассылка (n8n «WHIEDA Broadcast Delivery Worker») берёт только opted_in = true.
--
-- website_leads — через `alter table if exists`: в testkit этой таблицы нет,
-- на staging/бою она есть. Идемпотентно. В файле нет знака доллара: боевые
-- правки идут через n8n, а он режет этот знак.

begin;

-- 1. Галочка с формы заявки. По умолчанию false: без согласия — без рассылки.
alter table if exists website_leads
  add column if not exists marketing_consent boolean not null default false;
alter table if exists website_leads
  add column if not exists marketing_consent_at timestamptz;

-- 2. Ответ в боте. Строка появляется при первом ответе (да или нет) и дальше
--    только обновляется: opted_in — текущее состояние, даты — доказательство.
create table if not exists telegram_marketing_consents (
  tenant_id text not null,
  telegram_user_id bigint not null,
  telegram_chat_id bigint not null,
  opted_in boolean not null default false,
  consent_version text not null,
  source text not null default 'bot',
  opted_in_at timestamptz,
  opted_out_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, telegram_user_id)
);

create index if not exists telegram_marketing_consents_opted_in
  on telegram_marketing_consents (tenant_id, telegram_chat_id)
  where opted_in;

alter table telegram_marketing_consents enable row level security;

drop policy if exists telegram_marketing_consents_tenant on telegram_marketing_consents;
create policy telegram_marketing_consents_tenant on telegram_marketing_consents
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
