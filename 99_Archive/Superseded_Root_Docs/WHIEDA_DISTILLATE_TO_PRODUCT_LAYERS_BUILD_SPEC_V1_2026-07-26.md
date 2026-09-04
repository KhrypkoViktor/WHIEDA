# WHIEDA: из дистиллята RAW в продуктовые слои

Версия: 1.0  
Дата: 2026-07-26  
Исполнитель: Codex-разработчик WHIEDA  
Статус: утвержденное ТЗ после зеленого PASS 004 корпуса  
Приоритет слоев: вопросы → отзывы → бандлы → возражения → Problem/Solution → контент/SEO → обучение

## 1. Цель

Превратить очищенный и проверенный дистиллят реальных Telegram-диалогов WHIEDA в работающие слои:

- быстрого SQL-бота;
- сайта и будущего WHIEDA World Club;
- продуктового подбора;
- базы отзывов;
- бизнес-консультанта;
- safety/quality-системы;
- контент-фабрики;
- будущего обучающего коуча.

RAW не импортируется напрямую. Рабочий источник этого этапа — только валидированный дистиллят с точной ссылкой на RAW.

## 2. Обязательные документы

Перед работой прочитать:

1. `D:\Projects\WHIEDA\00_READ_FIRST_WHIEDA_DEVELOPMENT_BIBLE_V1_2026-07-14.md`
2. `D:\Projects\WHIEDA\WHIEDA_FILE_MAP_CURRENT.md`
3. `D:\Projects\WHIEDA\WHIEDA_SQL_FAST_ANSWERS_TWO_WEEK_BUILD_PLAN_2026-07-25.md`
4. `D:\Projects\WHIEDA\WHIEDA_COMPLIANCE_SELLING_LANGUAGE_ACCESS_MONETIZATION_RB_2026-07-14.md`
5. `D:\Projects\WHIEDA\RAG\1 компиляция. диалоги с врачами\2_SQL_корпус_из_RAW\CLAUDE_NEXT_TASK_PASS_004_QA_AND_DEEP_EXTRACTION.md`
6. свежий `BATCH_REPORT.md`;
7. свежий `CORPUS_VALIDATION_REPORT.md`;
8. свежий source register.

Если этот документ расходится с Библией, действует Библия и более новая прямая команда Виктора.

## 3. Входные данные

Основная папка:

`D:\Projects\WHIEDA\RAG\1 компиляция. диалоги с врачами\2_SQL_корпус_из_RAW`

Ожидаемые таблицы:

- `00_SOURCE_REGISTER.tsv`;
- `01_QUESTIONS.tsv`;
- `02_ALIASES.tsv`;
- `03_SQL_FAQ_CANDIDATES.tsv`;
- `04_CLARIFICATION_RULES.tsv`;
- `05_OBJECTIONS.tsv`;
- `06_USAGE_PATTERNS.tsv`;
- `07_TESTIMONIALS.tsv`;
- `08_PROBLEM_SOLUTION_CANDIDATES.tsv`;
- `09_BUNDLE_CANDIDATES.tsv`;
- `10_MEDICAL_REVIEW_QUEUE.tsv`;
- `11_MARKETING_LANGUAGE.tsv`;
- `12_RESOURCE_LINKS.tsv`;
- `13_SMOKE_CASES.tsv`;
- `14_KNOWLEDGE_GAPS.tsv`;
- `15_SAFETY_SIGNALS.tsv`;
- `16_COMMUNITY_BELIEFS.tsv`;
- `17_CONTRADICTIONS.tsv`;
- `18_DIALOGUE_FLOWS.tsv`;
- `MEDIA_RECOVERY_QUEUE.tsv`;
- schema и validation reports.

Не считать список гарантированным: сначала проверить фактические файлы и версии.

## 4. Входной шлюз качества

Разработка интеграции начинается только если:

- PASS 004 завершен;
- структурная валидация зеленая;
- семантическая валидация зеленая;
- нет malformed TSV;
- нет invalid `source_id`;
- каждое фактологическое значение имеет source locator;
- потерянные записи восстановлены либо поштучно помещены в quarantine;
- создан backup и hashes.

Если корпус частичный, разрешено строить архитектуру и загружать staging. Запрещено объявлять глобальную частоту, топы и окончательные бандлы до 100% покрытия.

## 5. Главный pipeline

