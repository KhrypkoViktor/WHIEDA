# Мастерская WWC — Core (STATE)

Ветка `core/shop-v1` от `origin/master` 0523846, подпись Core-agent / core@whieda.local.
Не запушено (пушит лид). Не деплоилось. Файл лежит в worktree и в ветке: хук сессии
запрещает писать в основной checkout `D:\Projects\WHIEDA\PROCESS\…`.

## Сделано (03.10.2026)

ТЗ §3–4, 6, 7 с поправками `LEAD_NOTES.md`: подтверждает только владелец, заявка в его
форуме «site», доли партнёру нет, поле доли наружу не отдаётся, Gemini не тронут.

- **Миграция** `postgres/sql/platform_shop_v22.sql` (V22 свободен: последняя v21 cabinet).
  Таблицы `shop_items`, `shop_orders`, `shop_access` + RLS. Seed §3: 6 товаров `pilot`,
  `gemini` — `published` / `external` / `?start=gemini` / `services_admin`, цена текстом
  «6 мес — 3 990 ₽ · 18 мес — 4 490 ₽». Доля у всех 0. Без `$` в файле (n8n).
  Повторный запуск ничего не перезаписывает. Медиа и курс — без FK: проверяет код.
  Добавлено в `schema_requirements.py` (core), `postgres_testkit.py`, `zones.json`
  (core: `app/shop/`, `postgres/sql/platform_shop_`) — только добавлением строк.
- **Сервис** `app/shop/service.py`: каталог, цены (rub/byn null → из W$, 1 W$ = 100 ₽ = 3,5 BYN),
  видимость (published всем; pilot — preview-админам = владелец + супер-админы, как превью
  Академии), заказ (повторное «Купить» до оплаты — тот же заказ; до чека можно сменить
  страну), чек (свежие заказы `new` ≤ 3 дней → `receipt`), «Оплачено» (paid + доставка в
  одной транзакции, повторное нажатие идемпотентно), «Отклонить» (`cancelled`),
  покупки, подписанная ссылка на файл, правка каталога.
- **Академия**: `grant_course_to_telegram_user` в `app/academy/service.py` (новая функция,
  старые не тронуты) — доступ по Telegram id покупателя, `source='purchase'`,
  `payment_ref='shop:<order_id>'`. Сбой Академии — savepoint и предупреждение владельцу,
  оплата не теряется.
- **API** `app/shop/routes.py` (+ `main.py`): каждый маршрут и на `/api/v1/…`, и на `/v1/…`.
- **Бот** `app/telegram/shop.py` (+ `processor.py`, `support.py`, `support/service.py`):
  `/start shop_<code>[_<ref>]` → карточка → «Купить/Заказать — цена · Россия|Беларусь» →
  заявка канала `shop` в форуме владельца «site» (тема «Мастерская · <товар> · <имя>»;
  без форума — личка владельца) + заказ + реквизиты `PAYMENT_RU`/`PAYMENT_BY` → чек
  (фото/документ) → в тему: копия чека и «Оплачено <сумма>» / «Отклонить» → только
  владелец → курс открыт (кнопка «Открыть курс»), файл — «Открыть кабинет» на
  `/me/#purchases`, услуга — `delivery_note` и разговор в той же заявке.
  `shop_gemini` → обычная карточка «Сервисы» Gemini. Команда владельца «витрина» /
  «витрина <code> <status>». Канал `shop` → форум «site»: Карина заявок не видит.
- **Тексты**: `test_bot_copy_audit` — бот, сервис и seed V22: без «баллов», без голого W$,
  без обещаний (`_PROMISE`) и без «терапи…».

## Независимое ревью (агент, 03.10) — 2 находки, исправлены

1. Неоплаченный заказ забирал любое фото как чек (чек продления, скриншот в заявке Gemini,
   Reply владельца). Теперь фото — чек Мастерской, только если: его не ждёт продление или
   анкета сайта (`_request_waits_for_file`); последняя открытая заявка человека — не другая
   заявка, где он писал позже заказа; это не Reply владельца или администратора.
