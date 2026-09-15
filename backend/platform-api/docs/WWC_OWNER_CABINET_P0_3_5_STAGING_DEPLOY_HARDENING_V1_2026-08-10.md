# WWC Owner Admin — P0.3.5: повторяемый и честный staging-deploy

## Статус и цель

**Исполнитель:** Composer (ops/backend scripts).  
**Результат:** staging уже работает, но его повторный deploy должен проходить по реальной схеме, а скрипты не должны заявлять защиту или блокировать первый deploy ложными проверками.

Это hardening существующего staging. Не менять production и не менять прикладной функционал кабинета.

## Фактическая утверждённая схема staging

```text
Browser / Telegram
  -> https://admin-staging.wwc.best
  -> site VPS 173.249.45.83
       /cabinet/  = static
       /api/*     = nginx -> 127.0.0.1:18081
  -> persistent SSH reverse tunnel
  -> Core VPS 185.252.232.93
       Docker API = 127.0.0.1:8081 only
```

На Core активен systemd unit `wwc-admin-staging-tunnel.service`, который публикует `127.0.0.1:8081` на Site VPS как `127.0.0.1:18081`.

Это заменяет старую неподтверждённую схему прямого `site VPS -> 185.252.232.93:8081`.

## Разрешённая область

- `D:\Projects\WHIEDA\backend\platform-api`;
- `D:\Projects\WHIEDA\backend\deploy\staging`;
- `D:\Projects\WHIEDA\n8n\current` — только staging deploy/runbook scripts;
- тесты и документация этих областей.

Не менять: production, public site, SQL-схемы, n8n workflows, Telegram business logic, API payloads.

## Задачи

### 1. Разделить проверки

`precheck` перед `--apply` проверяет только то, что должно существовать **до** deploy:

- DNS `admin-staging.wwc.best` указывает на Site VPS;
- TLS сертификат есть и действителен;
- server secrets существуют (показывать только имена ключей, не значения);
- доступы для Site/Core доступны;
- локальный исходный код/конфиги существуют.

`precheck` **не должен** требовать `/api/v1/admin/me → 401`, работающий API, слушающий `:8081` или уже поднятый Docker: это post-deploy проверки. Иначе первый безопасный deploy невозможно запустить.

После deploy отдельный `post_deploy_smoke` проверяет:

- Core: `127.0.0.1:8081/health/live` → 200;
- Site: `127.0.0.1:18081/health/live` → 200;
- public: `https://admin-staging.wwc.best/api/v1/admin/me` → 401;
- `https://admin-staging.wwc.best/cabinet/` → 200;
- config кабинета enabled true;
- webhook устанавливается **только после** успешного post-deploy smoke.

Orchestrator действительно останавливается при любой ошибке на каждом этапе.

### 2. Зафиксировать сетевую безопасность

- Docker staging API публикуется только как `127.0.0.1:8081:8080`;
- nginx staging vhost использует только `proxy_pass http://127.0.0.1:18081/`;
- runbook объясняет reverse tunnel и проверку systemd unit;
- удалить/пометить deprecated скрипт, который говорит «firewall restricted», если UFW не включён и правило фактически не применено. Нельзя писать, что порт ограничен firewall, когда реальная защита — loopback binding + SSH tunnel.

### 3. Сделать Windows deploy-скрипты повторяемыми

Зафиксировать и покрыть тестами:

- на Windows используется `npm.cmd`, на Linux — `npm`;
- static build запускается ровно один раз;
- SSH-клиенты не пытаются молча использовать агент/случайные ключи; применяют явно предоставленный доступ;
- лог deploy не печатает секреты, токены или пароли;
- конфиг staging кабинета накладывается после сборки;
- upload проверяется SHA-256 до распаковки или используется надёжный SFTP-путь с явной проверкой целостности.

### 4. Документация и отчёт

Обновить один короткий runbook с:

- актуальной схемой;
- precheck → deploy → post-deploy smoke → webhook;
- диагностикой: 404 означает proxy/API не подключены, 401 означает proxy доступен и auth защищён;
- отдельно: production не затрагивается.

## Критерии приёмки

1. Unit-тесты для разделения pre/post deploy и описанных Windows-веток.
2. Dry-run показывает реальную последовательность и не требует работающего API до apply.
3. Не выполнять `--apply` и не менять серверы в рамках этой задачи.
4. Отчёт строго 4 пункта: изменено / где видно / проверено фактически / ограничения.

## Не принимать

- «firewall secure» без фактической проверки активного firewall;
- «deploy ready», если precheck всё ещё требует 401 до deploy;
- прямой внешний порт Core API;
- вывод секретов в логах или документации.
