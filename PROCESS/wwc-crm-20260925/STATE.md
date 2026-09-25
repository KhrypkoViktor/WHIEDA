# CRM v1 «Ежедневник партнёра» — STATE (25.09.2026)

ТЗ: `PROCESS/wwc-crm-20260925/TZ_IMPLEMENTER_V1.md` и `TASK.md` (в основном checkout,
коммит 192fdb5 ветки wip/consolidation-20260831). Исполнитель: site-agent (Claude).

## Репозитории и ветки

| repo | worktree | ветка | база | мой коммит |
|---|---|---|---|---|
| Core (WHIEDA) | `D:\Projects\_worktrees\whieda-core-crm-v1` | `core/crm-v1` (pushed) | origin/master 99562dc | см. `git log origin/master..core/crm-v1` |
| Сайт (wwc-best) | `D:\Projects\_worktrees\wwc-site-crm-v1` | `site/crm-v1` (pushed) | origin/master 939007c | см. `git log origin/master..site/crm-v1` |

Разрешённая среда: сайт — только staging. Core — не выпускался (выпуск и миграция на бой — лид).

## Что сделано

Core:
- `postgres/sql/platform_crm_v14.sql`: platform_accounts, crm_contacts, crm_notes (tenant_id + RLS),
  platform_outbox (create if not exists) + `due_at` + индекс + статус `scheduled` в CHECK
  (`platform_outbox_status_check` пересоздаётся), entitlement `whieda/crm`.
  Зарегистрирована в testkit, apply-скрипте staging, APPLY_ORDER, EXPECTED_APPLY_COUNT и тестах (36 → 37).
- `app/crm`: rules (чистые правила), service, routes (`/api/v1/content-access/crm/*`, private no-store,
  в том числе на ошибках), digest, bot. Фича `crm` отключаемая (`PLATFORM_DISABLED_FEATURES=crm`).
- `app/leads/service.save_lead`: заявка → карточка «Новый контакт» у владельца, если у него есть
  platform_accounts; под savepoint, сбой не роняет заявку. Если номер уже в ежедневнике — заметка
  к существующей карточке, и её шаг поднимается не позже сегодняшнего дня.
- `app/jobs/worker.py`: `process_due_notifications` (статус `scheduled` → `processing` → `done`;
  повтор через 10 минут до 3 попыток; неоднозначная доставка, 400/403 и последняя попытка → `dead`;
  незатронутые строки прерванной пачки возвращаются в `scheduled`) + план утра CRM раз в 5 минут.
  `process_pending_outbox` не менялся: плановые строки в своём статусе, старые сборки воркера на
  общей базе их не видят.
- `app/jobs/outbox.py`: `due_at` и `scheduled` в ensure_outbox_table; enqueue с due_at пишет
  `scheduled`; DDL только если таблицы нет.
- Бот: «ежедневник», «crm», «срм», «мои контакты» → кнопка на `/crm/` со входом; кнопка
  «📒 Ежедневник» в кабинете рядом с «Академией» (только тем, кому открыт).
- `.github/zones.json`: `app/crm/` и `postgres/sql/platform_crm_` в зоне core.

Сайт:
- `/crm/` (`src/pages/crm/index.astro`, `src/lib/crm/*.js`, `src/styles/crm.css`), путь не зеркалится,
  без ref, Disallow в robots, нет в sitemap.

## Настройки для выпуска (новые env Core)

- `PLATFORM_CRM_PILOT_TELEGRAM_IDS` — пилот (Игорь, Макарова, Олеся, Доронина). Пусто = все с PRO.
- `PLATFORM_SCHEDULED_NOTIFY_BINDINGS=whieda-advisor-bot` — **только на боевом воркере**. Staging и бой
  работают на одной базе и оба запускают `app.jobs.worker`; без этой переменной процесс утро не
  планирует и не отправляет. На staging не ставить (иначе staging-бот заберёт боевые строки).

## Проверки

- Core unit: весь `tests -m "not integration"` — набор падений совпадает с origin/master
  (21 failed + 4 errors, tooling/golden), новых падений нет; новые test_crm_* зелёные.
- Core Postgres integration (`run_postgres_integration_tests.ps1`): 13/13 зелёные, в т. ч. два теста
  test_crm_postgres (полный цикл + обновление outbox, созданного кодом, на бою).
- `check_zone.py --base origin/master`: OK.
- Сайт: `npm run test:unit` 299/299, `npm run build` OK; staging выкладывался 12a2017 (проверены
  страница, noindex, нет EN/DE, 390 px без горизонтальной прокрутки). Дальше staging выкладывает
  лид сайта (попросил не выкладывать без него).
- Независимое ревью обеих веток: утечек между аккаунтами, XSS и ошибок SQL не найдено; найденные
  риски (outbox на общей базе, зависшие строки, дубль заявки, устаревшее «сегодня», старая встреча,
  порядок ответов поиска) исправлены и покрыты тестами.
- Локальный браузер (временный Postgres + локальный Core + astro dev): вход по ссылке из бота,
  добавление за 2 нажатия, правило статуса, встреча, заметка с черновиком, перенос, дубль 409,
  поиск/фильтр, CSV, удаление, 402 без PRO, 390 и 1440 px.

## Что не проверено / известные ограничения

- Боевые заявки с сайта идут в n8n (`wwc-website-leads-p0`), а не в Core `save_lead` → хук
  «заявка → карточка» на бою не сработает, пока n8n не повторит вставку (зона site, отдельный срез).
- Утро на реальном Telegram не отправлялось (моки). Проверить после выпуска на пилоте.
- Staging Core без CRM → на staging.wwc.best/crm/ после входа «Ежедневник ещё не включён на сервере».

## Следующий шаг (лид)

1. Ревью веток → merge в master.
2. Миграция: `python n8n/current/wwc_sql.py --file postgres/sql/platform_crm_v14.sql`.
3. `release_core.ps1 -Target staging`, затем `-Target core`; env из раздела выше.
4. Сайт: staging уже с /crm/; после выпуска Core — проверка на staging и `deploy:prod`.
