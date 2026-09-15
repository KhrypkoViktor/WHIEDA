# WWC Owner Cabinet — P0.1 Completion Report (V1, 2026-08-09)

**Scope:** backend `platform-api` + PostgreSQL migration `platform_admin_cabinet_v1.sql`.  
**Out of scope (unchanged):** production deploy, `03_Website`, n8n, Google Sheets, public UI.

---

## 1. Что сделано

- Реализован модуль **`app/admin/`**: Telegram challenge → confirm → poll → HttpOnly session cookie; read-only `/v1/admin/*` (auth, me, leads, referrals, markets/prices/service-centers/sync-status, overview).
- Миграция **`postgres/sql/platform_admin_cabinet_v1.sql`**: `platform_admin_principals`, `platform_admin_login_challenges`, `platform_admin_sessions`, `platform_admin_audit_log` + RLS (service role `app.admin_service`).
- Registry событий и gap-DTO для неготовых полей (markets/prices/sync и т.д.).
- Документация: `PLATFORM_API_CONTRACT_V1.md`, design note P0.1, `WWC_OWNER_CABINET_P0_1A_NEXT.md`, обновлён `verify_staging_apply_empty.py` (admin tables в проверке).
- **Follow-up (этот проход):** скрипт `scripts/admin_cabinet_staging_smoke.py`; исправлен вызов `GET /v1/admin/referrals` (`search=` вместо несуществующего kwargs `q` в `build_referrals_list`).

---

## 2. Что проверено

### Pytest (полный suite)

| Результат | Количество |
|-----------|------------|
| **passed** | **450** |
| failed | 0 |

Отдельно `tests/test_admin_cabinet.py`: **11 passed**.

### Local staging HTTP smoke

**Среда:** Docker `whieda-local-staging-postgres` на `127.0.0.1:55432`, БД `whieda_platform_local_core` (таблицы `platform_admin_*` уже применены; повторный apply не требовался).

**Команда:** `python scripts/admin_cabinet_staging_smoke.py` из `backend/platform-api`.

| Проверка | Результат |
|----------|-----------|
| `POST /v1/admin/auth/challenge` → confirm → poll → cookie | **PASS** |
| `GET /v1/admin/me` (super_admin) | **PASS** |
| `GET /v1/admin/leads`, `/referrals`, `/markets`, `/sync-status`, `/overview` | **PASS** |
| Markets: gap **или** данные (fixture DB с markets) | **PASS** |
| 401 без сессии; 403 wrong confirm secret / unknown Telegram ID | **PASS** |
| Poll body без bearer/token | **PASS** |
| Logout → последующий `/me` → 401 | **PASS** |
| super_admin `tenant_id=test-acme` + запись в `platform_admin_audit_log` | **PASS** |
| Lead detail | **PASS** (skip: в fixture DB нет leads) |

**Итог smoke:** **PASS** (19/19 checks, `failed: 0`).

**Не выполнялось в этом прогоне:** удалённый VPS/staging API за nginx (`cabinet.staging.wwc.best`) — только local Docker Postgres + in-process TestClient (тот же паттерн, что `staging_markets_http_proof.py`).

---

## 3. Что сознательно не тронуто

- Production deploy, nginx public routing, `cabinet.wwc.best`.
- Сайт `03_Website/`, публичные страницы и UI кабинета (P0.2).
- n8n workflows, Telegram legacy webhook, delivery заявок.
- Google Sheets и sync-пайплайны.
- Write-API кабинета, назначение owner/watcher, `website_events` writer.
- Секреты в git (super Telegram IDs и confirm secret — только env).

---

## 4. Что дальше

1. **P0.1A (staging slice):** `visitor_session_id` на ingest, `GET /v1/admin/interaction-events`, staging-only site flag — см. `WWC_OWNER_CABINET_P0_1A_NEXT.md`.
2. **Staging proof на хосте:** поднять platform-api против staging Postgres, прогнать `admin_cabinet_staging_smoke.py` с `PLATFORM_DATABASE_URL` staging (и env admin secrets) — для sign-off вне локальной машины.
3. **P0.2 UI:** кабинет на `cabinet.*` после freeze JSON-контракта.

---

## Готовность к acceptance

| Критерий | Статус |
|----------|--------|
| Контракт + pytest 450 | **Готово** |
| Local Docker staging smoke | **Готово (PASS)** |
| Remote staging / nginx proof | **Pending (P0.1A / ops)** |

**Вывод:** P0.1 backend **готов к acceptance владельцем по коду и локальному staging smoke**; для полного закрытия §9 execution task остаётся **optional remote staging proof** (не блокирует merge backend slice, если owner принимает local proof).

---

*Report generated: 2026-08-09. Smoke script: `backend/platform-api/scripts/admin_cabinet_staging_smoke.py`.*
