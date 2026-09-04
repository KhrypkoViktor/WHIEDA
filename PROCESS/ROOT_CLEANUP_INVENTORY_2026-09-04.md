# Генеральная уборка корня WHIEDA — инвентаризация и предложение

Дата: 2026-09-04
Статус: черновик на утверждение владельца. Ничего не перемещено, только предложение.
Автор: Claude (в этой сессии), read-only разведка.

## 0. Три критических находки (не решаю сам, нужен ответ)

1. **Канон ссылается на ~10 документов, которых физически нет в репозитории**:
   `WHIEDA_PRODUCT_DIRECTION.md`, `WHIEDA_HOW_WE_WRITE_AND_ADVISE.md`,
   `WHIEDA_MARKETING_VOICE_AND_CONTENT_SYSTEM_V1.md`,
   `WHIEDA_PLATFORM_MASTER_PROGRAM_SPEC_V1_2026-08-09.md`,
   `WHIEDA_CURRENT_BUILD_PLAN_SITE_TELEGRAM_ONBOARDING_V1_2026-08-07.md`,
   `WHIEDA_SQL_ADVISOR_PARITY_DEVELOPER_PLAN_V1_2026-08-03.md`, и все TZ 8a–8j
   кроме тех, что перечислены ниже как найденные. Это не про уборку — это про то,
   что канон сейчас нельзя выполнить буквально: агент, который честно идёт по
   списку чтения, упрётся в несуществующие файлы. Возможные причины: файлы
   остались в `backup/consolidation-raw-20260831` и не перенесены, либо канон
   писался с опережением. Нужно твое решение: искать их в raw-snapshot и
   переносить, или вычеркнуть из канона как ещё не написанные.
2. **Два архива одновременно**: старый `99_Archive/` (уже с чёткой структурой:
   `Superseded_Root_Docs`, `Superseded_File_Maps`, датированные снапшоты,
   354 МБ) и новый `ARCHIVE/` (создан сегодня по новому канону). Предлагаю: новые
   кандидаты кладём в `ARCHIVE/`, `99_Archive/` не трогаем и не сливаем — это
   отдельная задача, не связанная с сегодняшней уборкой.
3. **`00_READ_FIRST_WHIEDA_DEVELOPMENT_BIBLE_V1_2026-07-14.md`** называет себя
   «главный входной документ разработки» — ровно то же самое говорит про себя
   новый `00_READ_FIRST_WHIEDA_CANON.md`. Канон нигде не упоминает Библию в
   порядке чтения. Похоже, Канон заменил Библию, но это нигде не зафиксировано
   явно. Нужно твоё подтверждение перед архивацией — это не рядовой файл.

## 1. Корневые файлы — предложенная классификация

Легенда: **KEEP** — остаётся в корне как есть. **PROCESS** — в работе/ТЗ,
переносим в `PROCESS/`. **ARCHIVE** — предлагаю в `ARCHIVE/` (сделано,
заменено или устарело). **REVIEW** — не уверен, нужен твой ответ.

Дата ниже — дата **внутри документа**, не mtime файла (mtime у половины файлов
сброшен на 2026-08-31 массовым restore и не отражает реальный возраст).

