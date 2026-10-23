# STATE — worker не падает от сбоя очереди + согласие по клику в боте (23.10.2026)

Источник: аудит 23.10 (`PROCESS/wwc-audit-2026-10-23/FINDINGS_DETAIL.txt`, разделы `bot`, `infra`, `legal`) и решение владельца.
Бой, серверы и базу не трогал; выпуск делает лид.

## Где

- Репозиторий: корень WHIEDA, worktree `D:\Projects\_worktrees\whieda-core-audit`.
- Ветка `core/audit-worker-consent-20261023`, база `origin/core/academy-threads-20261013` (b51d968, общая линия = бой).
- Код: `backend/platform-api/`. Миграций нет.

## Срезы

1. **Worker** (`app/jobs/worker.py`): `process_pending_outbox` обёрнут, `logger.exception("outbox_step_failed")`,
   пауза после проходов со сбоем 2 → 4 → … ≤ 60 с (`FailureBackoff`), удачный проход сбрасывает. Остальные шаги цикла уже были обёрнуты.
   Тесты: `tests/test_worker_loop.py` (7).
2. **Согласие по клику** (`app/telegram/consent.py`, `site_requests.py`, `content_access.py`):
   - «Заказать сайт» (`site:create`): без «Принимаю» (`site:accept`) заявка не создаётся; клик пишет согласие, затем анкета.
     Не переспрашиваем: открытую анкету, повторный заказ на тот же текст, тенанты без опубликованных документов.
   - Вход на сайт (`/start content_access_…`): последняя строка подтверждения — «Входя, вы принимаете оферту (…) и политику (…)»;
     подтверждённый вход пишет согласие; сбой записи вход не блокирует. Новых шагов нет.
   - Хранение: `telegram_consents`, источник и версия текста в `policy_version`: `site_order:2026-10-23`, `site_login:2026-10-23`.
     Старые строки «продолжая общение» (голая дата `2026-09-18`) не тронуты.
   Тесты: `tests/test_click_consent.py` (17), `tests/test_click_consent_postgres.py` (цепочка на настоящей базе),
   `tests/test_site_request_quick_postgres.py` (добавлен шаг «Принимаю»).

## Проверено

- Unit (весь `tests`): 24 failed / 2731 passed / 75 skipped / 4 errors; на чистой базе b51d968: 24 / 2707 / 74 / 4,
  набор упавших тестов идентичен (golden corpus, product discovery map, shared staging harness и др. — были красными до правок).
- Postgres (`scripts/run_postgres_integration_tests.ps1`, весь `*_postgres`): 74 passed; на чистой базе 73 passed.
- Мутация: при выключенном гейте тесты «без клика заявка не создаётся» падают.

## Не проверено / решения лиду

- Выкладка, staging, реальный Telegram — не делал.
- Вход: нажатие = подтверждённый переход по ссылке входа (`/start content_access_…`); текст оферты бот показывает в самом подтверждении,
  а не до него. Показать до — на экране входа сайта рядом с «Войти через Telegram» (срез сайта) или в описании бота (BotFather).
  Жёсткая кнопка перед входом потребовала бы шаг и хранилище токена (callback_data ≤ 64 байт) — не делал, как просили «без лишних шагов».
- `renew:start` (продление) в этот срез не входило — согласия там нет.
- Анкеты, открытые до выпуска, продолжаются без вопроса и без записи клика.
- Согласие по заказу спрашивается один раз на версию текста (`CLICK_CONSENT_VERSION`), а не на каждый заказ; новая дата в константе спросит всех заново.

## Следующий шаг

Лид: ревью, merge в общую линию, релиз Core по правилу «ancestor check». После релиза проверить в логе
`outbox_step_failed` / `site_order_consent_record_failed` / `site_login_consent_record_failed` (не должно быть) и строки
`telegram_consents` с префиксами `site_order:` / `site_login:`.
