# WHIEDA: разбор сохранённого переезда

Дата: 2026-08-31  
Статус: рабочая карта для лида. Не является разрешением на deploy или merge.

## Что произошло

В корневом worktree одновременно накопились изменения нескольких независимых
программ. Они не должны попадать в одну ветку или один релиз.

Состояние сохранено локально в Git:

- чистая рабочая ветка: `wip/consolidation-20260831`;
- необработанный снимок всех содержательных изменений: `backup/consolidation-raw-20260831`;
- commit снимка: `f3b3ee9`.

Временная папка `.tmp/` и локальная цель Golden HTTP lab исключены из Git. Они
не удалялись с диска, но не являются исходным кодом или артефактами релиза.

## Правило извлечения

1. Не merge `backup/consolidation-raw-20260831` целиком и не push его как релиз.
2. Каждый следующий срез создаётся от чистой тематической базы.
3. В срез попадают только файлы одной поверхности и её тесты.
4. Перед переносом проверяются зависимости, затем запускаются целевые тесты.
5. Production, shared staging, n8n и SQL применяются только из отдельного
   принятого пакета, а не из backup-ветки.

## Очередь разбора

| Приоритет | Срез | Что извлекаем | Что не смешивать |
|---|---|---|---|
| P0 | Telegram/Core runtime | `backend/platform-api/app/telegram`, `app/advisor`, tenant binding, SQL и соответствующие тесты | сайт, n8n deploy-скрипты, QA-корпуса без runtime-задачи |
| P0 | NSP shared-staging canary | tenant package, preflight/apply/verify runner, media evidence | WHIEDA production и личные сайты |
| P1 | WHIEDA data quality | cards, bundles, product discovery, golden/rails QA | миграции, deploy и сайт |
| P1 | n8n control plane | sync safety, экспорт, диагностические и deploy helpers | Core feature-code и UI |
| P2 | WWC website/cabinet | `03_Website`, site staging, partner onboarding, кабинет | Telegram runtime и tenant import |
| P2 | Документы и архив | устаревшие отчёты, дубли ТЗ, file map | исполняемый код |

## Первое практическое действие

Критический путь на 2026-08-31: завершить controlled NSP shared-staging canary
из уже принятых Gate M/G2 пакетов, затем руками проверить Telegram-поверхность.
Это не требует переноса всего snapshot в основной worktree.

## Роли

- Terra/лид: выбор среза, интеграция, SQL, staging, deploy, реальные проверки.
- Младший разработчик: QA-корпуса, data inventory, fixtures, provenance,
  генераторы и документация в изолированной ветке.

