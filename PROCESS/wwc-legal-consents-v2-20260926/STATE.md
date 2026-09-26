# Юр-пакет 2 (согласия и реклама) — STATE исполнителя (26.09.2026)

Решение владельца 26.09.2026: «решай, доделывай всё». Три среза:
1. согласие на рекламную рассылку (ФЗ «О рекламе» ст. 18) — отдельная необязательная
   галочка на формах сайта и кнопка в боте; флаг в базе; рассылка бота только тем,
   у кого флаг стоит; личный ответ консультанта на заявку — не рассылка;
2. /reviews/ — «случаи излечения» только после входа через Telegram
   (ст. 24 38-ФЗ, ст. 15 закона РБ о рекламе); поле `medical: true/false` в данных;
3. кабинет партнёра /cabinet/ — согласия по заявкам (версия, дата, галочка рассылки).
Не вводить «согласие на переписку» и «согласие на покупку».

## Где работа

| Repo | Worktree | Ветка | База |
|---|---|---|---|
| Core (корень WHIEDA) | `D:\Projects\_worktrees\whieda-core-lead-20260918` | `core/legal-consents-v2` | origin/master c78d13a |
| Сайт wwc-best | `D:\Projects\_worktrees\wwc-site-master` | `site/legal-consents-v2` | origin/master 2482120 |

Ничего не выкладывать и не применять на бой — выпуск делает лид. n8n не публикую:
патч-скрипты + инструкция в этой папке.

## Дизайн (принятые решения исполнителя)

### Core
- Миграция `postgres/sql/platform_marketing_consent_v17.sql` (v16 зарезервирован
  за core/support-forum-v2): `website_leads.marketing_consent boolean not null default false`,
  `website_leads.marketing_consent_at timestamptz` (через `alter table if exists`, в
  testkit таблицы нет); новая таблица `telegram_marketing_consents`
  (PK tenant_id + telegram_user_id; opted_in, consent_version, source, opted_in_at,
  opted_out_at; RLS как у `telegram_consents`). Без `$`, идемпотентно.
- Регистрация: postgres_testkit.MIGRATIONS, apply_staging_platform_all.ps1,
  staging_proof_lib.APPLY_ORDER, EXPECTED_APPLY_COUNT 39 → 40, два счётчика в
  test_shared_staging_release_harness.py, test_staging_sql_order.py; `schema_requirements`
  + `telegram_marketing_consents`.
- Бот (`app/telegram/consent.py`): на /start, если человек ещё ни разу не ответил,
  после уведомления 152-ФЗ — сообщение с кнопками «✅ Хочу получать новости и
  предложения» / «Не сейчас» (callback `consent:news:yes|no`). Команды `/news`
  (показать кнопки), `/news_on`, `/news_off`, слово «рассылка». Ответ «нет» тоже
  пишется (явный отказ). Сбой БД — только warning, ответ бота не блокируется.
- Заявки: `parse_lead_body` читает `marketing_consent` (true/"true"/"1"/"on"/"yes"),
  `save_lead` пишет `marketing_consent`, `marketing_consent_at = now()` при true.
- Кабинет-API: список — `marketing_consent`; карточка — `consent_at`,
  `marketing_consent`, `marketing_consent_at` (consent_version уже был).
- Рассылка живёт в n8n («WHIEDA Broadcast Delivery Worker», узел «Postgres: Queue
  Broadcast Deliveries»): добавить join `telegram_marketing_consents … opted_in`.
  Патч-скрипт + правка QUEUE_SQL в publish-скрипте; лид публикует сам.
- n8n заявки (`wwc-website-leads-p0`): узел «Code: validate and assign owner» отдаёт
  `marketing_consent` boolean, узел «Postgres: save lead and audit» пишет два столбца.
  Патч-скрипт `n8n/current/patch_wwc_website_leads_marketing_consent_2026-09-26.py`
  (--dry-run) + `N8N_PATCH_INSTRUCTIONS.md`.

### Сайт
- Формы (LeadForm home/order, PartnershipForm, ProductDetailTemplate, CartBar):
  вторая, необязательная галочка `name="marketing_consent"` «Хочу получать новости и
  предложения WWC». `form-submit.js` → `marketingConsent`, `api/leads.js` →
  `marketing_consent: true/false` в теле; `public/wwc-api/cart-order.js` то же;
  cache-bust версии lead-form.js / cart-order.js / leads.js подняты.
- /reviews/: `src/data/review-medical-terms.js` — словарь симптомов/болезней/лечения +
  ручные закрытия спорных; в `reviews.js` у каждого отзыва `medical: true|false`,
  `reviews` = `!medical`, `closedReviews` = `medical`; список `PUBLIC_REVIEW_IDS` убран.
  В закрытом ярусе на открытом уровне вместо заголовка — нейтральная подпись
  «Закрытая история №N» (заголовки с «прошла/прекратились» тоже были на открытом
  уровне); после входа — настоящий заголовок и текст из Core.
  Юнит-тест держит данные в согласии со словарём.
- Кабинет `/cabinet/leads/<id>/`: секция «Согласия» (ПДн: версия + дата; рассылка:
  да/нет + дата); в списке колонка «Рассылка». Хелперы в `operational-labels.js`.

## Проверки
- Core: `python -m pytest tests -q` (unit) и
  `pwsh backend/platform-api/scripts/run_postgres_integration_tests.ps1` (Postgres).
- Сайт: `npm run test:unit`, `npm run build`.

## Статус
- [ ] Core: миграция + регистрация
- [ ] Core: бот, заявки, кабинет-API, тесты
- [ ] Core: n8n патч-скрипты + инструкция
- [ ] Сайт: формы, отзывы, кабинет, тесты, сборка
- [ ] Отчёт лиду (REPORT.md)

## Статус на момент остановки (26.09.2026, лимит сессии)
- Код Core и сайта написан, НЕ закоммичен (оба worktree — незакоммиченный diff на
  ветках core/legal-consents-v2 @ c78d13a и site/legal-consents-v2 @ 2482120).
- Проверено: Core unit по затронутым тестам 160 passed; Postgres-интеграция все 16 тестов
  passed (в т.ч. новый test_marketing_consent_postgres.py); сайт `npm run test:unit`
  324 passed; `npm run build` OK (144 страниц), в dist/reviews/ 31 открытая карточка,
  126 закрытых с подписью «Закрытая история №N», утечек заголовков/текстов нет.
- Известное: tests/acceptance/test_acceptance_lab.py::test_check_target_contract_uses_advisor_host_for_probes
  падает и на чистом origin/master (проверено во временном worktree) — не связано.
- Сборка сайта перегенерировала public/downloads/*, public/health.json,
  public/wwc-runtime-config.json, public/wwc-api/referral-registry.js,
  src/data/catalog-official-photos.js — в коммит НЕ включать.
- Осталось: коммиты по явному списку файлов; REPORT.md лиду; полный прогон Core unit
  (запущен в фоне, лог tasks/bzz3ty4x5.output).