2. Заказ мог застрять в `receipt` без кнопок (сбой Telegram, удалённая тема). Теперь чек с
   кнопками идёт в тему, при сбое — в личку владельца; не дошёл никуда — заказ снова `new`,
   покупателю «пришлите ещё раз». Повторное «Купить» при чеке на проверке заново шлёт
   владельцу кнопки.

## Решения Core-сессии

- Продажи Мастерской — в `shop_orders`; `service_sales` остаётся только для Gemini,
  unique по `ticket_id` не снимали. Колонку `sale_id` в заказ не добавляли: она всегда
  была бы пустой.
- Страна: обе кнопки на карточке (Россия — ₽ на Т-Банк, Беларусь — WWC$ на SUNRAYSWORD,
  как продление); страна прошлого заказа — первой кнопкой. BYN — только для показа на сайте.
- «Кто привёл»: ref из ссылки, если партнёр есть и это не сам покупатель; иначе первое
  касание. Только в заказ (и строкой владельцу в теме), атрибуцию не создаём, денег нет.
- `shop_access` пишется и для курса, и для файла: из него собирается «Покупки».
- Курс без `course_slug` («Продажи») не продаётся: `available:false`, в боте — «готовится».
  Файл без `file_media_id` продаётся; ссылка появится, когда файл привяжут.
- Мастерская работает на обоих профилях (minimal и full): вход только по ссылке с сайта,
  в кабинете бота кнопки нет.

## Проверено

- `python -m pytest -q -p no:cacheprovider tests`: новых падений нет. Все оставшиеся
  (golden, parity, product_discovery, staging_sql_order «4x == 41», markets RLS, harness)
  падают и на `origin/master`. Точные цифры — в итоговом отчёте сессии.
- Фокус-гейт `release_core.ps1` (тот же набор, что в gate.yml): 186 passed.
- `run_postgres_integration_tests.ps1`: 0 failed (в том числе 2 новых `test_shop_postgres`).
- Новые тесты: `tests/test_shop.py` (31 — каталог, цена по стране, ссылка с ref и без,
  карточка, заказ → форум владельца, чек → кнопки, чужое фото не чек, запасной путь и
  возврат заказа, повтор кнопок, оплачено для файла, курса и услуги, только владелец,
  идемпотентность, отклонить, «витрина», ручки сайта), `tests/test_shop_postgres.py`
  (2 — V22 дважды + seed + пилот, заказ → чек → оплачено → доступ, два заказа в одной
  заявке, возврат чека, подпись файла, RLS-роль, нет начислений в ledger).
- `check_zone.py --base origin/master`: OK.

## Не проверено

- Живой Telegram и staging: не деплоил (по задаче).
- Отрисовку обложки на сайте: в seed обложек нет.

## Контракт для сайта

`GET /api/v1/public/shop` (и `/v1/public/shop`). Гостю — `Cache-Control: public, max-age=60`;
с сессией — `private, no-store`; `Vary: Cookie`. Порядок — `sort_order`, затем code.

```json
{"ok": true, "preview": false,
 "items": [{"code": "preza-vozrazheniya", "kind": "digital", "category": "materials",
   "title": "…", "subtitle": "PDF, 21 слайд", "description_html": "<p>…</p>",
   "prices": {"wusd": 5, "rub": 500, "byn": 17.5},
   "price_text": null,
   "cover_url": null,
   "cta": {"kind": "bot", "start": "shop_preza-vozrazheniya", "url": "https://t.me/<бот>?start=shop_preza-vozrazheniya"},
   "available": true, "status": "pilot"}]}
```
- `kind`: service | course | digital | external; `category`: services | courses | materials | tools.
- `prices` — в целых единицах, не в сотых. `price_text` не null — показывать его вместо цены (Gemini).
- `cta.kind`: `bot` (`start` — токен; `url` null, если у Core нет имени бота) | `link` (`url`).
- `ref` дописывать как `_<ref>` **только к `start`, который начинается с `shop_`**
  (`gemini` остаётся `gemini`). Весь токен ≤ 64 символа, иначе ref не дописывать.
- `available:false` — курс не готов: кнопку не показывать.
- `preview:true` — вошёл владелец: в списке есть `pilot`.
- Доли партнёра в ответе нет.

