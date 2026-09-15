# WWC Owner Admin — P0.3.5.4: заменить Paramiko SFTP для static deploy

## Факт реальной приёмки

P0.3.5.3 unit-тесты проходили, но реальный deploy `P0.2.3` снова завис внутри Paramiko SFTP `write()`. Процесс был остановлен внешним timeout; staging не переключался. Единственный временный архив был удалён вручную после точной проверки имени.

**Вывод:** Paramiko SFTP нельзя использовать для передачи статики с этой Windows-машины. Его больше не нужно пытаться чинить таймерами.

## Scope

Только deploy-транспорт и его тесты:

- `n8n/current/deploy_cabinet_staging_site_2026-08-09.py`;
- новый небольшой OpenSSH transport helper в `n8n/current`;
- `staging_deploy_lib.py` только если требуется удалить SFTP code;
- P0.3.5 test files/runbook.

Не выполнять `--apply`, не менять серверы, authorized_keys, nginx, Core, SQL или Telegram.

## Требуемая схема static deploy

Static deploy использует **системные `scp.exe` и `ssh.exe`**, а не Paramiko SFTP.

Параметры берутся только из env:

```text
WHIEDA_SITE_SSH_KEY_PATH        # путь к заранее выданному private key
WHIEDA_SITE_KNOWN_HOSTS_PATH    # known_hosts с pinned ключом Site VPS
```

Если одного параметра нет — завершить до upload с понятным кодом `site_ssh_key_required`. Никакого fallback на password/SFTP/`StrictHostKeyChecking=no`.

### Последовательность

1. Сборка создаёт staging config как сейчас.
2. Архив получает уникальное имя `cabinet-staging.<random>.tgz`.
3. `scp` передаёт архив во временный путь с:
   - `BatchMode=yes`;
   - `StrictHostKeyChecking=yes`;
   - `UserKnownHostsFile=<WHIEDA_SITE_KNOWN_HOSTS_PATH>`;
   - `-i <WHIEDA_SITE_SSH_KEY_PATH>`;
   - конечным timeout через `subprocess.run(timeout=...)`.
4. `ssh` с теми же strict параметрами: SHA-256 временного архива → при совпадении extract → удалить только этот temp.
5. При timeout/error/mismatch: не extract; best-effort удалить только этот temp; опубликованная статика остаётся прежней.

Не добавлять SSH-ключ автоматически на сервер, не использовать `AutoAddPolicy`, `accept-new`, `StrictHostKeyChecking=no` или public-key fingerprint без known_hosts.

## Тесты

Добавить unit tests, проверяющие:

- без key/known_hosts есть `site_ssh_key_required` до запуска `scp`;
- `scp`/`ssh` содержат все strict аргументы;
- upload timeout → no extract + cleanup temp;
- hash mismatch → no extract + cleanup temp;
- success: upload → verify → extract → cleanup в порядке;
- source deploy script не импортирует и не вызывает `open_sftp`, `paramiko.SFTPClient` или `upload_bytes_with_timeout`.

Запустить полный P0.3.5 test set.

## Документация

Runbook должен отдельно сказать: один раз до первого static deploy оператор создаёт/кладёт ограниченный Site deploy key и known_hosts. Это отдельное явное действие владельца, не часть обычного deploy.

## Приёмка

Отчёт: изменено / где видно / проверено / ограничения. Серверные apply запрещены.

После положительной проверки я отдельно попрошу владельца разрешить один раз создать ограниченный deploy key на Site VPS, затем повторю staging deploy P0.2.3.
