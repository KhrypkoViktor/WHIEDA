# Кабинет партнёра v1 — Core (STATE)

Ветка `core/cabinet-v1` от origin/master `34d2c85`, worktree
`D:\Projects\WHIEDA\.claude\worktrees\cool-elbakyan-601cab`, подпись Core-agent.
Исполнитель: Core-сессия. Выпуск и staging — лид. Контракт API для Site — раздел «Контракт» ниже.
Файл лежит в ветке: хук сессии запрещает писать в базовую копию `D:\Projects\WHIEDA`.

## Ход работ
- 02.10 — прочитаны AGENTS.md, ТЗ (включая §8–9 от 20:02), код кабинета бота, рефералов, content-access, ref, storage, renewal, crm digest. Базовый прогон unit на origin/master: 22 failed + 4 errors (golden/parity/markets/knowledge) — их не трогаю.
- 02.10 — сообщение лида через Site-сессию учтено: tier free|pro|leader, locks с причинами, без 404 без сайта, лидер через `PLATFORM_LEADER_PILOT_TELEGRAM_IDS` или `partner_product_access.product_code='leader_cabinet'`; CHECK не трогаю (V22).
- 02.10 — сделано: V21, общие выборки рефералов/истории (бот и сайт), API `/me/*`, фото, модерация в боте, public ref (bio + контакты), кабинет бота (кнопка + короткий текст), `/start renew`, `/start support`. Unit: те же 22 failed + 4 errors, что на origin/master, новых нет; postgres-сценарий `tests/test_cabinet_postgres.py` зелёный.

## Решения (отклонения от §4 — для лида и Site)
- Фото: байты в общей БД (таблица `partner_media` в V21), раздаёт Core `GET /api/v1/content-access/partner-media/<id>.jpg`; `photo_url` абсолютный `https://wwc.best/api/v1/content-access/partner-media/<id>.jpg` (resolve_photo_url отдаёт его как есть, `PLATFORM_PARTNER_MEDIA_PUBLIC_BASE`). Причина: `partner_library/storage.py` только читает/подписывает, том на бою смонтирован `:ro`, media.sysarch.pro на сайт-VPS, а staging и бой — разные контейнеры с одной БД (фото со staging иначе не видно на бою).
- Модерация: владельцу пишет бот этого процесса напрямую (staging → staging-бот, бой → боевой), не outbox: плановые строки outbox на staging не отправляются (`PLATFORM_SCHEDULED_NOTIFY_BINDINGS` только на бою), а кнопки должен обработать тот же Core. Отправка — фоном после ответа сайту. Потерялась карточка — владелец пишет боту `/profiles` («правки сайтов»): все ожидающие придут заново.
- Статусы заявки: кроме pending/applied/rejected ещё `replaced` (новая заявка вытеснила) и `cancelled` (партнёр отозвал) — у старой карточки снимаются кнопки, нажатие на неё ничего не применяет.
- Причина отказа: «Отклонить» → бот спрашивает (ForceReply «Причина отказа — заявка №xxxxxxxx»), Reply владельца одной строкой = причина, «-» = без причины.
- Настройки (§3.8): готовых endpoints у сайта не было (согласие — только в боте, таймзона — только у CRM для пилота), добавил `PATCH /me/settings`.
- CRM-счётчики: таблицы CRM — функция «crm», ядро их не читает → маленький пакет `app/cabinet_crm` (часть функции «crm» в schema_requirements); «дел сегодня» — через `app.crm.service.today_view` (то же, что видит CRM).
- Кабинет бота: первая кнопка «Открыть кабинет» (`with_site_login(<сайт>/me/)`), текст — статус сайта, баланс, реферальная ссылка, строка «Приглашённые, история WWC$ и профиль сайта — в кабинете на сайте». «Мои рефералы»/«История WWC$» в боте и на сайте — одни функции (`list_referrals`, `list_bonus_ledger`), в истории бота теперь русские подписи и даты.
- Лимиты: заявок на изменение — 10 в час на сайт (каждая — сообщение владельцу), загрузок фото — 30 в сутки на человека.