`GET /api/v1/content-access/me/purchases` — 401 без сессии; `private, no-store`.
```json
{"ok": true,
 "orders": [{"order_id": "uuid", "item": {"code": "…", "title": "…", "kind": "digital", "category": "materials"},
   "status": "new|receipt|paid|delivered|cancelled", "amount": {"value": 500, "currency": "RUB|WUSD"},
   "ticket": "#S-41", "created_at": "iso", "paid_at": "iso|null", "delivered_at": "iso|null"}],
 "access": [{"item": {"code": "…", "kind": "digital|course", "category": "…", "title": "…", "subtitle": "…", "cover_url": null},
   "granted_at": "iso", "download_url": "/academy-media/…?u=&e=&s= | null", "file": {"name": "…", "size": 1234, "mime": "application/pdf"},
   "expires_in": 3600, "course_url": "/academy/?course=vozrazheniya | null"}]}
```

`GET /api/v1/content-access/shop/files/{item_code}` → свежая ссылка (1 ч):
`{"ok", "item_code", "name", "size", "mime", "url", "expires_in"}`; ошибки `{"error": …}`:
404 `item_not_found` | `file_not_ready`, 403 `purchase_required`, 503 `media_unavailable`.

Админка (preview-админ; иначе 403 `shop_admin_required`):
- `GET /api/v1/content-access/shop/admin/items` → `{"ok", "statuses": [...], "items": [<как публичный> +
  sort_order, description_md, price_overrides {rub, byn}, course_slug, file_media_id, cover_media_id,
  external_url, requisites_note, delivery_note, confirmer, updated_at]}` — все статусы.
- `PATCH /api/v1/content-access/shop/admin/items/{code}` — любые из: title, subtitle, description_md,
  category, price_wusd, price_rub, price_byn (числа в целых единицах; rub/byn null — считать из W$),
  price_text, status, sort_order, cover_media_id (картинка Академии), file_media_id (файл Академии,
  только digital), course_slug (только course, курс должен существовать), external_url, requisites_note,
  delivery_note → `{"ok", "item"}`. Ошибки 400 `{"error": "unknown_fields|bad_status|media_not_found|…"}`, 404.
- `POST /api/v1/content-access/shop/admin/items` — `code, kind, category, title, price_wusd` (+ поля выше),
  статус по умолчанию `draft`; 409 `code_taken`.
- В боте то же коротко: «витрина», «витрина <code> published|pilot|draft|archived».

## Для выпуска (лиду)

1. V22 на общую базу до релиза Core. Таблицы в `schema_requirements`: без V22 релиз не пройдёт гейт.
2. PDF презентации: загрузить через author media API (kind `file`), затем
   `PATCH …/admin/items/preza-vozrazheniya {"file_media_id": "<id>"}`.
3. Курс `vozrazheniya` в Академии должен быть `published`: Академия показывает только
   опубликованные курсы, доступ из Мастерской без этого не виден.
4. Приёмка на staging (§6): форум «site» staging-бота уже зарегистрирован? Если нет — заявки
   придут владельцу в личку (кнопки работают и там).

## Вопросы лиду

- AGENTS.md (minimal): «приём чека … не показывать». Мастерская принимает чеки и на бою —
  только по ссылке с сайта, в кабинете бота кнопки нет. Подтвердить у владельца и дописать
  строку в AGENTS.md, как для «сервисов» (15.09)?
- Беларусь платит в WWC$ на SUNRAYSWORD, как продление; BYN (×3,5) — только на сайте. Так?
- V22 не добавлен в `postgres/scripts/apply_staging_platform_all.ps1` и `staging_proof_lib.py`
  (V19–V21 там есть): добавление ломает манифест `shared_staging_release_harness` и
  `tenant_canary`. Добавлять ли и кто обновляет манифест?
- Известные ограничения: (а) одно фото-чек переводит в `receipt` все неоплаченные заказы
  человека — лишние владелец отклоняет; (б) текст покупателя после заказа идёт в самую
  свежую открытую заявку (так устроен туннель) — если у него параллельно открыта заявка
  Gemini и он писал туда позже, текст уйдёт Карине. Чек это не затрагивает.
