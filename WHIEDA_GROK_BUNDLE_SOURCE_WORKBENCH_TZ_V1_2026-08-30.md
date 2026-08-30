# ТЗ: WHIEDA Bundle Source Workbench V1

## Зачем

В Core уже работают 28 активных наборов. Теперь нужно не изобретать ответы заново, а разобрать весь накопленный RAW-материал в удобный рабочий стол: откуда взята фраза, к какому набору она относится, какие вопросы люди реально задают и чего ещё не хватает.

Это большая механическая работа для младшего разработчика. Её результат не публикуется сам и не меняет поведение бота.

## Золотое правило разделения работы

Ты работаешь как младший разработчик. Terra/лид владеет архитектурой, Core runtime, SQL, Google Sheets, n8n, staging, production и выкладкой. Тебе нельзя менять эти поверхности, даже если видишь удобный способ.

Ты владеешь только изолированным QA-workbench, генераторами, corpus, fixtures, отчётом и локальными тестами. Используй Git Bash для чтения, поиска и локальных команд.

## Рабочая ветка

Создай отдельный worktree от актуальной ветки WHIEDA. Ветка:

```text
grok-whieda-bundle-source-workbench
```

Разрешённые изменения только здесь:

```text
qa/whieda_bundle_source_workbench/**
backend/platform-api/tests/test_whieda_bundle_source_workbench.py
backend/platform-api/docs/WHIEDA_BUNDLE_SOURCE_WORKBENCH_LOCAL_REPORT.md
```

Не менять:

```text
backend/platform-api/app/**
postgres/**
n8n/**
03_Website/**
Google Sheets
production/staging/Telegram
```

## Источники только для чтения

1. `RAG/RAW диалоги с врачами/**`
2. `qa/whieda_bundle_triage/fixtures/09_BUNDLE_CANDIDATES.tsv`
3. `qa/whieda_bundles/**`
4. `n8n/live-exports/structured-master/**/` — только самый свежий валидный snapshot, если доступен.
5. `WHIEDA_BUNDLE_REGRESSION_CORPUS_TZ_V1_2026-08-30.md` и существующие отчёты bundle triage.

Не брать тексты из интернета и не дописывать отсутствующие факты от себя.

## Что собрать

### A. Инвентарь источников

Создай `sources_inventory_v1.jsonl`. Для каждого обработанного файла укажи:

- `source_id`
- относительный путь
- тип (`raw_chat`, `tsv_candidate`, `existing_bundle`, `snapshot`)
- SHA-256
- число извлечённых фрагментов
- статус (`processed`, `empty`, `unreadable`, `out_of_scope`)

Минимум: все доступные файлы из RAW-папки и все строки `09_BUNDLE_CANDIDATES.tsv`.

### B. Атомарные фрагменты

Создай `source_fragments_v1.jsonl`. Один фрагмент — короткая смысловая единица из источника, а не целая простыня. Обязательные поля:

- `fragment_id`
- `source_id`
- `source_locator` (номер строки, сообщение или иной точный locator)
- `verbatim_text`
- `topic_tags` (массив)
- `candidate_bundle_ids` (массив, может быть пустым)
- `fragment_kind`: `user_need`, `product_mention`, `usage_story`, `objection`, `question`, `claim`, `unknown`
- `review_status`: `linked`, `unlinked`, `duplicate`, `contradiction_candidate`

Не переписывай и не улучшай исходные формулировки. `verbatim_text` должен позволить владельцу увидеть исходную фактуру. Не клади в отчёт или fixtures персональные контакты, телефоны, usernames, ссылки-приглашения и полные диалоги.

### C. Карта к активным наборам

Создай `bundle_evidence_map_v1.tsv`.

Одна строка связывает активный bundle id с fragment id. Поля:

```text
bundle_id,fragment_id,evidence_role,confidence,review_note
```

`evidence_role`: `intent`, `product`, `question`, `objection`, `usage_story`, `claim`.

Нужно покрыть все 28 активных наборов хотя бы одной из строк `source` или пометить `no_source_found` в отдельном `uncovered_active_bundles_v1.tsv`. Не подставляй доказательства по догадке.

### D. Реальные фразы для будущей проверки

Создай `bundle_prompt_candidates_v1.jsonl`:

- минимум 180 уникальных фраз;
- минимум 100 привязаны к существующим 28 bundles;
- минимум 40 фраз без подходящего набора (`expected_bundle_id: null`);
- минимум 40 follow-up реплик с `context_before`;
- каждая фраза с `source_ref` и `review_status`.

Это кандидаты, не новый runtime test. Не ставь их как обязательные hard-fail ожидания без `owner_approved=true`.

### E. Дубликаты и противоречия

Сделай `review_queue_v1.tsv` с категориями:

- `duplicate_source`
- `possible_duplicate_bundle`
- `unlinked_material`
- `contradiction_candidate`
- `needs_owner_mapping`

У каждой строки должна быть ссылка на исходный `fragment_id` и короткая причина. Никаких автосклеек и никаких изменений 28 наборов.

### F. Инструменты качества

Напиши builder/linter и offline runner:

```bash
python qa/whieda_bundle_source_workbench/build_workbench.py
python qa/whieda_bundle_source_workbench/run_workbench.py --offline
python -m pytest backend/platform-api/tests/test_whieda_bundle_source_workbench.py -q
```

Линтер обязан проверить:

- уникальные id;
- ссылки на существующие source/fragment/bundle;
- отсутствие явных телефонов, Telegram usernames и invite links в публикуемых QA артефактах;
- минимумы по A-D;
- что каждый prompt имеет provenance;
- что не утверждённые prompts не выданы за обязательные runtime ожидания.

## Отчёт

Сделай `backend/platform-api/docs/WHIEDA_BUNDLE_SOURCE_WORKBENCH_LOCAL_REPORT.md` на русском:

1. сколько источников и фрагментов обработано;
2. сколько активных bundles покрыто фактурой;
3. сколько prompt candidates по категориям;
4. топ 20 строк review queue;
5. конкретно что не найдено и что требуется от владельца;
6. явная строка: `Core, SQL, n8n, Sheets, Telegram и production не изменялись`.

## Готовность

Работа готова, когда все три команды выше проходят, есть один focused commit, а в финальном отчёте указаны:

- commit SHA;
- фактические числа;
- список всех неразрешённых owner decisions;
- подтверждение, что runtime не менялся.

При неясности не останавливаться и не спрашивать лида на каждой строке: помечай `needs_owner_mapping` и продолжай с остальным корпусом.