```text
RAW
  ↓ Claude extraction
Validated Distillate
  ↓ Codex importer
Postgres Staging
  ↓ normalization/dedupe/linkage
Review Queue / Google Sheets
  ↓ medical/business/owner approval
Published Structured Layer
  ↓ 15-minute sync
Runtime Postgres
  ↓
n8n bot / website / coach / analytics
```

Ни одна строка не проходит из RAW/distillate прямо в пользовательский ответ.

## 6. Статусы публикации

Единые состояния:

- `blocked_raw`;
- `candidate`;
- `review_required`;
- `reviewed`;
- `owner_approved`;
- `published`;
- `rejected`;
- `superseded`.

Правила:

- новая фактологическая запись начинается с `blocked_raw`;
- технический вопрос/алиас/smoke может перейти в `candidate` автоматически;
- медицинские claims не переходят выше `review_required` без врача;
- продающий текст не переходит в `published` без Виктора;
- отзыв не публикуется без проверки источника, согласия и статуса маркетинга;
- rejected не удаляется, а остается в audit;
- изменение опубликованной owner-locked записи создает новую версию.

## 7. Общая модель provenance

Каждая запись обязана хранить:

- `record_id`;
- `tenant_id`;
- `source_id`;
- `source_locator`;
- `raw_quote`;
- `source_type`;
- `authority_level`;
- `extraction_pass`;
- `source_hash`;
- `created_at`;
- `updated_at`;
- `publication_status`;
- `medical_review_state`;
- `business_review_state`;
- `owner_state`;
- `content_hash`;
- `supersedes_record_id`.

WHIEDA является первым tenant. Архитектура не должна блокировать подключение NSP или другого клиента с другим корпусом.

## 8. Staging

Создать отдельную staging-схему или таблицы с явным префиксом:

`advisor_distillate_staging_*`

Требования:

- staging не читается пользовательским workflow;
- idempotent import;
- manifest каждого импорта;
- source file hash;
- row hash;
- импорт повторного файла не создает дубли;
- измененная строка создает новую версию или update с audit;
- удаление строки из файла не удаляет опубликованную запись;
- quarantine хранится отдельно;
- любой import можно откатить по `import_run_id`.

Нужны таблицы:

- `advisor_distillate_import_runs`;
- `advisor_distillate_records`;
- `advisor_distillate_quarantine`;
- `advisor_distillate_links`;
- `advisor_distillate_review_queue`.

Допустимо создать специализированные staging-таблицы, если это упрощает строгие constraints.

## 9. Импортер

Создать один автономный runner:

`n8n/current/run_whieda_distillate_import.py`

Пример:

```text
python run_whieda_distillate_import.py \
  --source-dir "<distillate path>" \
  --validate \
  --stage \
  --build-candidates \
  --no-publish
```

Режимы:

- `--validate`;
- `--stage`;
- `--build-candidates`;
- `--export-review`;
- `--publish-approved`;
- `--rollback-run <id>`;
- `--report`.

По умолчанию публикация выключена.

Runner:

- сохраняет checkpoint;
- продолжает после сбоя;
- пишет machine-readable report;
- возвращает ненулевой exit code при ошибке;
- не продолжает публикацию при красном P0;
- не требует ручной правки TSV.

## 10. Google Sheets как review/editor

Проверить существующие вкладки и переиспользовать их.

Ожидаемые логические слои:

- `Canonical_Questions`;
- `Product_Aliases`;
- `Testimonials`;
- существующая `Solution_Bundles`;
- `Objections`;
- существующая `Problem_Solution_Matrix`;
- `Content_Candidates`;
- `Training_Cases`;
- `Medical_Review`;
- `Safety_Signals`.

Не создавать новую вкладку, если существующая хранит ту же сущность.

Review export:

- выгружает только кандидатов;
- не перезаписывает owner-locked строки;
- сохраняет stable id;
- содержит source link/locator;
- показывает точный review state;
- отделяет исходную цитату от предлагаемого текста;
- не скрывает negative outcomes.

Google Sheets остается master утвержденных текстов. Staging является техническим буфером и историей происхождения.

## 11. Слой 1: реальные вопросы

### Цель

Расширить SQL-бота реальными формулировками и снизить fallback.

### Использовать

- `01_QUESTIONS`;
- `02_ALIASES`;
- `04_CLARIFICATION_RULES`;
- `13_SMOKE_CASES`;
- `18_DIALOGUE_FLOWS`;
- утвержденные части `03_SQL_FAQ_CANDIDATES`.

### Результат

