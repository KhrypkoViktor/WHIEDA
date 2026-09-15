# WWC Owner Admin — P0.3.5.3: реальный timeout SFTP-передачи

## Факт проверки

P0.2.3 собран успешно и не содержит public QA routes. При реальном staging deploy через `deploy_cabinet_staging_site... --apply --skip-build` процесс завис внутри SFTP upload.

Текущий `upload_bytes_with_timeout()` проверяет `time.monotonic()` **до** вызова `remote_file.write(chunk)`. Если сам `write()` блокируется, следующая проверка времени не наступает. Поэтому обещанный timeout сейчас декоративный.

Staging не был переключён: архив не распаковывался.

## Scope

Только `n8n/current/staging_deploy_lib.py`, связанные test files и completion report. Не выполнять `--apply`, не менять серверы, данные, nginx или SQL.

## Задача

1. Передавать реальный timeout в низкоуровневый SFTP channel/file до каждой операции записи. Подходит `remote_file.settimeout(remaining_seconds)` или эквивалентный API Paramiko.
2. Преобразовать `socket.timeout`, `TimeoutError` и SFTP timeout в `UploadTimeoutError("upload_timeout")`.
3. Сохранить алгоритм: временный файл → SHA-256 → extract → cleanup. При timeout extract не вызывается, temp удаляется best effort.
4. Таймаут должен быть ограниченным и не зависеть от внешнего `exec` timeout. В лог не выводить секреты.

## Обязательные тесты

- fake remote file получает `settimeout()` с положительным временем;
- `write()` кидает `socket.timeout` → `UploadTimeoutError`;
- `write()` кидает `TimeoutError` → `UploadTimeoutError`;
- timeout/mismatch не вызывают `tar -xzf`;
- полный P0.3.5 набор зелёный.

Не имитировать «зависание» только изменением `time.monotonic`: нужно проверить именно маппинг исключения от блокирующей операции.

## Приёмка

Отчёт: изменено / где видно / проверено / ограничения. Серверные apply запрещены в рамках этой правки.

После отчёта я снова выполню staging static deploy P0.2.3 и проверю URL/401/отсутствие `/cabinet/qa/`.
