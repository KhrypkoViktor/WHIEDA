# Отчёт: блок A инженерного пакета

Дата: 2026-09-05. Задача `engineering-order-20260905`.
Статус: готово к приёмке лидом. Deploy не выполнялся, production не изменялся.

## База и границы

| | Значение |
|---|---|
| Корневой repo | `D:/Projects/WHIEDA`, `wip/consolidation-20260831` |
| Repo сайта | `D:/Projects/WHIEDA/03_Website/wwc-best` (отдельный Git) |
| Task-worktree | `D:/Projects/_worktrees/wwc-engineering-order-20260905` |
| Ветка | `chore/engineering-order-20260905` |
| Base SHA | `bb603a97b19af2f9e6f647d202c0eb1999bab077` |

База выбрана явно: `origin/master`, содержит production `9044e52` как предок
(5 commits между), не содержит скоростного среза, который ТЗ велит не
подмешивать. **Интеграционную базу блок A не назначает.**

## Что изменено

Корневой repo, всё новое:

| Путь | Что это |
|---|---|
| `tools/git-snapshot.mjs` | генератор Git-снимка и реестра worktree |
| `tools/doc-check/check-docs.mjs` | проверка документов и ссылок по манифесту |
| `tools/doc-check/manifest.json` | явный список активных документов и известных отсутствующих, с причинами |
| `tools/doc-check/fixtures/` | четыре фикстуры: ok, missing-active, historical, missing-doc |
| `tools/doc-check/test-doc-check.mjs` | пять тестов |
| `tools/README.md` | точные команды запуска |
| `PROCESS/engineering-order-20260905/GIT_SNAPSHOT.md` | сгенерированный снимок |
| `PROCESS/engineering-order-20260905/ENVIRONMENTS.md` | карта сред с evidence |
| `PROCESS/engineering-order-20260905/SOURCE_MAP.md` | карта источников и конфликты |
| `PROCESS/engineering-order-20260905/TASK_TEMPLATE.md` | шаблон задачи |
| `PROCESS/engineering-order-20260905/STATE.md` | обновлён |

Task-worktree сайта создан и **пуст по изменениям** — он для блоков B и C.
Runtime-код, цены, фото, стили, серверы, чужие ветки не тронуты. Коммитов не
делал: пакет отдаётся на приёмку как рабочее дерево.

## Команды и результаты

```bash
node tools/doc-check/check-docs.mjs
# exit 1 — Проверено документов: 11. Ошибок: 2. Предупреждений: 32.

node --test tools/doc-check/test-doc-check.mjs
# tests 5, pass 5, fail 0

node tools/git-snapshot.mjs --repo D:/Projects/WHIEDA \
  --repo D:/Projects/WHIEDA/03_Website/wwc-best --base bb603a9...
# exit 0, снимок записан
```

### Негативные фикстуры — проверка действительно ловит

| Фикстура | Заложенный дефект | Ожидание | Факт |
|---|---|---|---|
| `ok` | нет | exit 0, 0 предупреждений | совпало |
| `missing-active` | ссылка на несуществующий файл, не объявленный историческим | exit 1 | совпало |
| `historical` | та же ссылка, но объявлена с причиной | exit 0 + WARN | совпало |
| `missing-doc` | сам обязательный документ отсутствует | exit 1 | совпало |

## Две реальные ошибки, которые нашла проверка

Это не дефекты инструмента — это ровно тот разрыв, который вы назвали:
правила записаны в каноне, а файлы, на которые они ссылаются, есть только на
одной ветке сайта.

```
ОШИБКА 00_READ_FIRST_WHIEDA_CANON.md -> 03_Website/wwc-best/docs/SEO_INDEXING_INVARIANTS.md
ОШИБКА 00_READ_FIRST_WHIEDA_CANON.md -> tests/unit/runtime-twin-sync.test.mjs
```

Оба файла существуют на `fix/staging-referral-isolation`, но отсутствуют в
checkout `03_Website/wwc-best` (`fix/fedorov-runtime-context` @ `e1cd939`).
Там же нет `check:seo` в `package.json`, хотя канон утверждает, что команда
входит в `release:verify`.

**Намеренно не «чинил» это ни добавлением в `knownMissing`, ни копированием
файлов.** Перенос правил в остальные рабочие копии — согласованный Git-пакет и
решение лида о базе. Пока переноса нет, проверка честно красная, и это
правильное её поведение.

32 предупреждения — известные отсутствующие документы из списка чтения канона
(девять ненайденных ТЗ, архивированный `WHIEDA_LIVE_STATUS.md`, внешний
Obsidian-pipeline). Каждое с причиной в манифесте; ни один файл из архива не
возвращался.

## Что проверено фактически

- Существование всех файлов карты источников на базе `bb603a9` — проверено
  поштучно.
- `9044e52` — предок `bb603a9`, между ними 5 commits.
- Реестр worktree: 24 в корневом repo, 20 в repo сайта, с dirty-счётчиком и
  числом уникальных commits от базы.
- Начальный баг генератора: `git rev-parse` возвращает полный 40-символьный SHA
  как есть, даже если объекта нет, — из-за этого база «находилась» в корневом
  repo, где её нет. Исправлено на `rev-parse --verify`; теперь корневой repo
  честно пишет «база не найдена в этом repo».

## Что НЕ проверено

- Production, staging, SSH, Sheets, БД — в этом блоке не трогались.
  Значения в `ENVIRONMENTS.md` с пометкой `verified (perf-срез)` получены
  раньше, в отдельном скоростном срезе того же дня; метод и дата указаны рядом.
- Соответствие прод-docroot commit `9044e52` — только по маркеру `health.json`,
  то есть `reported`. Хеши дерева не сверялись.
- Маршруты `/api/v1/*` — по устаревшей копии конфига в репозитории. Живой
  конфиг отличается: у `theme-access` там нет `location`, а маршрут отвечает.

Шесть read-only проверок для лида перечислены в конце `ENVIRONMENTS.md`.

## Чужие срезы

В repo сайта появился `review/astra-block-c-20260905`
(`C:/Users/srs/Documents/WHIEDA/.tmp/wwc-block-c-20260905`, 3 уникальных
commit) — другой исполнитель по ревизии. Не трогал.

## Следующий шаг

Приёмка блока A. Отдельно нужны два решения лида: интеграционная база и способ
переноса правил в остальные рабочие копии. После этого — блок B.

Production changed: no.