- новые canonical questions;
- варианты формулировок;
- безопасные алиасы;
- fuzzy examples;
- clarification rules;
- follow-up sequences;
- multi-intent sequences;
- новые smoke cases;
- gap mapping.

### Автоматически разрешено

- новый тестовый input;
- безопасный alias;
- связь вопроса с уже существующим утвержденным answer key;
- clarification wording без медицинского содержания;
- normalization.

### Запрещено автоматически

- создавать новый медицинский ответ;
- менять карточку;
- менять ограничения;
- добавлять дозировку;
- публиковать неподтвержденный FAQ.

### Критерий

- каждый вопрос проходит intent/entity/slot matching;
- неизвестное уточняется конкретно;
- фактические ответы используют существующий approved layer;
- новая regression suite зеленая;
- fallback rate измеряется до/после.

## 12. Слой 2: отзывы

### Цель

Создать единый слой реального опыта для сайта и бота без превращения отзывов в доказательства.

### Использовать

- `07_TESTIMONIALS`;
- `06_USAGE_PATTERNS`;
- `12_RESOURCE_LINKS`;
- `15_SAFETY_SIGNALS`;
- `MEDIA_RECOVERY_QUEUE`;
- связанные RAW locators.

### Сущность Testimonial

- `testimonial_id`;
- `tenant_id`;
- product ids;
- исходная ситуация;
- способ применения;
- длительность;
- результат словами автора;
- нейтральное summary;
- positive/neutral/negative;
- media type;
- media URL/id;
- transcript state;
- author/public name;
- consent state;
- medical review;
- marketing review;
- publication scope;
- source/provenance;
- status.

### Разделение

- положительный отзыв;
- нейтральный опыт;
- отсутствие результата;
- негативная реакция;
- serious safety signal;
- непроверяемый пересказ;
- видео без транскрипции.

Негативный результат никогда не превращать в положительную карточку.

### Бот

Поддержать:

- «дай отзывы об активаторе»;
- «есть опыт по суставам?»;
- «покажи видеоотзывы»;
- follow-up по выбранному товару;
- максимум 3 результата в первом ответе;
- ссылка/видео только из утвержденного ресурса;
- короткая отметка: опыт человека, результат не гарантируется.

### Сайт

На карточке товара:

- 3-6 утвержденных отзывов;
- фильтр по ситуации/формату;
- ссылка на полный источник или видео;
- без медицинского вывода от сайта;
- не публиковать персональные данные без согласия;
- отдельный negative/safety материал не смешивать с продающей витриной, но не удалять из внутренней базы.

### Критерий

- 100% опубликованных отзывов имеют source и consent;
- бот и сайт используют одну approved сущность;
- медиа не теряется;
- duplicate testimonial не показывается дважды;
- unsafe review не попадает в marketing query.

## 13. Слой 3: бандлы

### Цель

Собрать понятные сочетания продуктов под цель человека для сайта, бота и стартовой корзины.

### Использовать

- `09_BUNDLE_CANDIDATES`;
- `08_PROBLEM_SOLUTION_CANDIDATES`;
- usage patterns;
- testimonials;
- current product cards/prices/PV;
- врачебные и бизнес-апрувы.

### Bundle

- `bundle_id`;
- название;
- audience;
- goal/problem;
- trigger questions;
- primary product;
- additional products;
- роль каждого товара;
- logic source;
- порядок;
- duration, только если утверждена;
- restrictions;
- price/PV computed runtime;
- applicable promotions;
- testimonial links;
- media links;
- medical state;
- business state;
- owner state;
- status.

### Правила

- цены/PV не копировать в текст;
- считать из Products_Prices;
- медицинский бандл не публиковать без врача;
- бизнес/стартовый бандл не требует медицинского апрува, если не содержит health claims;
- не выдавать набор как лечение диагноза;
- отделять «сообщество часто сочетает» от утвержденной рекомендации;
- объяснять роль каждого товара без повторов.

### Выдача

Первый ответ:

- цель;
- 2-4 товара;
- короткая логика;
- итоговая цена/PV;
- одно ограничение;
- CTA: подробнее/отзывы/материалы/другая сумма.

### Критерий

- минимум 5 сильных утвержденных демонстрационных бандлов;
- цена/PV всегда актуальны;
- follow-up сохраняет bundle context;
- smoke покрывает цену, замену товара, исключение товара и материалы.

## 14. Слой 4: возражения

### Цель

Дать партнеру быстрые естественные ответы и подготовить основу тренажера.

