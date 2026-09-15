# WWC Owner Admin — P0.3.5.5: ограниченный deploy identity для staging-статики

## Решение

P0.3.5.4 готов принять отдельный OpenSSH key. Этот ключ **нельзя** добавлять пользователю `root`: обычная выкладка статики не требует полного доступа к VPS.

Нужен отдельный системный пользователь **`wwcdeploy`** только для `/var/www/admin-staging-wwc-best` и только с принудительным deploy-gate command.

## Scope

Подготовить код/скрипты/документацию. Не выполнять apply на сервере без отдельного явного одобрения владельца.

Разрешённые области:

- `backend/deploy/staging/`;
- `n8n/current/`;
- P0.3.5 tests/docs.

Не менять production, Core VPS, n8n workflows, database, Telegram и public `wwc.best`.

## Целевая модель доступа

```text
Windows operator key
  -> SSH user wwcdeploy @ Site VPS
  -> forced command /usr/local/libexec/wwc-admin-staging-deploy-gate
  -> only admin-staging static directory
```

В `authorized_keys` ключ получает:

```text
command="/usr/local/libexec/wwc-admin-staging-deploy-gate",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding
```

Пользователь не имеет sudo и не может войти в shell. Он не владеет и не пишет ничего вне целевого staging-каталога.

## Gate: допустимые команды

Gate получает `SSH_ORIGINAL_COMMAND` и разрешает **только**:

1. server side `scp -t` для пути:
   `/var/www/admin-staging-wwc-best/cabinet-staging.<16 hex>.tgz`
2. `mkdir -p /var/www/admin-staging-wwc-best`
3. `sha256sum` того же временного файла + фиксированный `awk` для первого поля
4. `tar -xzf` того же временного файла с `-C /var/www/admin-staging-wwc-best`
5. `rm -f` того же временного файла.

Любая другая команда, иной путь, подстановка, shell operator, `..`, wildcard и интерактивный shell → отказ с non-zero.

Чтобы избегать инъекций, gate не вызывает `eval`; он сопоставляет полную команду с безопасным regex и запускает фиксированные бинарники с разобранным token.

## Подготовить

1. Идемпотентный **dry-run-first** setup script:
   - создаёт `wwcdeploy` system user без sudo;
   - создаёт/проверяет доступ только к staging static directory;
   - кладёт gate с root ownership и mode `755`;
   - добавляет public key в `/home/wwcdeploy/.ssh/authorized_keys` с forced command/restrictions;
   - выводит только key fingerprint/paths, не private key.
2. Gate script и локальные тесты reject/allow matrix.
3. Обновить `staging_openssh_transport.py`: target user по default `wwcdeploy`, не root. `WHIEDA_SITE_SSH_USER` override запрещён или допускает только `wwcdeploy` для staging static deploy.
4. Runbook: one-time apply требует явно `--apply`, отдельно от обычного deploy.

## Тесты

- exact `scp -t`, sha256, tar, rm команд разрешены;
- `rm -rf`, `/etc/*`, другой temp name, `;`, `&&`, `$()`, `..`, shell без command — отклонены;
- generated authorized_keys содержит all restrictions;
- static transport default target `wwcdeploy`;
- полный P0.3.5 test set зелёный.

## Приёмка

В рамках задачи server apply запрещён. Отчёт: изменено / где видно / проверено / ограничения.

После code review я запрошу отдельный явный owner approval на единовременное создание `wwcdeploy` и его ограниченного ключа на Site VPS. Только после него можно будет выложить P0.2.3.
