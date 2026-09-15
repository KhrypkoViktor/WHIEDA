# WWC Owner Admin — P0.3.5.1: три правки безопасности повторного deploy

## Почему P0.3.5 ещё не принят

P0.3.5 правильно разделил precheck и post-deploy smoke. Тесты `12/12` проходят. Но реальный orchestrator всё ещё имеет три риска, выявленных при проверке кода:

1. `staging_deploy_lib.connect_ssh()` применяет `paramiko.AutoAddPolicy()`: первый попавшийся ключ сервера принимается автоматически. Это недопустимо для deploy с root-доступом.
2. Static deploy использует `sftp.file(...).write(...)` без ограниченного времени передачи. На реальной Windows-машине этот путь уже зависал. SHA-256 защищает от повреждения, но не от бесконечного зависания.
3. Оркестратор всегда передаёт `--with-base` в `apply_staging_cabinet_sql...`. Повторный deploy staging не должен каждый раз пытаться применить базовые tenant/production Telegram migrations.

## Scope

Только:

- `D:\Projects\WHIEDA\n8n\current`;
- тесты/документация P0.3.5.

Не выполнять `--apply`, не менять серверы, базы, nginx, бота или production.

## 1. Строгая проверка SSH host key

В конфигурации явно зафиксировать ожидаемые fingerprint для Site VPS и Core VPS. Использовать политику Paramiko, которая:

- принимает ключ **только** при совпадении ожидаемого SHA-256 fingerprint;
- при несовпадении или неизвестном ключе завершает deploy до любой команды;
- не пишет новые ключи в user known_hosts и не использует `AutoAddPolicy`.

Не печатать password, токены и приватные ключи.

Тесты: correct fingerprint → connection allowed (mock); unknown/mismatch → исключение до `connect`/deploy action.

## 2. Ограниченная и атомарная передача статики

Static deploy обязан иметь явный конечный timeout на SSH/SFTP transfer и понятную ошибку `upload_timeout`.

Алгоритм:

1. загрузить в уникальный временный файл вида `cabinet-staging.<random>.tgz`;
2. сверить SHA-256 этого конкретного временного файла;
3. только при совпадении распаковать;
4. при ошибке/timeout удалить только временный файл; текущая опубликованная статика остаётся нетронутой.

Нельзя перезаписывать единственный `cabinet-staging.tgz` до завершения проверки.

Тесты: mismatch не вызывает extract; timeout не вызывает extract; успешный путь вызывает verify → extract → cleanup в правильном порядке.

## 3. Не применять base migrations автоматически

В `run_staging_cabinet_p0_3_5...` заменить:

```python
("apply_staging_cabinet_sql_2026-08-10.py", ["--with-base"])
```

на обычный запуск без `--with-base`.

Если когда-либо нужна первичная инициализация чистой среды, дать явный отдельный флаг orchestrator, например `--bootstrap-base`, по умолчанию выключенный. Он должен быть виден в dry-run и требовать отдельного сознательного запуска.

Тест: обычный `--apply` не содержит `--with-base`; `--bootstrap-base` содержит.

## 4. Отчёт

Исправить в completion report путь тестов: фактический файл — `tests/test_staging_deploy_p0_3_5.py`, не `test_staging_cabinet_p0_3_5.py`.

Отчёт строго 4 пунктами. Не заявлять, что deploy выполнен или staging изменён.
