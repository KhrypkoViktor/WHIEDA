# P0.3.5.5.2 — совместимость OpenSSH SCP с ограниченным staging deploy gate

## Причина

В `n8n/current/staging_openssh_transport.py` системный `scp` вызывается без флага `-O`.
Современный OpenSSH использует SFTP по умолчанию. Но ключ `wwcdeploy` принудительно
запускает `wwc-admin-staging-deploy-gate`, который намеренно разрешает только
серверную команду `scp -t <строго-проверенный-tarball>`.

В таком виде первая реальная загрузка завершится ошибкой: SFTP не является
разрешённой командой gate. Это нужно исправить **до** создания пользователя
`wwcdeploy` на Site VPS.

## Единственная задача

В `build_scp_command()` добавить клиентский флаг `-O` (capital O) к вызову
системного `scp`.

Он принудительно включает legacy SCP protocol. На сервере это даёт ровно
разрешённую шлюзом команду `scp -t <temp-path>`.

Пример ожидаемой формы команды:

```text
scp.exe -O -o BatchMode=yes ... local.tgz wwcdeploy@173.249.45.83:/var/www/admin-staging-wwc-best/cabinet-staging.<16hex>.tgz
```

## Ограничения

- Не менять gate whitelist: SFTP и shell не разрешать.
- Не добавлять password fallback, `StrictHostKeyChecking=no`, root-доступ или SFTP.
- Не менять production, nginx, backend API, SQL, UI, бота.
- Не запускать server `--apply`.
- Не менять способ проверки SHA-256 и порядок `temp → verify → extract → cleanup`.

## Приёмка

1. Unit-тест проверяет, что `build_scp_command()` содержит отдельный аргумент `-O`.
2. Регрессия сохраняет `BatchMode=yes`, `StrictHostKeyChecking=yes`,
   `UserKnownHostsFile` и identity file.
3. Проверяется, что разрешённая серверная команда остаётся `scp -t <safe path>`;
   SFTP не добавляется в gate.
4. Прогнать:

```powershell
python -m pytest backend/platform-api/tests/test_staging_deploy_p0_3_5.py backend/platform-api/tests/test_staging_cabinet_orchestrator_gate.py backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5.py backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5_1.py -q
```

5. В отчёте: diff, точный тестовый результат, и явное подтверждение: серверы не
   трогались, `--apply` не выполнялся.