## Контракт API (Site)
Все пути есть в двух видах: `/api/v1/content-access/me/…` и `/v1/content-access/me/…`. Сессия — cookie content-access; без неё `401 {"error":"content_session_required"}`. Все ответы `Cache-Control: private, no-store`. Ошибка: `{"ok": false, "error": "<код>", "field"?: "<поле>"}`.

- `GET /me/overview` →
  `tier`: `free | pro | leader`;
  `locks`: только закрытые, `{ключ: причина}`. Ключи: `site` (нет сайта), `profile`, `calculator`, `repeat_prices`, `academy_pro`, `academy`, `crm`, `club`, `team`. Причины: `pro_required`, `club_required`, `leader_required`, `crm_pilot_only`, `feature_disabled`, `academy_not_open`. Партнёры, баланс, поддержка, настройки, «Что отправить» не запираются.
  `person`: `{display_name, telegram_username}`;
  `account`: как в `GET /me` (`balance{currency,amount_minor}`, `pro{status,paid_until,days_left,ref_code}`, `club{…}`) или null;
  `site`: null или `{ref_code, host, url, status (active|grace|suspended|no_subscription), paid_until, days_left, renew_url (t.me/<бот>?start=renew), price_url "/start/"}`;
  `profile`: null или `{ref_code, current, pending, last_review, limits}`; `current`/`changes` — форма полей ниже; `pending`/`last_review` — `{request_id, status, changes, previous, reject_reason, created_at, reviewed_at}`;
  `journey`: `{path: pro|free, steps:[{key,title,status (done|current|upcoming|locked),done,locked,lock_reason,manual,action}], current, done, total, percent}`. Шаги PRO: `presentation, profile, lesson1, invite_sent, crm_contact, club`; free: `presentation, free_lesson (если есть бесплатный курс), invite_sent, want_site`. `manual: true` — у presentation и invite_sent (кнопка ставит отметку). `lock_reason` у upcoming — `previous_step`.
  `counters`: `{invited, paid, crm_today (null — CRM закрыта), crm_contacts, academy: null | {course "zapusk-wwc", title, lessons_total, lessons_done, percent, locked, lock_reason, url}}`;
  `invite`: `{link, text}` (text — `invitation_text`, как в боте);
  `links`: `{cabinet "/me/", academy "/academy/", crm "/crm/", start "/start/", club "/club/", faq "/otvety/", bot, support (t.me/<бот>?start=support), renew, club_group (только с CLUB), news_channel}`;
  `settings`: `{timezone (null — ещё не выбирали), timezone_default "Europe/Moscow", marketing_opt_in (true|false|null)}`.
- `GET /me/journey` → `{tier, journey}`; `POST /me/journey/{presentation|invite_sent}/done` → `{step, done, done_at}`; другой шаг — `400 step_not_manual`. `POST /me/invite/share` → то же для invite_sent.
- `GET /me/referrals?cursor=&limit=20` (limit ≤ 50) → `{items:[{display_name, telegram_username, attributed_at, site_state (paid|waiting|no_site), status_label, subscription_status}], next_cursor, counts{invited, paid}}`; чужой курсор — `400 invalid_cursor`.
- `GET /me/bonus-ledger?cursor=&limit=20` → `{balance{currency "WWC$", amount_minor}, rules{first_payment_percent 20, renewal_percent 10, cash_out false, spend_url "/start/"}, items:[{entry_id, created_at, amount_minor (минус — списание), currency, entry_type, label}], next_cursor}`.
- `POST /me/profile` (JSON) — заявка. Тело — поля формы; ключа нет — поле не меняется; `""`/null у bio, contacts, socials — убрать. Новая заявка заменяет ожидающую и сохраняет её поля, которые сейчас не прислали; значение как на сайте — не изменение. Ответ `{status: pending|no_changes, pending}`. Ошибки: `402 pro_required` (нет оплаченного сайта), `400 no_changes`, `429 too_many_requests` (10 в час), `400 <код> + field`.
  Поля и проверка: `display_name` 2–80, одна строка; `bio` ≤ 600 символов, абзацы, без HTML; `photo_url` — только адрес из `POST /me/profile/photo` этого сайта (`invalid_photo`); `contacts{phone, whatsapp, viber}` → E.164 (`invalid_phone`), `max_url` https://max.ru/…, `email` (`invalid_email`), `address` ≤ 200; `socials{telegram_channel_url (t.me), vk_url (vk.com|vk.ru), instagram_url, youtube_url (youtube.com|youtu.be), tiktok_url}` — только https (`https_required`) и только своя сеть (`wrong_site`), `invalid_url`. HTML — `html_not_allowed`; лишний ключ — `unknown_field`. camelCase-ключи публичного контракта (`maxUrl`, `telegramChannelUrl`, …) тоже принимаются.