| Файл | Дата | Суть | Статус | Почему |
|---|---|---|---|---|
| `00_READ_FIRST_WHIEDA_CANON.md` | 2026-09-04 | Главный канон, все решения | KEEP | текущий вход №1 |
| `00_READ_FIRST_WHIEDA_DEVELOPMENT_BIBLE_V1` | 2026-07-14/28 | Старый «главный документ» | REVIEW→ARCHIVE | см. находку №3 |
| `CORE_ADVISOR_PARITY_LAB_V2_REPORT.md` | 2026-08-09 | Отчёт лабы, завершён | ARCHIVE | законченный отчёт, не в списке канона |
| `CORE_CONVERSATION_RELIABILITY_LAB_REPORT.md` | 2026-08-10 | Отчёт, PASS 39/39 | ARCHIVE | завершено |
| `CORE_LOCAL_BUILD_REPORT.md` + `WHIEDA_LOCAL_BUILD_BLOCKS_V1.json` | 2026-08-07 | Отчёт + манифест сборки | ARCHIVE | пара, завершено |
| `LOCAL_CORE_E2E_LAB_REPORT.md` | 2026-08-08 | Финальный отчёт лабы | ARCHIVE | завершено |
| `LOCAL_CORE_LAB_REPORT.md` | 2026-08-03 | Отчёт лабы | ARCHIVE | завершено |
| `LOCAL_STAGING_PROOF_REPORT.md` | 2026-08-07 | Отчёт pytest/staging proof | ARCHIVE | завершено |
| `NO_BLIND_ZONE_LOCAL_REPORT.md` | 2026-08-09 | Отчёт, corpus PASS | ARCHIVE | завершено |
| `NO_BLIND_ZONE_OPERATOR_SAMPLE.md` | — | Синтетический пример для владельца | ARCHIVE | одноразовая выборка |
| `PARTNER_RUNTIME_RECONCILIATION_V2_LOCAL_REPORT.md` | 2026-08-10 | Отчёт, бага исправлена | ARCHIVE | завершено |
| `PLATFORM_NEW_TENANT_PLAYBOOK_V1_2026-08-20.md` | 2026-08-20 | Заявлен как «действующий канон онбординга tenant» | REVIEW | сам себя называет активным, но канон его не цитирует — актуален ли ещё? |
| `WHIEDA_ACCESS_MAP_PRIVATE_2026-07-07.md` | 2026-07-07 | Приватные доступы | KEEP | канон п.13 |
| `WHIEDA_COMPLIANCE_SELLING_LANGUAGE_ACCESS_MONETIZATION_RB_2026-07-14.md` | 2026-07-14 | Compliance/язык/монетизация | KEEP | канон п.11 |
| `WHIEDA_COMPOSER_CONVERSATION_RELIABILITY_LAB_TASK_V1` | 2026-08-10 | Задача Composer | ARCHIVE | результат есть выше (report PASS) |
| `WHIEDA_COMPOSER_OPERATOR_GAP_CONTROL_PLANE_TASK_V1` | 2026-08-10 | Задача Composer, gap-слой | REVIEW | результата/отчёта не нашёл — сделано или брошено? |
| `WHIEDA_COMPOSER_STRUCTURED_SYNC_SAFETY_P0_TASK_V1` | 2026-08-10 | Задача Composer, sync safety | ARCHIVE | есть парный review-fixes отчёт (см. ниже) |
| `WHIEDA_CORE_ADVISOR_REGRESSION_REPAIR_R1_2026-08-21.md` | 2026-08-21 | Repair-задача, привязана к коммиту | ARCHIVE | точечный фикс к старой ветке |
| `WHIEDA_CORE_BINDING_RELEASE_SLICE_TZ_V1` (Gate B) | 2026-08-20 | Core Gate B | ARCHIVE (пакет из 7) | см. ниже |
| `WHIEDA_CORE_DURABLE_TELEGRAM_INBOX_TZ_V1` (Gate C) | 2026-08-20 | Core Gate C | ARCHIVE (пакет) | ↑ |
| `WHIEDA_CORE_TENANT_ADVISOR_DATA_PLANE_TZ_V1` (Gate D) | 2026-08-21 | Core Gate D | ARCHIVE (пакет) | ↑ |
| `WHIEDA_CORE_TENANT_RELEASE_PACKAGE_FIREWALL_TZ_V1` (Gate E) | 2026-08-21 | Core Gate E | ARCHIVE (пакет) | ↑ |
| `WHIEDA_CORE_TENANT_CURRENCY_PRICE_PLANE_TZ_V1` (Gate E1) | 2026-08-21 | Core Gate E1 repair | ARCHIVE (пакет) | ↑ |
| `WHIEDA_CORE_CANARY_RELEASE_INTEGRATION_TZ_V1` (Gate F) | 2026-08-21 | Core Gate F | ARCHIVE (пакет) | ↑ |
| `WHIEDA_CORE_TENANT_MEDIA_DELIVERY_TZ_V1` (Gate H) | 2026-08-21 | Core Gate H | ARCHIVE (пакет) | ↑ — все 7 Gate B–H, вероятно пройдены: канон п.8h/8i цитирует уже Gate после J/M, значит B–H позади |
| `WHIEDA_CURSOR_INVENTORY_AND_CORPUS_TASK_V1_2026-08-09.md` | 2026-08-09 | Read-only инвентаризация (прошлая) | ARCHIVE | предок сегодняшней GLM-задачи, роль исчерпана |
| `WHIEDA_CURSOR_NO_BLIND_ZONE_AND_GAP_LOOP_TASK_V1_2026-08-09.md` | 2026-08-09 | Задача Cursor | ARCHIVE | результат есть (NO_BLIND_ZONE report выше) |
| `WHIEDA_DISTILLATE_TO_PRODUCT_LAYERS_BUILD_SPEC_V1_2026-07-26.md` | 2026-07-26 | Утверждённое ТЗ дистиллята | REVIEW | старый файл-карта считал его «держать в корне»; новый канон не упоминает — всё ещё исполняется? |
| `WHIEDA_DROVOSEK_MASTER_INTEGRITY_AND_BACKUP_TASK_V1_2026-08-10.md` | 2026-08-10 | Задача: сделать мастер-реестр надёжным | ARCHIVE | сегодня это уже реализовано в `WORK/data-contracts/` |
| `WHIEDA_DROVOSEK_STRUCTURED_SYNC_SAFETY_P0_REVIEW_FIXES_V1_2026-08-10.md` | 2026-08-10 | Фиксы к P0 sync | ARCHIVE | парный к Composer-задаче выше |
| `WHIEDA_FILE_MAP_CURRENT.md` | 2026-07-26 | Карта проекта | **REWRITE** | стиль хороший (см. твой ответ), но описывает схему до WORK/PROCESS/ARCHIVE — переписываю как часть уборки, не архивирую |
| `WHIEDA_GROK_BUNDLE_SOURCE_WORKBENCH_TZ_V1_2026-08-30.md` | 2026-08-30 | ТЗ: разбор RAW в 28 наборов | KEEP | свежее, судя по тексту — в работе |
| `WHIEDA_IDEAS_BACKLOG.md` | живой | Backlog идей | KEEP | живой документ, не снимок |
| `WHIEDA_LIVE_STATUS.md` | обновлён 2026-08-02 | Факты live-контура | KEEP, но **устарел на месяц** | канон п.10 требует «только подтверждённые факты» — сейчас это не так; предлагаю обновить, не архивировать |
| `WHIEDA_LOCAL_REVIEW_GUIDE.md` | без даты (guide) | Как гонять Core-тесты локально | KEEP | вечнозелёная инструкция |
| `WHIEDA_MASTER_RUNTIME_INTEGRITY_REVIEW_FIX_LOCAL_REPORT.md` | 2026-08-11 | Отчёт по фиксу | ARCHIVE | завершено |
| `WHIEDA_SITE_REFERRAL_MVP_SPEC_V1_2026-07-26.md` | 2026-07-26 | Referral MVP на старом домене `whieda.sysarch.pro` | ARCHIVE | домен-мигрант, заменено WWC_SITE_BOT_LEADS на следующий день + канон §13 |
| `WHIEDA_SQL_FAST_ANSWERS_TWO_WEEK_BUILD_PLAN_2026-07-25.md` | 2026-07-25 | SQL-план на 2 недели | KEEP (как справочник) | канон сам пишет: «остаётся справочником требований, не задаёт очередность» |
| `WHIEDA_STRUCTURED_GROWTH_MODULES_SPEC_V1_2026-07-26.md` | 2026-07-26 | Акции/корзина/рассылки, ТЗ | REVIEW | статус после SQL-плана неясен в новом каноне |
| `WHIEDA_TECH_BACKLOG.md` | 2026-07-28 | Технический backlog | KEEP | живой список |
| `WHIEDA_TELEGRAM_SERVICE_INTENT_AND_DUPLICATE_LAB_LOCAL_REPORT.md` | 2026-08-11 | Отчёт | ARCHIVE | завершено |
| `WHIEDA_WORKSPACE_CONSOLIDATION_2026-08-31.md` | 2026-08-31 | Карта разбора snapshot | KEEP | канон п.6a, обязателен пока не закрыт |
| `WHIEDA_WWC_SITE_BOT_LEADS_MULTI_REF_SPEC_V1_2026-07-27.md` | 2026-07-27 | ТЗ сайта/заявок/ref | KEEP | канон §"WWC, ref и заявки" явно цитирует это ТЗ как исполняемое |
| `WWC_PLATFORM_CLUB_PROMPT_CONTEXT_V1_2026-09-04.md` | 2026-09-04 | Контекст для промптов агентов | KEEP | сегодняшний |
| `WWC_SITE_MATERIALS_INDEX.md` | 2026-07-29 | Указатель на Obsidian-материалы сайта | REVIEW | нужно подтвердить, что путь на Obsidian ещё верный |
| `WWC_SUBDOMAIN_RUNTIME_AND_WILDCARD_SSL_TZ_V1_2026-09-01.md` | 2026-09-01 | ТЗ поддоменов/SSL | KEEP | свежее, ещё не закоммичено |

