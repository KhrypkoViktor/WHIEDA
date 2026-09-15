# WWC Owner Cabinet — P0.3.5.1 completion report

**Дата:** 2026-08-11  
**Scope:** deploy safety fixes in ops scripts. **`--apply` на серверах не выполнялся.**

## 1. Что изменено

- **SSH host key:** `staging_deploy_lib.connect_ssh()` использует `StrictFingerprintPolicy` + `n8n/current/staging_ssh_host_keys.json` (Core/Site SHA-256). `AutoAddPolicy` удалён.
- **Atomic static upload:** temp `cabinet-staging.<random>.tgz` → SHA-256 verify → extract → cleanup; `upload_timeout` с ошибкой `upload_timeout`; live static не перезаписывается до verify.
- **Orchestrator:** повторный `--apply` без `--with-base`; явный `--bootstrap-base` для первичной SQL-инициализации.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| SSH policy + upload | `n8n/current/staging_deploy_lib.py` |
| Host key fingerprints | `n8n/current/staging_ssh_host_keys.json` |
| Static deploy | `n8n/current/deploy_cabinet_staging_site_2026-08-09.py` |
| Orchestrator | `n8n/current/run_staging_cabinet_p0_3_5_2026-08-10.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_p0_3_5.py` |
| Runbook | `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py` | unit tests (mock SSH/upload/orchestrator) |
| `staging_deploy_lib.py` source | нет `AutoAddPolicy` |
| Dry-run orchestrator | `--bootstrap-base` виден в выводе без apply |

## 4. Ограничения

- Серверы и staging **не изменялись**.
- Live SSH/SFTP upload не гонялся (нужны env + owner apply).
- Fingerprint Site/Core зафиксированы в repo; смена ключа сервера потребует обновления JSON.
