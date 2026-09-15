# WWC Owner Cabinet — P0.3.5.5.1 completion report

**Дата:** 2026-08-11  
**Scope:** safe archive validation before extract + idempotent `authorized_keys`. **`--apply` на сервере не выполнялся.**

## 1. Что изменено

- Archive validator: `n8n/current/staging_archive_validator.py` — `tarfile` + `TarInfo` checks (no text parse).
- Server entry: `backend/deploy/staging/wwc-admin-staging-archive-verify` → `/usr/local/libexec/wwc-admin-staging-archive-verify`.
- Bash gate: archive verify **до** `tar -xzf`; extract с `--no-same-owner --no-same-permissions`; reject `unsafe_archive`.
- `staging_deploy_gate_lib.py`: `tar_extract` metadata includes `pre_extract` + safe tar flags; re-exports `UnsafeArchiveError`.
- Setup script: installs validator lib + verify entry; `authorized_keys` idempotency через `grep -Fxq` полной строки (не `split()[0]`).
- Tests: `backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5_1.py`.
- Runbook: archive gate + full-line idempotency.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| Archive validator | `n8n/current/staging_archive_validator.py` |
| Verify entry | `backend/deploy/staging/wwc-admin-staging-archive-verify` |
| Bash gate | `backend/deploy/staging/wwc-admin-staging-deploy-gate` |
| Gate lib | `n8n/current/staging_deploy_gate_lib.py` |
| Identity setup | `n8n/current/setup_staging_site_wwcdeploy_identity_2026-08-11.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5_1.py` |
| Task spec | `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_5_5_1_SAFE_ARCHIVE_GATE_V1_2026-08-11.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| Full P0.3.5 test set | **66 passed** |
| allow: normal files + dirs | unit test |
| reject: symlink, hardlink, absolute, `../`, device, FIFO | unit tests |
| gate source: verify before `tar -xzf` | source assertion |
| setup: `grep -Fxq` full line, no `split()[0]` | source assertion |
| invalid archive → exit 1 `deploy_gate_rejected` | CLI test |

## 4. Ограничения

- P0.3.5.5 **не применять** на сервере до приёмки этой задачи.
- Server `--apply` не выполнялся.
- Owner approval required для one-time `setup_staging_site_wwcdeploy_identity_2026-08-11.py --apply`.
