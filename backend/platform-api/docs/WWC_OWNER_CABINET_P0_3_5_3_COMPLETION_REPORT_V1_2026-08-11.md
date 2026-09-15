# WWC Owner Cabinet — P0.3.5.3 completion report

**Дата:** 2026-08-11  
**Scope:** real SFTP upload timeout in deploy lib. **`--apply` на серверах не выполнялся.**

## 1. Что изменено

- `upload_bytes_with_timeout()` вызывает `remote_file.settimeout(remaining_seconds)` **перед каждым** `write()`.
- `socket.timeout`, `TimeoutError` и timeout-like `OSError` мапятся в `UploadTimeoutError("upload_timeout")`.
- Алгоритм temp → SHA-256 → extract → cleanup без изменений; при timeout extract не вызывается.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| SFTP timeout | `n8n/current/staging_deploy_lib.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_p0_3_5.py` |
| Task spec | `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_5_3_REAL_SFTP_TIMEOUT_V1_2026-08-11.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py` | **29 passed** |
| `settimeout()` positive before write | mock test |
| `socket.timeout` / `TimeoutError` → `UploadTimeoutError` | mock tests |
| timeout/mismatch → no `tar -xzf` | existing tests |

## 4. Ограничения

- Live SFTP hang не воспроизводился в этой сессии (нет `--apply`).
- Owner повторит staging static deploy P0.2.3 и проверит URL/401/отсутствие `/cabinet/qa/`.
