-- WWC CRM: адрес Telegram в карточке V50 (04.11.2026).
-- ТЗ: PROCESS/wwc-ux-complex-20261104/TZ_crm_telegram.md, раздел «Ядро». Код — app/crm/ (rules.parse_telegram,
-- service, telegram_import).
--
--   crm_contacts.telegram_username  имя в Telegram без «@», регистр как прислан (проверка по тому же шаблону,
--                                   что в коде: латиница, цифры, «_», 4–32 знака);
--   crm_contacts.telegram_user_id   числовой ID человека: его ставит только бот (выбор людей в Telegram или
--                                   пересланный контакт), через API сайта задать нельзя.
--   Оба поля необязательные: у карточки может быть только номер, только Telegram или оба.
--
-- Один человек — одна живая карточка аккаунта: частичные уникальные индексы на (аккаунт, имя без учёта регистра)
-- и (аккаунт, ID). Мягко удалённая карточка (deleted_at) в них не участвует: адрес освобождается сразу, а «Вернуть»
-- проверяет его так же, как номер. Нарушение индекса при гонке код превращает в 409 duplicate, а не в 500.
--
-- Только add column if not exists и пересоздание своей проверки: повторный прогон безопасен. Существующие
-- карточки не меняются (оба поля пустые).
-- Знака доллара в файле нет: боевые правки идут из контейнера Core (/tmp/apply_sql.py) и через n8n, а он режет этот знак.

begin;

-- ALTER crm_contacts не должен висеть за долгой транзакцией: упасть и повторить лучше, чем держать таблицу.
set local lock_timeout = '5s';

alter table crm_contacts add column if not exists telegram_username text;
alter table crm_contacts add column if not exists telegram_user_id bigint;

alter table crm_contacts drop constraint if exists crm_contacts_telegram_username_check;
alter table crm_contacts add constraint crm_contacts_telegram_username_check
  check (telegram_username is null or telegram_username ~ '^[A-Za-z][A-Za-z0-9_]{3,31}\Z');

create unique index if not exists crm_contacts_telegram_username_live
  on crm_contacts (tenant_id, account_id, lower(telegram_username))
  where deleted_at is null and telegram_username is not null;

create unique index if not exists crm_contacts_telegram_user_id_live
  on crm_contacts (tenant_id, account_id, telegram_user_id)
  where deleted_at is null and telegram_user_id is not null;

commit;