### Использовать

- `05_OBJECTIONS`;
- marketing language;
- dialogue flows;
- реальные удачные/неудачные ответы;
- business review.

### Objection

- `objection_id`;
- category;
- raw phrase;
- normalized objection;
- hidden concern;
- clarifying question;
- short response;
- expanded response;
- prohibited response;
- proof/material links;
- product/business context;
- tone;
- business approval;
- source.

### Бот

- распознает возражение;
- не вываливает длинный скрипт;
- сначала уточняет скрытую причину при неоднозначности;
- дает короткий ответ;
- предлагает материал или разбор;
- сохраняет контекст.

### Критерий

- топ-20 возражений;
- 3-10 реальных формулировок каждого;
- ответы owner-approved;
- нет гарантий дохода и медицинских обещаний;
- отдельный objection smoke.

## 15. Слой 5: Problem → Solution

### Цель

Направлять человека от его языка к подходящей категории товаров и материалам.

### Использовать

- `08_PROBLEM_SOLUTION_CANDIDATES`;
- questions;
- usage;
- bundles;
- testimonials;
- safety/contradictions;
- medical review.

### Правило

Это навигация по базе, а не диагностика.

Система:

1. распознает проблему;
2. задает 1-2 уточнения;
3. проверяет hard safety;
4. показывает утвержденные направления;
5. предлагает товары/бандлы/материалы;
6. отдельно дает отзывы;
7. не обещает лечение.

### Публикация

- все health mapping проходят врача;
- спорные community beliefs не используются как механизм;
- negative safety signals влияют на ограничения и тесты;
- при отсутствии approved mapping бот не фантазирует.

### Критерий

- минимум 10 утвержденных problem routes;
- каждое направление имеет source и review;
- опасные состояния переключают safety route;
- нет свободного LLM-назначения.

## 16. Слой 6: контент и SEO

### Цель

Превратить реальные вопросы и истории в очередь уникального контента.

### Использовать

- question clusters;
- objections;
- testimonials;
- marketing language;
- resource links;
- gaps;
- product/problem frequency.

### Content Candidate

- `content_id`;
- search/user question;
- search intent;
- audience;
- product;
- proposed title;
- outline;
- required approved facts;
- testimonial candidates;
- media;
- CTA;
- source cluster;
- uniqueness requirement;
- compliance review;
- owner state;
- target site/domain;
- canonical owner.

### Выходы

- FAQ;
- статья;
- карточка товара;
- страница проблемы;
- сценарий короткого видео;
- сценарий интервью;
- Telegram-пост;
- письмо/рассылка;
- страница специалиста WWC.

### Правила

- автоматически создавать только brief/draft;
- не публиковать без owner approval;
- общий контент имеет одного SEO-владельца;
- tenant-копии общего текста получают `noindex,follow`;
- уникальные страницы специалистов могут индексироваться;
- цитаты и отзывы сохраняют provenance.

### Критерий

- топ-50 content briefs;
- приоритет основан на реальной частоте и ценности;
- нет дублирующих страниц;
- каждый brief связан с approved facts или gaps.

## 17. Слой 7: обучение и коуч

### Цель

Сделать обучение на реальных ситуациях, а не на искусственных лекциях.

### Использовать

- dialogue flows;
- objections;
- questions;
- bad/good answers;
- contradictions;
- safety cases;
- approved business language.

### Training Case

- `training_id`;
- role/level;
- situation;
- customer message;
- expected clarification;
- acceptable answer structure;
- required elements;
- forbidden elements;
- reference answer;
- scoring rubric;
- source;
- difficulty;
- approval.

### Функции

- ролевая тренировка;
- проверка ответа партнера;
- разбор ошибки;
- следующая попытка;
- короткое домашнее задание;
- прогресс пользователя.

### Критерий

- минимум 30 утвержденных кейсов;
- уровни beginner/partner/leader;
- scoring не оценивает стиль как медицинскую истину;
- dangerous advice блокируется;
- прогресс совместим с текущим coach context.

## 18. Внутренние safety-слои

Не выводить пользователю напрямую:

- raw medical queue;
- safety signals;
- community beliefs;
- contradictions;
- rejected claims.

Использовать их для:

- hard safety rules;
- smoke tests;
- review queue;
- gap report;
- обучения;
- контроля опубликованного контента;
- мониторинга повторяющихся рисков.

