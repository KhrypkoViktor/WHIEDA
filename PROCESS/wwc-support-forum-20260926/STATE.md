# Поддержка WWC как чат с топиками (форум «site») — STATE (26.09.2026)

Решение владельца 26.09.2026: кнопка/команда «Поддержка» открывает тикет вида «site»
(не Gemini), тикеты «site» живут во втором форуме, где владелец видит имя партнёра и
адрес его сайта. Форум Карины (Gemini, «services») не меняется. Исполнитель: Core-agent.

## Ветка

| repo | worktree | ветка | база |
|---|---|---|---|
| Core (WHIEDA) | `D:\Projects\_worktrees\whieda-core-support-forum` | `core/support-forum-v2` | origin/master c78d13a |

Не выкладывалось ни на staging, ни на бой. Выпуск — лид (`release_core.ps1 -Target staging`
→ проверка владельца → тем же SHA `-Target core`). **Миграция v16 применяется к общей базе
раньше кода**: код читает `support_forums.kind` и делает `on conflict (tenant_id, binding_id, kind)`.

## Что сделано

Миграция `postgres/sql/platform_support_site_forum_v16.sql` (идемпотентная, без `$`, `lock_timeout 5s`):
- `support_forums.kind text not null default 'services'` + check (`services` | `site`);
- первичный ключ `(tenant_id, binding_id)` → `(tenant_id, binding_id, kind)` (drop if exists + add,
  FK на таблицу нет). Существующая строка форума Карины автоматически становится `services`.
- Зарегистрирована: `tests/postgres_testkit.py` (добавлены v8, v9, v16 — v16 зависит от v9),
  `apply_staging_platform_all.ps1`, `staging_proof_lib.APPLY_ORDER`, `EXPECTED_APPLY_COUNT` 39→40,
  `test_shared_staging_release_harness.py` (2 места), `test_staging_sql_order.py`.

`app/support/service.py`:
- `register_forum(..., kind)`, `get_forum(..., kind='services')`, `set_forum_service_threads` — только
  строка `services`; `list_open_tickets_for_admin(..., forum_kind)`; `forum_kind_for_channel`
  (`channel_code='site'` → форум `site`, всё остальное → `services`);
- `partner_site_for_telegram_user`: сайт партнёра из `referral_profiles` по `owner_id`
  (через `lead_actors.telegram_user_id`), URL по `public_profile.subdomain` / карте выданных
  поддоменов / `ref_code`.

`app/telegram/support.py`:
- «Поддержка» (меню `/support`, слово «поддержка», кнопка кабинета `svc:support:site`) →
  `open_site_support` → тикет `channel_code='site'`, адресат — `PLATFORM_BILLING_OWNER_TELEGRAM_ID`;
  партнёру — «Обращение #S-N открыто.» + текст владельца дословно (`SUPPORT_INVITE_TEXT`).
- `/forum site` в новой группе регистрирует форум `site` (только владелец); `/forum` — как раньше
  (`services`, темы «Бонусы»/«Отчёты»). Открытые тикеты своего вида переносятся в темы.
- Форум `site`: тема `#S-12 · Имя Фамилия (@username) · ref`; первое сообщение — `#S-12 · Имя (@username)`,
  строка «Партнёр: …» (без username — ссылка `tg://user?id=…`), «Сайт: https://….wwc.best/».
  Каждая строка релея партнёра подписана `#S-N · Имя`. Ответ партнёру: «Ответ команды WWC по
  обращению #S-N: …». Закрытие: «нажмите «Поддержка» в меню», тема переименовывается с ✅.
- Без форума `site` — fallback в личку владельца с заголовком с именем; владелец отвечает
  **только Reply** на заголовок (его обычный текст — команды «оплата», «безлимит» и вопросы советнику,
  без Reply не трогаем). Кнопку «Закрыть» тикета может нажать адресат тикета (владелец) или
  администратор сервисов.
- Форум `services` (Карина): тексты, темы, анонимность — без изменений.

`app/telegram/delivery.py`: `format_telegram_html` пропускает сбалансированную ссылку
`<a href="tg://user?id=N">…</a>` и `https://…`; остальные `<a …>` экранируются как раньше.

`app/telegram/referral_bonus.py`: кнопка «Поддержка» в кабинете (оба профиля) — callback
`svc:support:site` вместо ссылки на ЛС владельца; `show_support` удалён, `/support` в
референс-роутере — safety net на `open_site_support`.

## Проверено

- Unit: `tests/test_telegram_support_site.py` (16 тестов: маршрутизация команды/слова/кнопки,
  адресат, тексты, тема с именем, ссылка по id, `/forum site`, релей в обе стороны, fallback Reply,
  закрытие, кнопка кабинета, HTML-ссылки), `tests/test_telegram_support_tunnel.py`,
  `tests/test_referral_bonus_service.py`, `tests/test_telegram_service_sales.py` — зелёные.
- Postgres-интеграция (`scripts/run_postgres_integration_tests.ps1`, временный локальный кластер):
  17 тестов зелёные, включая новый `tests/test_support_site_forum_postgres.py`:
  два форума одного бота по kind, строка до v16 = `services`, check на kind, параллельные тикеты
  `site` + `gemini` у одного партнёра, выборка по kind, поиск сайта партнёра; сквозной путь через
  `process_core_telegram_update` с моками Telegram: `/forum` + `/forum site`, «поддержка» → тема
  `#S-1 · Ольга (@olga) · olga`, релей партнёр→тема→партнёр, Gemini-тикет в форуме Карины без имени.
- Полный unit-прогон: набор падающих тестов тот же, что на origin/master (product_discovery_map,
  golden corpus, acceptance lab и т.п. — не связаны с этой веткой); ничего нового не сломано.
- Локальные тесты реальных сообщений не шлют (отправка замокана).

## Не проверено

- Живой Telegram: создание темы в реальной форум-группе, кликабельность `tg://user?id=` без
  username (Telegram показывает ссылку, если партнёр разрешил в приватности), 128-символьный лимит
  имени темы при длинных ФИО (обрезается в `create_forum_topic`).
- Staging/бой не трогались.

## Что нужно от владельца

1. Создать форум-группу (включить «Темы»), добавить бота администратором с правом
   «Управление темами» (Manage topics). Отдельно для staging-бота и для боевого бота, если нужны обе.
2. Внутри группы отправить `/forum site` (от владельца — `PLATFORM_BILLING_OWNER_TELEGRAM_ID`).
   Бот ответит «Группа поддержки сайтов подключена…»; открытые тикеты «site» переедут в темы.
3. До регистрации группы тикеты «site» приходят владельцу в личку; отвечать — Reply на заголовок.

## Вопросы лиду

- Тенант NSP: если его бот идёт через `core`-процессор, `/support` у NSP теперь откроет тикет
  «site» в тенанте NSP с адресатом — владельцем WWC (раньше показывал ссылку на ЛС Виктора).
  Форум по `(tenant_id, binding_id)` у NSP не зарегистрирован → fallback в личку. Нужен ли guard по тенанту?
- У партнёра могут быть одновременно открыты тикет Gemini и тикет «site». Свободный текст партнёра
  уходит в тикет с самой свежей активностью (`get_open_ticket_for_user` по `last_message_at`) —
  как и раньше для нескольких каналов; ограничение осталось.
