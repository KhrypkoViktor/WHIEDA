# WWC Owner Cabinet — P0.3.5.2 completion report

**Дата:** 2026-08-11  
**Scope:** post-deploy smoke import fix. **Deploy и серверы не менялись.**

## 1. Что изменено

- В `post_deploy_staging_cabinet_smoke_2026-08-10.py` добавлен явный bootstrap: `ROOT / "n8n" / "current"` в `sys.path` **до** lazy-import `whieda_runtime_env` / `staging_deploy_lib`.
- Unit-тесты: subprocess без `PYTHONPATH`, mock SSH (8081/18081/tunnel), public 404 → fail.

## 2. Где видно

| Артефакт | Путь |
|----------|------|
| Smoke script | `backend/platform-api/scripts/post_deploy_staging_cabinet_smoke_2026-08-10.py` |
| Tests | `backend/platform-api/tests/test_staging_deploy_p0_3_5.py` |

## 3. Что проверено фактически

| Проверка | Результат |
|----------|-----------|
| `pytest tests/test_staging_deploy_p0_3_5.py tests/test_staging_cabinet_orchestrator_gate.py` | **27 passed** |
| Локальный запуск smoke (без ручного PYTHONPATH) | нет `ModuleNotFoundError`; Core `:8081` → 200, tunnel → active, public → 401/200 |
| Site `:18081` локально | `RuntimeError` (нет `WHIEDA_SITE_SSH_PASSWORD` в env — ожидаемо) |

Пример локального вывода (фрагмент):

```json
"core_local_8081_health": {"status": "200", "ok": true},
"core_tunnel_unit": {"state": "active", "ok": true},
"public_http": {"ok": true, "/api/v1/admin/me": 401}
```

## 4. Ограничения

- Полный `passed: true` локально не достигнут без Site SSH env.
- Повторная read-only проверка на live staging — за owner после этого отчёта.