Паттерн `обострение = хороший знак` не удалять из истории и не объявлять автоматически ложным. Он хранится как belief/risk pattern и не используется ботом как универсальное разрешение продолжать применение.

## 19. Автономный цикл разработки

Для каждого слоя:

1. Снять baseline.
2. Проверить входные записи.
3. Импортировать staging.
4. Нормализовать и дедуплицировать.
5. Построить candidates.
6. Экспортировать review.
7. Автоматически применить только safe technical changes.
8. Опубликовать только approved rows.
9. Запустить tests.
10. Проверить live.
11. Зафиксировать метрики.
12. Обновить этот документ.
13. Перейти к следующему слою без ожидания подтверждения, если не требуется owner/medical decision.

Не останавливать работу из-за отсутствия апрува:

- подготовить все candidates;
- продолжить техническую часть следующего слоя;
- сложить blocked decisions в одну очередь.

## 20. Тесты

### Data

- schema;
- enums;
- provenance;
- duplicates;
- referential integrity;
- tenant isolation;
- publication firewall;
- owner lock;
- quarantine.

### Runtime

- SQL route;
- latency;
- context;
- multi-intent;
- media;
- no Dify for structured request;
- approved-only selection;
- negative/safety exclusion from marketing.

### Website

- correct product/testimonial linkage;
- consent;
- no duplicate cards;
- canonical;
- noindex tenant copies;
- referral attribution.

### Regression

После каждого слоя:

- P0;
- product regression;
- functional smoke;
- review smoke;
- health check;
- соответствующий новый suite.

## 21. Метрики

Считать:

- число входных записей;
- unique после dedupe;
- candidates;
- approved;
- published;
- rejected;
- quarantine;
- покрытие источников;
- fallback rate;
- intent accuracy;
- entity accuracy;
- p50/p95 latency;
- долю ответов из SQL;
- количество опубликованных отзывов;
- количество готовых бандлов;
- число закрытых gaps;
- число найденных safety conflicts.

Число записей не является самоцелью. Главные показатели — качество ответа, трассируемость и снижение ручной работы лидера.

## 22. Порядок релизов

### Release 0. Pipeline

- validator;
- staging;
- manifest;
- review export;
- publication firewall;
- rollback.

### Release 1. Questions

- questions;
- aliases;
- clarification;
- dialogue flows;
- smoke.

### Release 2. Testimonials

- testimonial storage;
- media linkage;
- bot response;
- product-page component.

### Release 3. Bundles

- bundle entity;
- price/PV calculation;
- bot/site output.

### Release 4. Objections

- objection cards;
- bot route;
- training candidates.

### Release 5. Problem/Solution

- reviewed matrix;
- clarifications;
- safety routing.

### Release 6. Content/SEO

- briefs;
- queues;
- site integration.

### Release 7. Training

- cases;
- scoring;
- coach integration.

## 23. Stop conditions

Остановить публикацию, но не всю подготовительную работу, если:

- красный P0;
- invalid provenance;
- tenant leak;
- owner-locked diff;
- unsafe row попадает в marketing query;
- отзыв без source/consent;
- цена/PV расходятся с Products_Prices;
- medical claim публикуется без review;
- rollback не готов.

## 24. Definition of Done

Задача полностью готова, когда:

- дистиллят импортируется идемпотентно;
- staging изолирован от runtime;
- есть строгий publication firewall;
- каждый опубликованный факт трассируется до RAW;
- вопросы расширили SQL и снизили fallback;
- отзывы работают в боте и на сайте;
- утвержденные бандлы считают актуальную цену/PV;
- возражения отвечают живым owner-approved языком;
- Problem/Solution работает только по reviewed mappings;
- content briefs создаются из реального спроса;
- обучение использует реальные диалоги;
- safety/contradictions влияют на тесты;
- все regression suites зеленые;
- multi-tenant constraints соблюдены;
- есть release reports и rollback.

## 25. Артефакты

Оставить:

- importer;
- validator integration;
- SQL migrations;
- review exports;
- Google Sheets mapping;
- новые smoke suites;
- site components;
- release reports каждого слоя;
- metrics report;
- rollback;
- обновленные Библию, file map и этот документ.

## 26. Статус

- [ ] Release 0. Pipeline
- [ ] Release 1. Questions
- [ ] Release 2. Testimonials
- [ ] Release 3. Bundles
- [ ] Release 4. Objections
- [ ] Release 5. Problem/Solution
- [ ] Release 6. Content/SEO
- [ ] Release 7. Training
