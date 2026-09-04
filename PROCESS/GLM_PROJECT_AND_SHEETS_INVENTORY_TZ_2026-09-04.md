# GLM: инвентаризация проекта и Google Sheet - строго read-only

## Цель

Составить карту активного, незавершенного и архивного. Ничего не переносить,
не удалять, не переименовывать и не создавать.

## Локальный проект

Для каждого файла/каталога вывести:

path | git_repo | branch | commit | dirty | referenced_by | status | reason

Допустимые статусы:

- WORK_candidate - подтверждено кодом, каноном и боем.
- PROCESS_candidate - ТЗ, отчет, аудит или незавершенная работа.
- ARCHIVE_candidate - заменено и не вызывается.
- unknown - недостаточно доказательств; обязательная эскалация.

Отдельно:

- все worktree;
- все deploy/repair/migrate скрипты;
- абсолютные пути D:\\Projects;
- дубли документов и отчетов по SHA;
- расхождения между главным checkout и активным staging worktree.

## Google Sheet

Таблица:
https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/edit

Для каждой вкладки:

title | gid | headers | populated_rows | likely_owner | referenced_by | duplicate_of | recommendation | evidence

Особо проверить Products_Prices и product_prices. Products_Prices с gid
1035748906 является мастер-источником цен по решению владельца.

## Запреты

- Не создавать новые вкладки.
- Не исправлять и не форматировать существующие вкладки.
- Не удалять и не перемещать файлы.
- Не запускать deploy, SSH, sync и миграции.
- Не считать похожие названия дублями без сравнения схемы и содержимого.
- Не выбирать источник правды самостоятельно.
- Не заходить и не выполнять git-команды во вложенном репозитории
  `99_Archive/C_Documents_WHIEDA_2026-07-26/.git` — это чужой архивный
  worktree, не текущий проект.

Результат: два TSV и короткий Markdown-отчет только с фактами и вопросами владельцу.
