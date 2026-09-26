# CRM: импорт контактов — STATE (26.09.2026)

Срез «Добавление людей» из `TASK.md` (шаг 3 плана: телефонная книга Android, .vcf, CSV).
Архитектура — `TZ_IMPLEMENTER_V1.md`. Лид-STATE всего CRM — `STATE.md` (не трогал).
Исполнитель: Claude (Core — core-agent, сайт — site-agent). Отчёт — `REPORT_IMPORT_V1.md`.

## Репозитории и ветки

| repo | worktree | ветка | база | мой коммит |
|---|---|---|---|---|
| Core (WHIEDA) | `D:\Projects\_worktrees\whieda-core-crm-import` | `core/crm-import-v1` | origin/master `c78d13a` | `9100641` код, далее docs-коммит с этим файлом |
| Сайт (wwc-best) | `D:\Projects\_worktrees\wwc-site-crm-import` | `site/crm-import-v1` | origin/master `2482120` | `5e516d4` |

Исходное состояние обоих worktree — чистое от origin/master.

## Разрешённые файлы (всё, что менял)

- Core: `backend/platform-api/app/crm/routes.py`, `app/crm/service.py`,
  `tests/test_crm_routes.py`, `tests/test_crm_bulk_postgres.py` (новый),
  `PROCESS/wwc-crm-20260925/STATE_IMPORT_V1.md`, `REPORT_IMPORT_V1.md`.
- Сайт: `src/lib/crm/app.js`, `src/lib/crm/import.js` (новый), `src/styles/crm.css`,
  `tests/unit/crm-import-{vcf,csv,file}.test.mjs` (новые), `tests/unit/crm-today.test.mjs`
  (одна строка: маршрут `#import`).

Незакоммиченное в worktree сайта — побочные файлы `npm run build`
(`public/downloads/*.pdf|csv`, `public/health.json`, `public/wwc-api/referral-registry.js`,
`public/wwc-runtime-config.json`, `src/data/catalog-official-photos.js`). Не мои, не коммитил.
В worktree Core — только `app/reports/__pycache__/` (было до меня).

## Проверки

- Core: `pytest tests/test_crm_routes.py` 23/23; `run_postgres_integration_tests.ps1 -PytestArgs @('-k','crm')`
  4/4 (в т. ч. новый `test_crm_bulk_postgres.py`); `pytest tests -m "not integration"` —
  1700 passed, 21 failed + 4 errors, все в tooling-тестах (golden corpus, product discovery map
  и т. п.), ни один не упоминает crm — те же известные падения.
- Сайт: `npm run test:unit` 329/329 (после `npm run build`; до build падают чужие
  `motion-reveal-fallback` — нужен `dist/` — и `referral-registry asset stays in sync`, который
  падает и на нетронутом master `2482120`: сгенерированный `public/wwc-api/referral-registry.js`
  отстал от `src/data/referrals.js`, build его пересобирает). `npm run build` OK.
- Браузер (статический сервер на `dist/`, Core подменён заглушкой `fetch`): файл .vcf →
  предпросмотр → отправка → отчёт; Contact Picker (заглушка `navigator.contacts`); cp1251-CSV;
  1200 строк → три запроса 500/500/200.

Не проверено: staging/prod, реальный Android Chrome и iPhone, реальный Core за nginx.

## Среда и следующий шаг

Разрешённая среда — только локальная. На staging/бой ничего не выкладывал, миграций нет
(схема не менялась). Следующий шаг — лид: ревью, merge в master, `release_core.ps1`,
`deploy:staging` → проверка `/crm/#import` с телефона → `deploy:prod`.