Итог по корню: из ~46 файлов предлагаю **KEEP ~16**, **ARCHIVE ~22** (из них 7 —
пачка Core Gate B–H), **REWRITE 1** (file map), **REVIEW ~7** (нужен твой ответ).

## 2. Легаси-папки — вердикт на уровне папки (без разбора имён кода внутри)

| Папка | Что внутри (кратко) | Предложение |
|---|---|---|
| `01_Context/` | Handoff (июль), review-loop заметки (июль), runtime-статусы (июль), `Completed_Work/2026-08_Recovery_and_Labs` | Всё июльское и «Completed_Work» → `ARCHIVE/`. Актуального контента для сентября не нашёл. |
| `02_Demo/` | Один runbook демо от 2026-07-12 | → `ARCHIVE/` (старый демо-сценарий, не подтверждён как текущий) |
| `03_Website/` (корень папки, не `wwc-best/`) | 2 файла: публичная контент-стратегия (канон п.12, живая) и план разработки сайта от 2026-08-02 | Стратегию — **KEEP** (канон-референс). План разработки — **REVIEW**: возможно устарел на фоне WWC_SUBDOMAIN TZ. `wwc-best/` — рабочий код сайта, не трогаю. |
| `07_Utilities/` | 3 скрипта сборки корпуса от 2026-07-15, `local_utility_shortage_tracker` (node.js утилита, node_modules корректно в .gitignore) | Скрипты — **REVIEW по датам, июль = подозрительно старое**: если корпус уже собран и лежит в RAG/, скрипты разового запуска можно в ARCHIVE. Сам tracker — не трогаю, это отдельный маленький инструмент. |
| `99_Archive/` | Уже архив: `Superseded_Root_Docs`, `Superseded_File_Maps`, снапшоты июля, 354 МБ | **Не трогаю.** Уже сделано правильно, отдельная задача — не сегодня. |

