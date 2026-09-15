# WWC Owner Cabinet — P0.3.5 completion report

**Дата:** 2026-08-11  
**Scope:** ops scripts + runbook. **`--apply` на серверах не выполнялся.**

## 1. Что изменено

- **Precheck/post-deploy split:** `precheck_staging_cabinet_p0_3_5_2026-08-10.py` (до apply) vs `post_deploy_staging_cabinet_smoke_2026-08-10.py` (после).
- **Orchestrator P0.3.5:** `run_staging_cabinet_p0_3_5_2026-08-10.py` — стоп на любом этапе; webhook только после post-deploy smoke.
- **Схема tunnel:** nginx `127.0.0.1:18081`, Docker `127.0.0.1:8081`; deprecated `restrict_staging_api_firewall`.
- **Deploy hardening:** `staging_deploy_lib.py` (npm.cmd/npm, SHA-256 upload verify, explicit SSH keys off).
- **Runbook:** `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md` переписан под P0.3.5.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| Precheck | `n8n/current/precheck_staging_cabinet_p0_3_5_2026-08-10.py` |
| Post-deploy smoke | `backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py` |
| Orchestrator | `n8n/current/run_staging_cabinet_p0_3_5_2026-08-10.py` |
| Runbook | `backend/deploy/staging/CABINET_STAGING_RUNBOOK.md` |
| Task spec | `backend/platform-api/docs/WWC_OWNER_CABINET_P0_3_5_STAGING_DEPLOY_HARDENING_V1_2026-08-10.md` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py` | **12 passed** |
| Dry-run orchestrator P0.3.5 без `--apply` | precheck-only path |
| Firewall script | exit 2 deprecated |
| nginx conf | no direct Core IP |

## 4. Ограничения

- Серверы **не изменялись** (критерий задачи).
- Post-deploy smoke против live staging не гонялся (нужны SSH env + tunnel).
- P0.2.2 UI на admin-staging — отдельный deploy static, не входил в P0.3.5.