- `GET /me/profile/pending` → `{pending, last_review}`; `DELETE /me/profile/pending` → `{cancelled: bool}`.
- `POST /me/profile/photo` — multipart (поле `photo` или `file`, нужен Content-Length) или тело `image/*`; ≤ 20 МБ; JPEG/PNG/HEIC/WebP → JPEG ≤ 1200 px без EXIF. Ответ `{media_id, photo_url, width, height, size_bytes}`. Ошибки: `402 pro_required`, `413 photo_too_large`, `415 photo_type_unsupported`, `422 photo_unreadable`, `400 photo_too_small` (< 160 px), `400 photo_missing`, `411 length_required`, `429 too_many_uploads` (30 в сутки). Фото попадает на сайт только через заявку.
- `PATCH /me/settings` `{timezone?, marketing_opt_in?}` → `{settings}`; `400 invalid_timezone`.
- `GET /api/v1/content-access/partner-media/<id>.jpg` — публично, `Cache-Control: public, max-age=31536000, immutable`.
- Публичный `/api/v1/public/ref/<code>`: `consultant.bio` (null — нет в Core) и в `consultant.socials` ключи `phone, whatsapp, viber, maxUrl, email, address` (рядом с прежними 7).
- Бот: `t.me/<бот>?start=renew` — продление (на профиле minimal — ответ «оформляется вручную», как кнопка), `?start=support` — то же обращение, что `/support`.

## Открытые вопросы (одной строкой)
- nginx сайта: `location ^~ /api/v1/content-access { client_max_body_size 16k; }`, у Core-nginx дефолт 1m → загрузка фото (до 20 МБ) получит 413; нужен отдельный location для `/api/v1/content-access/me/profile/photo` с 20m (не моя зона, решение лида). Сайт может ужимать фото в браузере — всё равно больше 16 КБ.
- Legacy-синк `n8n/current/run_partners_ref_runtime_sync_2026-08-01.py` пишет `public_profile = excluded.public_profile` целиком: если его запустить, пропадут правки кабинета (и уже сейчас subdomain/тема); не трогал.
- @sunraysword — это аккаунт владельца 688931415 (тесты support_site, владелец nnm/dev), отдельного тестового id в коде нет; второй аккаунт @khrypko_pro добавить в `PLATFORM_LEADER_PILOT_TELEGRAM_IDS` вручную, id не выдумываю.
- Фото и «о себе» многих партнёров лежат только в реестре сайта (`src/data/referrals.js`), не в Core: для них шаг «Заполнить профиль» не отмечен, пока они не отправят профиль через кабинет (или лид не перенесёт данные в public_profile).
- AGENTS.md (профиль minimal) перечисляет кнопки кабинета без «Открыть кабинет» — дописать строку решает лид.
- Фото, загруженное через staging и применённое, откроется на бою только после выкладки этого Core на бой (раздаёт боевой Core).
- Миграцию V21 применить к общей БД до выкладки кода (schema gate требует 3 новые таблицы) и записать в APPLIED.log — делает лид.
