# WWC Owner Admin — P0.3.5.5.1: safe archive gate до создания deploy identity

## Блокер

P0.3.5.5 не применять на сервере в текущем виде.

Forced command правильно ограничивает пути и команды, но разрешает:

```text
tar -xzf <trusted-temp-name> -C /var/www/admin-staging-wwc-best
```

Ключ `wwcdeploy` сможет сам загрузить архив с symlink, например `etc -> /etc`. Nginx из static root потенциально сможет отдать файлы через этот symlink. Ограничение на команды не защищает содержимое разрешённого tar.

Также setup script проверяет `authorized_keys` только по первому token (`command="..."`), а не по полной строке ключа. При ротации или уже существующей записи это может не добавить нужный key.

## Scope

Только P0.3.5.5 gate/setup/tests/docs. Server apply запрещён.

## 1. Проверка содержимого архива перед extract

Перед распаковкой gate обязан принять архив только если каждый tar entry:

- обычный файл или каталог;
- относительный путь;
- не содержит `..`, пустых сегментов и не начинается с `/`;
- не является symlink, hard link, device, FIFO или любым иным special type.

Подходит безопасный root-owned helper (Python `tarfile` с явной проверкой `TarInfo`) или строгая проверка `tar -tvzf`/`tar -tzf`. Предпочтителен Python helper: он не парсит текстовую локаль tar.

После проверки extract должен использовать безопасные flags по возможности (`--no-same-owner`, `--no-same-permissions`) и всё равно выполняться только как `wwcdeploy` в разрешённый каталог.

На invalid archive: non-zero `deploy_gate_rejected: unsafe_archive`, нет extract; временный файл затем удаляется обычным cleanup.

## 2. Идемпотентный `authorized_keys`

В setup заменить проверку по первому token на точное совпадение полной prepared строки:

```text
grep -Fxq '<full authorized_keys line>' ...
```

или эквивалент без regex. Новый public key при ротации добавляется, старый не затирается самовольно.

## 3. Тесты

Добавить allow/reject matrix для archive validator:

- normal files + dirs → allowed;
- symlink → reject;
- hardlink → reject;
- absolute path → reject;
- `../` traversal → reject;
- device/FIFO → reject.

Проверить gate source: archive validation происходит **до** `tar -xzf`.

Проверить setup source/full-line idempotency: используется full auth line, не `split()[0]`.

Полный P0.3.5 test set зелёный.

## Приёмка

Серверы не менять. Отчёт: изменено / где видно / проверено / ограничения.

Только после приёмки P0.3.5.5.1 я запрошу owner approval на создание `wwcdeploy` на Site VPS.
