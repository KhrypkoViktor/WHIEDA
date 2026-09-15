# Task: Telegram Navigation and Catalog Browse V1

Исполнитель: Дровосек  
Дата: 2026-08-12  
Основание: `WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md`  
Режим: local-only. Не deploy, не менять production, Google Sheets, n8n, Dify, карточки и `03_Website`.

## Цель

Сделать первый понятный Telegram-интерфейс для человека, который не знает названий товаров. Это не новая база и не второй советник: навигация использует текущий Platform Core, текущий runtime-репозиторий продуктов и текущую доставку Telegram.

После блока пользователь может нажать меню `Товары`, увидеть постраничный список реальных товаров, выбрать товар и получить существующую карточку. Свободный текст по-прежнему работает.

## Неподвижные правила

- Один Telegram update даёт одну логическую реакцию. Фото + отдельный текст карточки остаются одной реакцией.
- Никакой второй таблицы каталога, CSV-кэша или захардкоженного списка SKU в runtime.
- Список строится только из `advisor_structured_products` Postgres через repository. `category` в текущем master пустой: не выдумывать категории.
- Не переписывать owner-locked карточки, цены, тексты, alias, safety или promotion.
- Не трогать legacy n8n workflow и route flags.
- Не делать publish/deploy. Не использовать реальные Telegram token, DSN, Sheets OAuth.
- Callback data максимум 64 bytes, только allowlisted action и page/SKU. Любой битый callback получает безопасный menu response и не вызывает SQL с произвольным значением.

## Block A: Telegram delivery primitives

1. В `app/telegram/delivery.py` добавить поддержку `reply_markup`:
   - `send_telegram_text(..., reply_markup: dict | None = None)`;
   - payload содержит `reply_markup` только если он валиден и передан;
   - `send_telegram_photo` не получает caption и не меняет photo-first правило;
   - добавить отдельную `answer_callback_query(callback_query_id, ...)` без текста пользователя в логах.

2. Создать `app/telegram/navigation.py`:
   - единственный источник labels, callback payload и клавиатур;
   - persistent reply keyboard:
     `📦 Товары`, `🧮 Калькулятор`, `📈 Бизнес`, `🏢 О компании`, `🧭 Подбор`, `📅 Встречи`;
   - inline product list: максимум 8 товаров на страницу, затем `Назад` / `Далее`, плюс `В меню`;
   - inline product actions: `Карточка`, `Цена/PV`, `Фото`, `Видео`, `Сертификат`, `Сравнить`.

3. Не возвращать markdown со звёздочками. Кнопки и текст не должны зависеть от LLM.

## Block B: Runtime catalog browse

1. В `app/advisor/sql/repository.py` добавить read-only функции:
   - `list_catalog_products(conn, tenant_id, page, page_size=8)`;
   - `count_catalog_products(conn, tenant_id)`;
   - `resolve_catalog_product_by_sku(conn, tenant_id, sku)`.

2. SQL:
   - только активные товары tenant;
   - сортировка `canonical_name`, затем `sku`;
   - строгая пагинация, page size clamp `1..8`;
   - не скрывать товары с отсутствующей ценой/фото;
   - не возвращать лишние поля и не строить SQL из callback data.

3. Реализовать один handler `app/telegram/catalog_browse.py`:
   - `nav:products` → первая страница реального каталога;
   - `cat:p:<page>` → указанная страница;
   - `cat:s:<sku>` → существующий product rail / карточка;
   - действия товара переводятся в уже существующие structured intents, а не дублируют formatter.

4. Реальные text labels меню должны поступать в те же handlers. Не добавлять второй regex-набор в engine: navigation module публикует допустимые labels/intent keys, engine импортирует их.

## Block C: Telegram callback ingress

1. Расширить update parser для `callback_query`:
   - chat/user/message/callback id/data;
   - callback обрабатывается только для активного bot binding;
   - callback не проходит через group free-text путь;
   - callback ACK выполняется один раз;
   - update_id dedup и per-chat sequencer обязательны и для callback.

2. В `routes.py` / `processor.py` встроить callback после binding resolution и до advisor free-text.

3. При устаревшей странице, несуществующем SKU или неверном callback:
   - не ошибка;
   - без leak внутренних id;
   - ответ: коротко вернуть в `📦 Товары` или главное меню.

## Block D: UX and contract tests

Добавить tests без сети и реальные fixture products через текущий local seed:

1. Menu keyboard имеет ровно 6 верхних действий и стабильные labels.
2. Browse первой страницы: реальные товары, максимум 8, корректные Next/Back.
3. Последняя страница не имеет Next; первая не имеет Back.
4. Callback SKU существует → вызывает текущую карточку, не альтернативный formatter.
5. Invalid/oversized callback безопасно возвращает меню, без SQL injection.
6. Callback one update → один ACK + одна logical response.
7. Free text `📦 Товары`, `какие есть товары`, `хай`, `что можешь` ведут предсказуемо.
8. Photo-first не нарушен для card action.
9. Existing tests Telegram, parity, no-blind-zone не ослаблять.

Создать `qa/telegram_navigation/`:

- offline corpus минимум 20 flows;
- отдельный runner `--offline`;
- hook `run_local_core_lab.py --e2e --telegram-navigation`;
- E2E с локальным Docker допускается только как дополнительное доказательство, не подменяет unit/contract tests.

## Deliverables

Три логических коммита:

1. `feat: add Telegram navigation delivery primitives`
2. `feat: add runtime catalog browse and callback routing`
3. `test: add Telegram navigation acceptance lab`

Отчёт: `TELEGRAM_NAVIGATION_CATALOG_LOCAL_REPORT.md` с честными статусами:

- реализовано локально;
- local tests;
- Docker E2E (PASS / NOT RUN);
- явно не сделано: prod deploy, Sheets, n8n, full visual Telegram acceptance.

## Acceptance command

```powershell
cd D:\Projects\WHIEDA\backend\platform-api
python -m pytest tests -q

cd D:\Projects\WHIEDA
python qa\telegram_navigation\run_telegram_navigation.py --offline
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-navigation
```

Не заявлять production readiness, пока Core route не выкатил и не проверил сам владелец.
