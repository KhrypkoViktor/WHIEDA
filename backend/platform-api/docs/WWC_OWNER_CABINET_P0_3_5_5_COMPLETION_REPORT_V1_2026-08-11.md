# WWC Owner Cabinet — P0.3.5.5 completion report

**Дата:** 2026-08-11  
**Scope:** least-privilege deploy identity `wwcdeploy` + deploy gate для staging static. **`--apply` на сервере не выполнялся.**

## 1. Что изменено

- Gate validation library: `n8n/current/staging_deploy_gate_lib.py` — allow/reject matrix, `authorized_keys` builder, fingerprint helper.
- Bash gate: `backend/deploy/staging/wwc-admin-staging-deploy-gate` → install target `/usr/local/libexec/wwc-admin-staging-deploy-gate`.
- One-time setup (dry-run first): `n8n/current/setup_staging_site_wwcdeploy_identity_2026-08-11.py` — `--public-key-file` required, `--apply` для server setup.
- `staging_openssh_transport.py`: default user `wwcdeploy`, `WHIEDA_SITE_SSH_USER` только `wwcdeploy`, `remote_dir` validated against `STAGING_STATIC_DIR`.
- `deploy_cabinet_staging_site_2026-08-09.py`: dry-run показывает `wwcdeploy`; `SiteSshDeployUserForbiddenError` → exit **2**.
- Tests: `backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5.py`; P0.3.5 tests updated for `wwcdeploy` + staging dir.
- Runbook: wwcdeploy identity model + отдельный one-time `--apply`.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| Gate lib | `n8n/current/staging_deploy_gate_lib.py` |
| Bash gate | `backend/deploy/staging/wwc-admin-staging-deploy-gate` |
| Identity setup | `n8n/current/setup_staging_site_wwcdeploy_identity_2026-08-11.py` |
| OpenSSH transport | `n8n/current/staging_openssh_transport.py` |
| Static deploy | `n8n/current/deploy_cabinet_staging_site_2026-08-09.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_gate_p0_3_5_5.py` |
| Runbook | `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md` |
| Task spec | `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_5_5_LEAST_PRIVILEGE_DEPLOY_IDENTITY_V1_2026-08-11.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py tests/test_staging_deploy_gate_p0_3_5_5.py -q` | **54 passed** |
| allow: scp -t, mkdir, sha256, tar, rm | unit tests |
| reject: rm -rf, /etc/*, wrong temp, shell ops, .., bash | unit tests |
| authorized_keys all restrictions | unit test |
| transport default wwcdeploy; root override forbidden | unit tests |
| remote_dir must match STAGING_STATIC_DIR | unit test |

## 4. Ограничения

- Server `--apply` для `setup_staging_site_wwcdeploy_identity_2026-08-11.py` **не выполнялся** (требует owner approval).
- Live static deploy с Windows key для `wwcdeploy` не запускался.
- Paramiko password SSH остаётся для precheck/smoke/nginx (root); static deploy — только key-based `wwcdeploy`.

## 5. Следующий шаг (owner)

После code review: явное одобрение → `setup_staging_site_wwcdeploy_identity_2026-08-11.py --apply` на Site VPS → staging static deploy P0.2.3.
