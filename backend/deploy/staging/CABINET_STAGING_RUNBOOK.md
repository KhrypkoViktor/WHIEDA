# WWC Owner Cabinet — Staging Deploy Runbook (P0.3.5)

**Host:** `admin-staging.wwc.best` only. **Production не трогать.**

## Схема

```text
Browser / Telegram
  → https://admin-staging.wwc.best (Site VPS 173.249.45.83)
       /cabinet/  static
       /api/*     nginx → http://127.0.0.1:18081/
  → SSH reverse tunnel (systemd: wwc-admin-staging-tunnel.service on Core)
  → Core VPS 185.252.232.93
       Docker staging API 127.0.0.1:8081 only
```

Защита: **loopback binding + tunnel**, не публичный `:8081` и не «UFW restricted» без фактических правил.

## Диагностика HTTP

| Ответ `/api/v1/admin/me` | Значение |
|--------------------------|----------|
| **404** | nginx proxy или API/tunnel не подключены |
| **401** | proxy работает, auth защищён (ожидаемо без cookie) |

## Порядок

### 1. Precheck (до apply)

```powershell
python n8n/current/precheck_staging_cabinet_p0_3_5_2026-08-10.py
```

Проверяет: DNS, TLS cert file, Core/Site SSH, secrets (только имена ключей), локальные конфиги.  
**Не требует** работающий API, `:8081`, `401`.

### 2. Deploy (orchestrator)

```powershell
python n8n/current/run_staging_cabinet_p0_3_5_2026-08-10.py
python n8n/current/run_staging_cabinet_p0_3_5_2026-08-10.py --apply --insecure-smoke
```

Повторный deploy **не** передаёт `--with-base` в SQL. Первичная инициализация чистой среды — только явно:

```powershell
python n8n/current/run_staging_cabinet_p0_3_5_2026-08-10.py --apply --bootstrap-base
```

SSH: host key проверяется по `n8n/current/staging_ssh_host_keys.json` (SHA-256 fingerprint, без AutoAddPolicy).  
**Static upload (P0.3.5.4):** системные `scp.exe` / `ssh.exe` с deploy key — **не** Paramiko SFTP. Temp `cabinet-staging.<random>.tgz` → SHA-256 → extract → cleanup; timeout = `upload_timeout`.

Шаги: SQL → Core API → merge secrets → static site → nginx vhost.  
Credentials: Core/Site precheck — `PGPASSWORD`, `WHIEDA_SSH_PASSWORD`, `WHIEDA_SITE_SSH_PASSWORD`.  
**Static deploy** — только key-based OpenSSH (см. ниже).

### One-time: wwcdeploy identity (отдельно от обычного deploy)

Перед первым static `--apply` оператор **один раз** создаёт least-privilege deploy identity на Site VPS:

```powershell
python n8n/current/setup_staging_site_wwcdeploy_identity_2026-08-11.py --public-key-file C:\path\to\wwcdeploy.pub
python n8n/current/setup_staging_site_wwcdeploy_identity_2026-08-11.py --public-key-file C:\path\to\wwcdeploy.pub --apply
```

Dry-run по умолчанию: JSON-план (user, gate path, staging dir, fingerprint — **без private key**).  
`--apply` требует root SSH (`WHIEDA_SITE_SSH_PASSWORD`) и выполняется **отдельно** от orchestrator/deploy.

Модель доступа:

```text
Windows operator key
  -> SSH user wwcdeploy @ Site VPS
  -> forced command /usr/local/libexec/wwc-admin-staging-deploy-gate
  -> only /var/www/admin-staging-wwc-best
```

Gate разрешает только: `scp -t` temp `.tgz`, `mkdir -p`, `sha256sum`, `tar -xzf`, `rm -f` temp.  
**P0.3.5.5.1:** перед extract gate вызывает `/usr/local/libexec/wwc-admin-staging-archive-verify` — только обычные файлы/каталоги с относительными путями (без symlink/hardlink/device/FIFO/`..`/absolute). Extract: `tar --no-same-owner --no-same-permissions`.  
`authorized_keys`: idempotent append через `grep -Fxq` полной строки (не первый token).

### Static deploy env (после one-time setup)

Env для static deploy (обязательны key + known_hosts):

```text
WHIEDA_SITE_SSH_KEY_PATH         # private key file (wwcdeploy)
WHIEDA_SITE_KNOWN_HOSTS_PATH     # known_hosts с pinned ключом Site VPS
WHIEDA_SITE_SSH_HOST             # default 173.249.45.83
WHIEDA_SITE_SSH_USER             # default wwcdeploy (root запрещён)
```

Без key/known_hosts deploy script завершится с кодом **2** (`site_ssh_key_required`).  
`WHIEDA_SITE_SSH_USER=root` → exit **2** (`site_ssh_user_forbidden`).  
Нет fallback на password, SFTP или `StrictHostKeyChecking=no`.

Проверка dry-run:

```powershell
python n8n/current/deploy_cabinet_staging_site_2026-08-09.py
```

### 3. Post-deploy smoke (после apply)

```powershell
python backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py --insecure
```

Проверяет: Core `:8081/health/live`, tunnel unit, Site `:18081/health/live`, public `401`/`200`.

### 4. Webhook (только после green smoke)

```powershell
python n8n/current/setup_staging_cabinet_telegram_webhook_2026-08-09.py --apply
```

URL: `https://admin-staging.wwc.best/api/v1/telegram/wwc-cabinet-staging-bot/webhook`

## Tunnel (Core VPS)

```bash
systemctl status wwc-admin-staging-tunnel.service
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/health/live   # Core
```

На Site VPS:

```bash
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/health/live
```

## Deprecated

- `restrict_staging_api_firewall_2026-08-09.py` — не использовать
- `precheck_staging_cabinet_p0_3_2` — делегирует в P0.3.5 precheck
- прямой `proxy_pass` на `185.252.232.93:8081`

## Живая приёмка

`https://admin-staging.wwc.best/cabinet/` → QR → **@wwc_admin_staging_bot**
