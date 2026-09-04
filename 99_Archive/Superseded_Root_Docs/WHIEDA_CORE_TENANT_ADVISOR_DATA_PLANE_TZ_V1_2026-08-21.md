# Core Gate D: Tenant Advisor Data Plane V1

Дата: 2026-08-21  
Владелец: Core lead  
Исполнитель: Core developer  
Статус: local implementation only; без deploy/shared staging/webhook.

## Цель

После Telegram binding и durable inbox новый tenant должен получать только
свои structured данные. NSP или Никита не могут унаследовать товары, цены,
карточки, фото, PDF, FAQ, business-тексты или fallback WHIEDA.

## Старт и scope

Создать чистый worktree от `44aa215` (`core-telegram-durable-inbox`) и ветку
`core-tenant-advisor-data-plane`.

Разрешено:

- `backend/platform-api/app/advisor/**`;
- минимально необходимые `app/telegram/**` для передачи уже построенного
  `BotBindingContext` в advisor request;
- additive SQL/migrations и staging proof только для tenant data plane;
- tests, focused report и manifest.

Запрещено: deploy, shared staging, production, n8n, setWebhook, Sheets,
сайт, cart, admin cabinet, изменение данных WHIEDA/NSP, backfill/publish.
Не merge в грязный `feat/platform-scale-core`.

## Неподвижный контракт

1. Tenant для Telegram advisor берётся только из server-side
   `BotBindingContext`, не из user text, callback, session, HTTP payload или
   client-provided query parameter.
2. Все repository reads требуют tenant scope: products, aliases, cards,
   prices, PV, FAQ, media, PDF/video/certificates, promotions и business FAQ.
3. При пустом каталоге tenant ответ честно ведёт в его меню/эскалацию, но
   никогда не ищет или не показывает WHIEDA как fallback.
4. Одинаковые alias/SKU в разных tenant остаются независимы. Один и тот же
   `update_id`/chat не переносит product context между tenant.
5. Исходящая подпись/название берутся из tenant config или нейтральной
   платформенной фразы, не хардкодом `WHIEDA` для чужого tenant.
6. Disabled/unknown binding по-прежнему не создаёт advisor/session/inbox
   данных.

## Данные и SQL

- Не создавать второй каталог и не копировать данные NSP в Core seed.
- Если для tenant display/config недостаточно существующих полей, добавить
  одну additive migration с явными safe defaults.
- Existing WHIEDA local seed оставляется как есть; для тестов создать
  минимальный isolation fixture `whieda` + `nsp-maxim` с пересекающимися
  alias/SKU и разными card/price/media/FAQ.
- Миграция идемпотентна, входит в staging apply один раз, proof проходит x2.

## Обязательные проверки

- NSP Telegram request с alias, который есть только у WHIEDA -> нет утечки;
- общий alias -> выбирается NSP товар, цена, фото и FAQ;
- WHIEDA request при тех же input получает WHIEDA данные;
- price/media/PDF/certificate/comparison/basket не пересекают tenant;
- follow-up `цена`, `фото`, `видео`, `сертификат` сохраняет tenant;
- session/callback из другого tenant не принимаются;
- прямой HTTP payload с `tenant=whieda` не переопределяет binding NSP;
- unknown/disabled/bad secret не создают advisor/session/inbox строк;
- full B1+B2+C+D suite и local staging proof зелёные.

## Результат

Один focused commit и push. В `D:\Projects\_peer-sync\nsp-whieda\from-core`
положить `CORE_GATE_D_TENANT_DATA_PLANE_MANIFEST_V1.json` и
`CORE_GATE_D_TENANT_DATA_PLANE_LOCAL_REPORT.md` с командами, фактическими
результатами, SHA новых migration и списком не решённых business-data gaps.

После Gate D lead собирает B1+C+D как единый Core пакет для shared staging.
