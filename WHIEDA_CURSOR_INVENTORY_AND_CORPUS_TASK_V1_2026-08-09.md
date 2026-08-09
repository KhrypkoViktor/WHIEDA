# Cursor task: инвентаризация активов и карта возврата в продукт

## Цель

Сделать **read-only инвентаризацию** всей уже созданной фактуры WHIEDA. Результат нужен владельцу: увидеть, что готово, где лежит, что опубликовано в продукте, а что пока только файл/staging/Dify.

Не менять production, n8n, Telegram, Dify, Google Sheets, SQL runtime, карточки и исходные материалы.

## Исходные папки

- `D:\Projects\WHIEDA\RAG\`
- `D:\Obsidian\WHIEDA\05_WHIEDA_RAG\`
- `D:\Projects\WHIEDA\backend\platform-api\`
- `D:\Projects\WHIEDA\n8n\current\`
- `D:\Projects\WHIEDA\postgres\`
- `D:\Projects\WHIEDA\qa\`

## Что сделать

### 1. Манифест всех источников

Создать `D:\Projects\WHIEDA\WHIEDA_ASSET_MANIFEST_2026-08-09.csv`.

Одна строка на логический источник, не на каждый backup-файл. Поля:

```text
asset_id,layer,title,path,format,records_or_lines,source_kind,publication_state,review_state,provenance_state,user_visible,notes
```

Допустимые `layer`:

```text
raw,distillate,structured_master,runtime_sql,deep_rag,testimonials,medical,safety,marketing,bundles,media,qa,workflow,operator
```

Допустимые `publication_state`:

```text
live,staging,loaded_to_dify,local_only,raw_only,unknown
```

Не писать `live`, если это не подтверждено кодом/реальным runtime-отчётом.

### 2. Карта дистиллята

Для каждого TSV из:

```text
RAG\1 компиляция. диалоги с врачами\2_SQL_корпус_из_RAW\
```

зафиксировать:

- имя и число строк;
- ключевые поля;
- для чего слой нужен пользователю;
- куда он должен пойти: SQL / RAG / review врача / review бизнеса / owner;
- можно ли публиковать сейчас;
- зависимость от другого слоя.

Отдельно отметить как `blocked_raw`/`review_required`:

- medical review;
- safety signals;
- testimonials без согласия;
- derived dialogues без подтверждённого provenance;
- contradictions;
- quarantine.

### 3. Отзывы и видео

Не сжимать и не выбрасывать материал.

Собрать реестр файлов/экспортов отзывов:

- текстовые отзывы;
- видео;
- фото/материалы;
- исходные Telegram exports;
- уже выделенные `07_TESTIMONIALS.tsv`, `06_USAGE_PATTERNS.tsv`, `15_SAFETY_SIGNALS.tsv`.

Для каждого источника отметить: источник, приблизительный объём, есть ли автор/дата/ссылка, есть ли согласие, есть ли медицинский/safety риск, есть ли привязка к товару.

**Не превращать отзыв в медицинское доказательство.**

### 4. Функциональная матрица

Создать `WHIEDA_FEATURE_STATUS_MATRIX_2026-08-09.md`.

Для каждой функции поставить ровно один статус:

- `LIVE_CONFIRMED` — есть свежая проверка живого ответа;
- `IMPLEMENTED_NOT_LIVE_PROVEN` — код есть, но пользовательский результат не подтверждён;
- `DATA_READY_NOT_CONNECTED` — данные есть, runtime не подключён;
- `STAGING_OR_REVIEW` — специально не опубликовано;
- `MISSING` — нет ни кода, ни данных;
- `BLOCKED_BY_OWNER_OR_DOCTOR` — требуется внешний review/правило.

Проверить минимум:

1. карточка товара;
2. цена/первичка/повторка/PV/W$;
3. фото;
4. видео;
5. PDF/сертификаты;
6. сравнение;
7. follow-up контекст;
8. опечатки и алиасы;
9. уточнения вместо fallback;
10. корзина;
11. стартовый подбор;
12. акции РБ;
13. бизнес FAQ;
14. возражения;
15. safety;
16. отзывы;
17. Problem -> Solution;
18. бандлы;
19. глубокий RAG;
20. интернет;
21. коуч;
22. пользователи/доступ;
23. лиды/ref;
24. события/рассылки;
25. аналитика/gaps.

### 5. Карта «данные -> пользовательская функция»

Создать `WHIEDA_DATA_TO_FEATURE_MAP_2026-08-09.md`.

Для каждого слоя ответить:

```text
Что уже есть?
Что может увидеть пользователь сейчас?
Почему он этого ещё не видит?
Минимальное действие, чтобы показать это пользователю.
Кто должен подтвердить: владелец / врач / бизнес-лидер / админ / никто.
```

Особенно подробно разобрать:

- 9 276 строк RAW отзывов;
- 73 выделенных testimonials;
- 156 usage patterns;
- 59 Problem -> Solution кандидатов;
- 25 бандлов;
- 109 medical review;
- 56 safety signals;
- 14 deep-corpus документов;
- 37 resource links;
- 37 objections;
- 83 smoke cases.

### 6. Безопасный QA-скан

Сделать локальный скрипт:

```text
qa/inventory/run_whieda_asset_inventory.py
```

Он должен только читать файлы и генерировать три отчёта выше. Не ходить в сеть. Не трогать БД. Не изменять файлы-источники.

Проверки:

- путь существует;
- TSV читается и имеет header;
- строка не пустая;
- JSONL валиден;
- дубли logical source не маскируются;
- backup и generated reports не считаются отдельными источниками;
- у медицинского/отзывного слоя есть review/provenance статус;
- итоговые цифры отчётов совпадают с файлами.

Добавить pytest для сканера и fixtures, без live-инфраструктуры.

## Запрещено

- не трогать Google Sheets;
- не загружать файлы в Dify;
- не менять live Core/n8n/Telegram;
- не удалять RAW, backups, quarantine;
- не создавать «готовые факты» из непроверенных отзывов;
- не менять owner-locked карточки;
- не считать любой файл в `deep-corpus` автоматически опубликованным.

## Приёмка

1. Одна команда запускает inventory scan.
2. Есть CSV manifest и две понятные MD-карты.
3. Для каждого крупного актива виден путь до пользовательской функции.
4. В отчёте отдельно перечислены `ready to connect`, `needs review`, `not found`.
5. Нет изменений live/Sheets/Dify/Telegram.
6. Отчёт содержит только факты, без заявления «всё готово».
