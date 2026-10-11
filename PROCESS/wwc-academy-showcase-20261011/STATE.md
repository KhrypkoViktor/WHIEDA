# STATE — витрины Академии (11.10.2026)

## Где

- Core: worktree `D:\Projects\_worktrees\whieda-core-academy-showcase`, ветка
  `core/academy-showcase-20261011` от `35c0bd6` (развёрнут на core и staging 11.10).
  Ветку `core/bot-ux-20261008` параллельно ведёт другой агент (уже `ccfadbe`):
  перед выпуском — rebase на её голову и выпуск одним SHA.
- Сайт: worktree `D:\Projects\_worktrees\wwc-site-academy-showcase`, ветка
  `site/academy-showcase-20261011` от `3a74816` (развёрнут на wwc.best 10.10).
- Подписи: `Core-agent`, `site-agent`.

## Сделано

Core:
- V29 `postgres/sql/platform_academy_showcases_v29.sql`: `academy_showcases`, RLS, витрина `wwc`.
- `app/academy/showcases.py`: витрина по хосту/боту/автору, кэш 60 с, хосты для входа.
- `/academy/showcase` (без входа), `/courses` по витрине (`section own|more`, `more_mode`).
- Вход: бот витрины в deep link, хосты витрин в `return_to` / `with_site_login` / `/tg-open/`.
- Бот витрины: `app/telegram/showcase_bot.py`, binding несёт `showcase_id` (тот же запрос).
- Уведомления и ссылки — на витрину автора курса, её ботом; воркер шлёт строки ботов витрин.
- Партнёр-владелец витрины = автор без «Академии автора» (`shelf.partner`).

Сайт: `src/lib/academy/brand.js`, нейтральный title, галочка согласия, свои/«Ещё курсы»,
кнопки цвета автора; демо `?demo=1&showcase=almira`. Коммит `4babfe1`.

## Проверки

- Core: фокусные тесты Академии/входа/бота 196+8 зелёные; PG-интеграция (28 файлов) зелёная.
  Полный прогон: падают те же тесты, что на базе (golden/parity/discovery/markets, Docker),
  плюс ничего нового.
- Сайт: `node --test tests/unit/*.test.mjs` — 614/615; падает
  `referral-api-bootstrap` (сгенерированный реестр партнёров отстал на базе `3a74816`, не наше).
  Сборка 0 ошибок. Браузер: демо витрины 1280 и 375, без горизонтальной прокрутки,
  галочка не пускает вход без согласия.

## Дальше

1. Данные: actor автора ALMIRA, binding `almira-academy-bot`, строка витрины, `tenant_domains`.
2. Токен @ALMIRA_I_bot и секрет вебхука — в env Core (бой), `setWebhook`.
3. Миграция V29 на общую базу → выпуск Core staging → core.
4. nginx origin + московский прокси: `X-WWC-Showcase-Host`, блок academy.almira.by, буферизация видео.
5. DNS `academy.almira.by` (hoster.by) — ждём доступ; пилот на almira.wwc.best.
6. almira.by: кнопка «Академия», тексты, политика.
