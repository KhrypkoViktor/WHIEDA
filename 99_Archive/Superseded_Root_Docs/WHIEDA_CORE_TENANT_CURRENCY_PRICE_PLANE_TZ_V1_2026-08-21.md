# Core Repair E1: Tenant Currency Price Plane V1

Дата: 2026-08-21  
Исполнитель: Core developer  
База: `4744737` (R1) или итоговая Gate F release branch  
Ветка: `core-tenant-currency-price-plane`

## Дефект

NSP package содержит 17 официальных USD розничных цен, но Gate E признаёт
ценой только `retail_price_byn`, `retail_price_rub` или partner fields. В
результате каждая реальная USD цена получила ложный `price_missing=true`.
Это блокирует корректный multi-tenant запуск и не исправляется подстановкой
BYN или партнёрской цены.

## Цель

Сделать generic price representation для tenant release package и будущего
runtime publish: любая подтверждённая розничная валюта (USD, BYN, RUB и далее)
хранится и отображается честно, без пересчёта, если курс не передан отдельно.

## Scope

- `backend/platform-api/scripts/tenant_release/**`;
- release package schema/examples/tests;
- additive release-package SQL/staging proof;
- минимальные advisor formatting/repository contracts, если нужны для
  будущего runtime mapping.

Не трогать live данные, shared staging, production, n8n, Sheets, bot binding,
сайт и cart. Не добавлять вычисление BYN, курс, партнёрские цены или скидку
30% без отдельного официального price source.

## Контракт цены

В package используется нормализованный список retail price entries:

```json
[{"kind":"retail","amount":"37.13","currency":"USD","source":"catalogue_2026"}]
```

- amount строго положительный decimal;
- currency ISO uppercase; retail price обязателен для `price_missing=false`;
- package может содержать несколько retail currencies с отдельными sources;
- partner price хранится отдельно и не возникает из формулы;
- `price_missing=true` только когда подтверждённой розничной цены реально нет;
- UI/answer показывает сумму с валютой как есть; `~BYN` возможно только при
  отдельном dated exchange-rate record.

## Проверки

- NSP-compatible synthetic USD product: `price_missing=false`, candidate
  содержит USD сумму и источник;
- BYN/RUB прежние fixtures без регрессии;
- malformed amount/currency/zero -> validation error;
- USD не превращается в BYN/RUB и не теряется в staging/candidate version;
- price source/hash/version сохраняются;
- полный B1-E+R1+E1 suite без deselect зелёный;
- local staging apply x2.

## Result

Один focused commit/push, manifest/report в `from-core`. После E1 NSP
developer меняет 17 текущих `price_missing=true` на реальные USD entries и
повторяет validate. Runtime publish всё ещё не включается в этом слайсе.
