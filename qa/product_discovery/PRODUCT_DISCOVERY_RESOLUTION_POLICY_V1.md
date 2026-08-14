# Политика разрешения Product Discovery Map V1

Документ для владельца Core: как использовать карту кандидатов
`PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv` при втором рельсе советника —
«не знаю точное название → показать 2–3 релевантных продукта».

Источник данных: master-снимок `aliases.tsv` + `products.tsv` (read-only).
HLR triage: live report `HLR_HTTP_REPORT_20260814T154952Z-f5e9841f.json`.
Runtime, n8n и корпус HLR этим документом **не меняются**.

## Статусы строк карты

| status | Смысл для Core |
|--------|----------------|
| `ready_for_core_review` | Кандидат подтверждён снимком; можно открыть карточку **только** если фраза однозначна (полное имя / цвет+эликсир / exact alias). |
| `needs_owner_review` | Есть SKU, но короткий/слабый токен — только `show_choices` или `clarify_only`, не auto-open. |
| `generic_category` | Широкая категория или намеренная неоднозначность; **только** меню 2–3 SKU или `task_selection`, без silent open rank 1. |
| `do_not_resolve` | Запрещено привязывать к SKU; только уточнение / choices без default SKU. |

## Когда Core может открыть карточку напрямую (`open_card`)

1. **Точное имя или сильный алиас** — `confidence=high`, `ready_for_core_review`, rank 1.
   - «красный эликсир» → F001-02; «зубная паста» → EU-N000030-25; «наколенники» → T001.
2. **Пользователь уточнил после clarification** — второй ход с полным названием.

**Не auto-open**, даже если в снимке один SKU, когда HLR требует choice rail на nickname:
«маска», «шампунь», «очки», «чай», «кофе» — в карте rank 1 есть, но live HLR ожидает `show_choices` на первом ходе.

## Когда показывать 2–3 варианта (`show_choices`)

1. **`generic_category`** — «эликсир», «активатор», «капсулы», «косметика», «бад», «прибор для дома», «подарок» (после выбора направления).
2. **Несколько SKU** — «паста» (F071-00 vs EU-N000030-25), «гель» (D011-00 vs C002-00).
3. **`needs_owner_review`** — «pro», «пояс», «массажer», «magic».

### Активатор — намеренная неоднозначность

В `aliases.tsv` rank 1 — базовый M015-00, но есть PRO (EU-N000031-25). Строки карты:
`generic_category` для обоих SKU. **Core не должен молча открывать M015-00** по одному слову «активатор» — live HLR фиксирует auto-open вместо `product_choices`.

## Когда открывать task selection (`task_selection`)

1. **Цель без конкретного товара** — «нужен подарок», «хочу косметику», «подарок женщине что лучше».
2. **Строка «подарок» в карте** — `generic_category`; SKU C065-00 / EU-N000036-26 только как кнопки **после** выбора направления, не на первом ходе «нужен подарок».

## Цвета и generic-категории

- Одиночный цвет → `do_not_resolve`, пустой SKU; live HLR `красный` — отдельный corpus gap по формулировке «уточн».
- Generic-слова → rank 1–3 это **предложение выбора**, не решение за пользователя.

## Таблица интеграции для Core

| phrase | Core action | why | up to 3 SKU buttons |
|--------|-------------|-----|---------------------|
| активатор | show_choices | base+PRO ambiguity despite alias rank 1 | M015-00, EU-N000031-25 |
| паста | show_choices | two paste families in snapshot | F071-00, EU-N000030-25 |
| эликсир | show_choices | three-color family | F001-02, F003-02, F002-02 |
| красный / зелёный / синий | clarify_only | weak colour tokens; no auto SKU | — |
| красный эликсир / зелёный эликсир / синий эликсир | open_card | colour+product explicit | F001-02 / F003-02 / F002-02 |
| гель | show_choices | Foherb vs Fundesee | D011-00, C002-00 |
| капсулы | show_choices | multiple capsule SKUs | F028-00, F024-00, F007-00 |
| подарок | task_selection | goal-first; gift SKUs only after direction | C065-00, EU-N000036-26 (later step) |
| крем | clarify_only | no SKU in snapshot | — |
| цена (без контекста товара) | show_choices or universal_menu | live HLR: need product context first | — |

Допустимые значения `Core action`: `open_card`, `show_choices`, `task_selection`, `universal_menu`, `clarify_only`.

## Подключение к Core (после review архитектора)

1. Загрузить TSV как read-only слой рядом с alias resolver.
2. Lookup: `normalize(user_text)` → строки по `normalized_phrase`, сортировка по `candidate_rank`.
3. Применить политику по `status` / таблице интеграции — **не** по голому alias priority.
4. Только `canonical_name` из карты для кнопок выбора — без medical copy из product_cards.
