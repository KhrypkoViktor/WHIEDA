# Новый tenant: канон запуска V1

Дата: 2026-08-20  
Владелец: Виктор Хрипко  
Статус: действующий канон для NSP, Никиты и следующих tenant.

## Зачем

Новый tenant не является копией WHIEDA или NSP. Он получает один Platform Core
и свой изолированный набор данных, Telegram binding, медиа и владельцев.
Цель: запускать следующий tenant повторяемым конвейером, а не заново собирать
бота и webhook.

## Что переиспользуется из платформы

- Platform Core, SQL-модель и structured advisor;
- Telegram `BotBindingContext`: один binding определяет tenant, secret,
  username и исходящий token;
- импортёр tenant-данных, review-статусы и smoke-набор;
- каталог, карточки, уточнения, корзина и universal fallback;
- media-правило `media/{tenant_id}/{sku}/`;
- staging gates, backup и доказательства изоляции.

Нельзя копировать в новый tenant чужие SKU, цены, фото, ответы, Telegram token,
Google Sheet или webhook. Новый tenant получает шаблон, не содержимое NSP.

## Что владелец нового tenant должен собрать

1. Название tenant, владелец, страна, язык, валюта и контакт эскалации.
2. Отдельный Telegram bot: username, token и webhook secret передаются только
   через secret store, не через Git или таблицы.
3. Master-таблица по шаблону: товары, SKU, алиасы, цены, PV, карточки, FAQ,
   видео, PDF и ссылки на источники.
4. Фото на нашем диске по SKU: оригинал сохраняется; при размере более 300 KB
   рядом готовится webp quality 80. Путь: `media/{tenant_id}/{sku}/`.
5. Google Drive с видео, PDF и текстами; NotebookLM может помогать разбирать
   материалы, но не является runtime-источником.
6. Список публичных лидеров, контактов, мероприятий, акций и правил передачи
   человека наставнику.
7. Первые 10-20 товаров для canary и назначенного бизнес-reviewer.

## Состояния данных

```text
raw -> candidate -> review_required -> approved -> published
```

- `raw` и `candidate` никогда не отвечают пользователю;
- для `approved` есть источник и владелец решения;
- только `published` виден runtime;
- отсутствие цены, фото или утверждения не заменяется догадкой: товар остаётся
  в review или бот предлагает следующий понятный шаг.

## Порядок запуска

1. Создать отдельный private Git repository tenant и заполнить manifest.
2. Прогнать локальный импорт в отдельную staging-БД, проверить tenant isolation.
3. Сформировать review-пакет для бизнеса: товары, цены, карточки, фото,
   документы и спорные места.
4. Получить минимум 10 утверждённых canary SKU и прогнать smoke.
5. Применить Core binding slice и shared staging truth table.
6. Создать binding в статусе `disabled`; проверить unknown/disabled 200 ACK без
   обработки и без fallback в другой tenant.
7. После отдельного решения владельца включить canary: два пользователя,
   один Core process, без deploy/restart в окно проверки.
8. Только после canary включать production webhook и расширять catalog.

## Неподвижные правила Telegram

- unknown или disabled binding -> HTTP 200 `{\"ok\":true}`, processing 0;
- active binding с неверным secret -> 403;
- недоступный/misconfigured binding -> 503;
- ответ всегда уходит token того же binding;
- новый tenant никогда не использует username, token, каталог или legacy
  fallback WHIEDA/NSP.

## Definition of ready for the next tenant

- есть отдельный repo и tenant manifest;
- master соответствует шаблону;
- данные импортируются идемпотентно и tenant isolation доказана;
- опубликован только approved canary subset;
- фото размещены под своим tenant media path;
- 60 smoke-кейсов зелёные: продукт, цена, медиа, alias, уточнение, контекст,
  unknown input, cross-tenant isolation;
- binding включается только после Gate E владельца.

## Связанные документы

- Core Telegram binding: `WHIEDA_TELEGRAM_MULTI_TENANT_BINDING_TZ_V1_2026-08-19.md`.
- Core release slice: `WHIEDA_CORE_BINDING_RELEASE_SLICE_TZ_V1_2026-08-20.md`.
- NSP как первый экземпляр: `D:\Projects\NSP\qa\nsp\handoff\NSP_TENANT_LAUNCH_CONTRACT_V1_2026-08-19.md`.
- Локальные фото NSP: `D:\Projects\NSP\qa\nsp\handoff\NSP_PHOTO_DISK_WEBP_CONTRACT.md`.
- Координация Core и tenant: `D:\Projects\_peer-sync\nsp-whieda\CURRENT.md`.
