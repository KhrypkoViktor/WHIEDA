# WHIEDA: актуальная карта проекта

Дата: 2026-07-26  
Статус: единственная актуальная карта файлов проекта

## 1. Правило входа

Перед любой работой в WHIEDA читать в таком порядке:

1. `00_READ_FIRST_WHIEDA_DEVELOPMENT_BIBLE_V1_2026-07-14.md`.
2. Этот файл: `WHIEDA_FILE_MAP_CURRENT.md`.
3. `WHIEDA_ACCESS_MAP_PRIVATE_2026-07-07.md` — только если работа требует live-доступов.
4. Нужное тематическое ТЗ из корня.

Старые карты и migration pack — архив. Они не являются рабочей инструкцией.

## 2. Что оставляем в корне

Только документы, с которых начинается работа:

- `00_READ_FIRST_WHIEDA_DEVELOPMENT_BIBLE_V1_2026-07-14.md` — главный источник решений.
- `WHIEDA_FILE_MAP_CURRENT.md` — эта карта.
- `WHIEDA_ACCESS_MAP_PRIVATE_2026-07-07.md` — private-доступы.
- `WHIEDA_COMPLIANCE_SELLING_LANGUAGE_ACCESS_MONETIZATION_RB_2026-07-14.md` — продающий язык, доступ и монетизация.
- `WHIEDA_SQL_FAST_ANSWERS_TWO_WEEK_BUILD_PLAN_2026-07-25.md` — текущее исполняемое ТЗ по Structure Basic и автономному циклу улучшения SQL-ответов.
- `WHIEDA_STRUCTURED_GROWTH_MODULES_SPEC_V1_2026-07-26.md` — следующее ТЗ: акции, стартовая корзина, ресурсы, события и сегментированные рассылки.
- `WHIEDA_SITE_REFERRAL_MVP_SPEC_V1_2026-07-26.md` — P0-ТЗ сайта: анонимный `nnm`, именные ref-профили, атрибуция заявок и SEO-защита.
- `WHIEDA_WWC_SITE_BOT_LEADS_MULTI_REF_SPEC_V1_2026-07-27.md` — развитие WWC после referral MVP: очередь заявок, attributed/assigned owner, watchers, Telegram CRM, сервисные точки, единый каталог и будущий SQL-консультант на сайте.
- `WHIEDA_DISTILLATE_TO_PRODUCT_LAYERS_BUILD_SPEC_V1_2026-07-26.md` — полное ТЗ Codex: validated distillate → staging/review → вопросы, отзывы, бандлы, возражения, Problem/Solution, контент/SEO и обучение.
- `WHIEDA_IDEAS_BACKLOG.md` — обсуждаемые идеи, которые еще не стали утвержденным ТЗ.

## 3. Рабочие папки

- `01_Context` — handoff, текущий runtime/status и review-loop контекст.
- `02_Demo` — runbook демонстрации.
- `assets` — манифесты и продуктовые ассеты.
- `dify` — Dify-материалы, спецификации и локальные сессии.
- `n8n` — workflow backups, patches, scripts, live exports, legacy operations.
- `postgres` — SQL-схемы, проверки и патчи runtime базы.
- `RAG` — RAW, компиляции, реестры источников и подготовленные RAG/SQL-корпусы.
- `07_Utilities` — побочные локальные утилиты, не ядро советника.
- `99_Archive` — исторические карты, migration pack и другие неактуальные ориентиры.

## 4. Что реально нужно читать по ситуации

### Текущий цикл Structure Basic

- Библия;
- `WHIEDA_SQL_FAST_ANSWERS_TWO_WEEK_BUILD_PLAN_2026-07-25.md`;
- свежий live status и export;
- access map только для применения и проверки live-изменений.

### Следующий structured growth-цикл

- Библия;
- `WHIEDA_STRUCTURED_GROWTH_MODULES_SPEC_V1_2026-07-26.md`;
- свежий live status/export;
- текущий SQL-план только для проверки уже реализованного поведения.

### Multi-tenant, коммерческая архитектура или сайт

- Библия;
- compliance-документ, если затрагиваются доступ, персональные данные, claims или монетизация;
- отдельное ТЗ создается только после продуктового решения Виктора.

### Работа с live n8n/Postgres/Dify

- Библия;
- access map;
- `01_Context/Runtime_and_Status`;
- свежий export из `n8n/live-exports`;
- соответствующий скрипт или патч.

### Новый агент с нуля

- Библия;
- эта карта;
- `01_Context/Handoff/WHIEDA_HANDOFF_2026-07-07.md`;
- access map только при необходимости доступов.

### История переезда или старые решения

- `99_Archive` только по необходимости. Не использовать как источник текущего состояния.

## 5. Правила порядка

- Новые ключевые документы добавлять в корень только если без них невозможно начать работу.
- Замененные стратегические документы с полезной историей убирать в `99_Archive`.
- Дубли, незавершенные копии и файлы без уникальной фактуры удалять после проверки.
- Рабочие скрипты и exports хранить в тематических папках, не в корне.
- Private-файлы не копировать в публичные/общие папки и не прикладывать внешним исполнителям без необходимости.
- При появлении нового важного слоя сначала обновлять эту карту и Библию, затем писать код.