## 3. Крупные рабочие деревья (backend, n8n, RAG, dify, postgres, qa, reviews,
   reports, assets, .tmp, .work-snapshots)

Не разбираю сам — по твоему решению это отдаём GLM. Хорошая новость: TЗ для
этого уже лежит в `PROCESS/GLM_PROJECT_AND_SHEETS_INVENTORY_TZ_2026-09-04.md`
(судя по дате — подготовлено сегодня же) и почти дословно повторяет то, что
описывал Terra в твоей переписке: read-only, path/git/branch/referenced_by/
status для каждого файла, отдельно deploy-скрипты, дубли по SHA, абсолютные
пути `D:\Projects`. Ничего переписывать не нужно — можно отправлять GLM как
есть. Единственное, чего там нет: явного запрета трогать `.git` внутри
`99_Archive/C_Documents_WHIEDA_2026-07-26/.git` (там вложенный репозиторий) —
стоит добавить строку в раздел «Запреты», чтобы GLM не пытался туда заходить.

## 4. Что делаю дальше

После твоего ответа по REVIEW-пунктам и находкам №1–3:

1. Правлю `PROCESS/GLM_PROJECT_AND_SHEETS_INVENTORY_TZ_2026-09-04.md` (одна
   строка про вложенный `.git`) — отдаёшь GLM сам.
2. Переношу подтверждённые ARCHIVE-файлы через `git mv` небольшими коммитами
   по группам (отчёты одним коммитом, Core Gate B–H другим, легаси-папки
   третьим), с записью в `ARCHIVE/README.md`: путь → причина → дата.
3. Переписываю `WHIEDA_FILE_MAP_CURRENT.md` под новую структуру
   (WORK/PROCESS/ARCHIVE + то, что реально осталось в корне).
4. Ничего не удаляю физически и не трогаю `99_Archive/`, `backend/`, `n8n/`,
   `RAG/`, `dify/`, `postgres/`, `qa/`.
