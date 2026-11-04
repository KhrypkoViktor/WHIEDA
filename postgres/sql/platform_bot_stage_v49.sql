-- WWC: статус человека в боте V49 (04.11.2026).
-- ТЗ: PROCESS/wwc-ux-complex-20261104/TZ_core_bot_three_types.md, решение 1. Код — app/bot_stages/.
--
--   lead_actors.bot_stage — какой из трёх экранов видит человек в боте WWC:
--     guest           гость: пришёл с рекламы, по голому /start или по ссылке заявки (меню «С чего начать»);
--     partner_whieda  партнёр WHIEDA: ведущий подтвердил его в WWC CRM (или владелец сказал «партнёр @ник»);
--     partner_wwc     партнёр WWC: у него включённый referral_profiles (оплаченный сайт) — меню кабинета.
--   Статус партнёра WWC бот определяет и по самому профилю сайта, не дожидаясь этого поля: поле лишь отражает его
--   (бот сам подтягивает его при первом касании). Статус «партнёр WHIEDA» живёт только здесь.
--
-- Заполнение: у кого есть включённый referral_profiles — partner_wwc; остальные guest (значение по умолчанию).
-- Только add column if not exists и пересоздание своего ограничения: повторный прогон безопасен и ничего не откатывает
-- (заполнение трогает только строки со статусом guest).
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py) и через n8n, а он режет этот знак.

begin;

-- ALTER lead_actors не должен висеть за долгой транзакцией: упасть и повторить лучше, чем держать таблицу.
set local lock_timeout = '5s';

alter table lead_actors add column if not exists bot_stage text not null default 'guest';

alter table lead_actors drop constraint if exists lead_actors_bot_stage_check;
alter table lead_actors add constraint lead_actors_bot_stage_check
  check (bot_stage in ('guest', 'partner_whieda', 'partner_wwc'));

update lead_actors la
set bot_stage = 'partner_wwc'
where la.bot_stage = 'guest'
  and exists (
    select 1 from referral_profiles rp
    where rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled
  );

commit;
