# Core Gate H: Tenant Media Delivery Contract V1

## Цель

Сделать единый безопасный путь от **одобренной media-строки tenant release package** до ответа Telegram.  В результате карточка NSP сможет вернуть только HTTPS-ссылку нашего медиа-контура NSP и отправить фото именно через bot binding NSP.  WHIEDA и другой tenant не должны получить эту ссылку или этот файл.

Это не загрузка файлов и не публикация tenant.  Все реальные файлы, nginx, CDN, Telegram `setWebhook`, shared staging, production, Google Drive, n8n и `03_Website/` вне scope.

## База и рабочая ветка

- База: `origin/core-tenant-currency-price-plane` @ `37819c7`.
- Новый чистый worktree и ветка: `core-tenant-media-delivery`.
- Не merge в грязный `feat/platform-scale-core`.
- Один focused commit, manifest и local report в `D:\Projects\_peer-sync\nsp-whieda\from-core`.

## Контракт media entry

Использовать уже существующий `media.json` tenant package. Не заводить второй каталог, второй список SKU или отдельный WHIEDA-specific mapping.

Допустимый опубликованный URL строится только так:

```text
{PLATFORM_TENANT_MEDIA_BASE_URL}/{tenant_id}/{sku}/{filename}
```

Пример для будущего NSP:

```text
https://<media-host>/media/nsp-maxim/1346/main.webp
```

Точная конфигурация base URL может быть `PLATFORM_TENANT_MEDIA_BASE_URL`; без неё медиа считается недоступным и бот честно отправляет текст без фото. Не подставлять localhost, Drive, `wwc.best`, сайт WHIEDA, произвольный URL из release package или URL другого tenant.

`tenant_id`, `sku` и `filename` должны проходить строгую проверку сегмента пути. Запрещены `/`, `\\`, `..`, query/fragment, URL-схемы и любые попытки выйти из `media/{tenant}/...`.

## Поведение

1. Telegram ingress уже создаёт `BotBindingContext`. Вся доставка получает tenant только оттуда, никогда из текста, callback, session или payload.
2. Для `structured_card`, `structured_photo` и любого existing photo-first path Core получает approved media текущего tenant.
3. При валидном локальном media reference: `sendPhoto` сначала, затем полный `sendMessage`; оба вызова используют token текущего binding.
4. Нет approved media, не задан base URL или reference невалиден: не вызывать `sendPhoto`; текст карточки всё равно уходит и не содержит чужую или внешнюю ссылку.
5. Медиа NSP не видно WHIEDA, и наоборот. Одинаковые SKU между tenants также изолированы.
6. Это только доставка. Не открывать publish release package и не менять статусы `approved`/`review_required`.

## Обязательные тесты

Добавить focused tests и включить их в существующий Core slice:

- NSP reference → ровно один URL `.../media/nsp-maxim/{sku}/{file}`; photo-first и token NSP.
- Тот же SKU у WHIEDA → только URL WHIEDA; NSP URL не появляется.
- `../`, slash, backslash, URL, query/fragment в любом сегменте → media rejected, отправлен только text.
- base URL отсутствует → только text, без invented URL.
- `wwc.best`, Drive и любой внешний URL в package не проходят.
- unknown/disabled binding не создаёт delivery.
- вторичный delivery/retry не пересылает media другому binding.
- существующая WHIEDA photo delivery остаётся зелёной.

## Local proof

1. Полный release slice от Gate F/E1 без `--deselect`.
2. `python postgres/scripts/run_local_staging_proof.py` остаётся зелёным. Новая SQL-миграция не нужна, если контракт можно выразить через current release media payload; если без таблицы действительно нельзя, обосновать её в отчёте и добавить в apply order с x2 proof.
3. Local Core HTTP/Telegram mock proof: NSP и WHIEDA с одинаковым SKU, плюс invalid-reference matrix.

## Строгие запреты

- Не трогать shared staging, Supabase, production, SSH, web server, n8n, webhook, Sheets.
- Не класть binary/webp в Core Git.
- Не добавлять fallback на WHIEDA, если у tenant нет media.
- Не делать `--publish` рабочим.

## Результат

Commit, полный список тестов и их фактический вывод, manifest с базой/head и перечнем изменённых файлов, `CORE_GATE_H_TENANT_MEDIA_DELIVERY_LOCAL_REPORT.md`. Отдельно перечислить, что потребуется от лида позже для реальной выкладки файлов: HTTPS media host и upload manifest, без выполнения этой выкладки.
