# WWC: текущее состояние production

Обновлено: 2026-09-13. Это краткая отправная точка для агента. Перед выводом
о живом состоянии всё равно сверять ревизию и health с сервером.

## Развёрнуто

- Core production и staging: `e662571`.
- Production Core: `PLATFORM_TELEGRAM_UI_PROFILE=minimal`.
- Staging Core: `PLATFORM_TELEGRAM_UI_PROFILE=full`.
- Production бот: `@WHIEDA_Advisor_bot`, binding `whieda-advisor-bot`.
- Staging бот: `@wwc_admin_staging_bot`, binding `wwc-cabinet-staging-bot`.

Production-бот публикует только команды `cabinet`, `invite`, `calculator`,
`support`. Его `/start` убирает старую большую reply-клавиатуру и открывает
кабинет. Пользователь должен один раз отправить `/start`, чтобы его Telegram
клиент убрал старое меню.

## Данные партнёров

Операционный реестр: вкладка `Partner_Subscriptions` в Google Sheet
`1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4`.

- Тестовый доступ оформлен до 21.09.2026 включительно.
- `natali` и `olesya-vselennaya` имеют реальные строки тестовой подписки в
  `partner_subscriptions` до `2026-09-21T21:00:00Z`.
- `dev` — технический тестовый поддомен с такой же тестовой подпиской.
- `nnm` — рабочий тестовый поддомен без подписки; не создавать ему доступ без
  отдельного решения владельца.
- Поля `telegram_chat_id` и `Бот /start` в таблице отражают связку с ботом.
  Значение `проверить` означает: chat ID известен, но Telegram user ID ещё не
  закреплён; человеку надо перейти по своей ссылке и нажать `/start`.

Ни оплата, ни бонус не должны считаться записанными только из сообщения,
скриншота или значения в таблице. Для этого нужна отдельная подтверждённая
операция владельца и запись в ledger.

## Не нарушать

- Staging и production используют общую БД.
- Не создавать фальшивые оплаты, людей, реферальные связи или бонусы на
  staging.
- Не выдавать `nnm` подписку.
- Не менять Telegram UI profile, bot binding, webhook, nginx или DNS без
  отдельного пункта задачи, бэкапа и последующей проверки.

## Проверка и выпуск

Команды и порядок — в `AGENTS.md` и
`backend/deploy/core/TELEGRAM_COMMAND_MENU_RUNBOOK.md`. Production выпускать
только после staging и только тем же commit. Использовать
`backend/deploy/core/release_core.ps1`, не ручную подмену файлов на сервере.
