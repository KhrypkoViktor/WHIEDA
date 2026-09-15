# WWC Owner Cabinet — P0.3.5.4 completion report

**Дата:** 2026-08-11  
**Scope:** заменить Paramiko SFTP на OpenSSH `scp`/`ssh` для static deploy. **`--apply` на серверах не выполнялся.**

## 1. Что изменено

- Новый transport: `n8n/current/staging_openssh_transport.py` — `scp` upload → `ssh` sha256 verify → extract → cleanup temp only.
- `deploy_cabinet_staging_site_2026-08-09.py` использует OpenSSH transport; без `WHIEDA_SITE_SSH_KEY_PATH` + `WHIEDA_SITE_KNOWN_HOSTS_PATH` → exit **2** (`site_ssh_key_required`).
- Из `staging_deploy_lib.py` удалены SFTP helpers (`upload_bytes_with_timeout`, `upload_staging_tarball_atomic`, `UploadTimeoutError` для Paramiko).
- Strict args на каждый вызов: `BatchMode=yes`, `StrictHostKeyChecking=yes`, `UserKnownHostsFile=…`, `-i key`; subprocess timeout на `scp`.
- Tests переписаны под OpenSSH; SFTP timeout tests удалены.
- Runbook: one-time Site deploy key setup перед первым static `--apply`.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| OpenSSH transport | `n8n/current/staging_openssh_transport.py` |
| Static deploy script | `n8n/current/deploy_cabinet_staging_site_2026-08-09.py` |
| Paramiko (precheck/smoke only) | `n8n/current/staging_deploy_lib.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_p0_3_5.py` |
| Runbook | `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md` |
| Task spec | `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_5_4_OPENSSH_STATIC_TRANSPORT_V1_2026-08-11.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py` | **30 passed** |
| missing key/known_hosts → `site_ssh_key_required` | unit test |
| scp/ssh strict args (BatchMode, StrictHostKeyChecking, UserKnownHostsFile, `-i`) | unit test |
| success: upload → verify → extract → cleanup order | unit test |
| hash mismatch / scp timeout → no extract + cleanup temp | unit tests |
| deploy script без `open_sftp`, `upload_bytes_with_timeout`, Paramiko SFTP | source assertion |

## 4. Ограничения

- Live static deploy с Windows **не запускался** (нет owner-approved deploy key на Site VPS).
- Paramiko остаётся только для precheck/smoke SSH (password + fingerprint policy).
- После одобрения owner: one-time deploy key на Site VPS → повтор staging static deploy P0.2.3 → verify `/cabinet/` 200, `/api/v1/admin/me` 401, нет `/cabinet/qa/`.
