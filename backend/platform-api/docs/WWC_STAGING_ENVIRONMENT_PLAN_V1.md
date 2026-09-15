# WWC — план испытательной среды для кабинета и событий V1

**Дата:** 2026-08-09  
**Этап:** P0.0  
**Ограничение:** production, Google Sheets и n8n workflows **не меняются** этим планом.  
**Цель:** среда, на которой можно безопасно проверить P0.1 admin API, journey-события и read-only кабинет (P0.2–P0.3).

---

## 1. Цели staging для кабинета

| # | Цель | Критерий готовности |
|---|------|---------------------|
| G1 | Admin API read model на реалистичных данных | `/v1/admin/leads` возвращает ≥1 lead; cross-tenant test fails |
| G2 | Markets/prices/centers snapshot | `wwc_*` seeded; `/v1/admin/markets` не пустой |
| G3 | Journey + interaction_events | `journeyApi` on staging site/host; events в Postgres |
| G4 | Lead ↔ session linkage | lead POST включает `visitor_session_id`; `interaction_events.lead_created` exists |
| G5 | Telegram identity (optional track) | `telegramLinkApi` on staging; exchange smoke |
| G6 | Cabinet UI acceptance (P0.3) | `cabinet.staging.wwc.best` or closed path; desktop 1440 + mobile 390 |
| G7 | Metrika parity check | Manual: ref_visit in Metrika vs Core session counts (same staging ref) |

---

## 2. Существующая инфраструктура (факт)

### 2.1. Local Docker Postgres

| Параметр | Значение |
|----------|----------|
| Compose | `postgres/docker-compose.local-staging.yml` |
| Port | `55432` |
| Apply script | `postgres/scripts/apply_staging_platform_all.ps1` |
| Verify empty | `postgres/scripts/verify_staging_apply_empty.py` |
| Markets seed | `postgres/scripts/staging_seed_wwc_markets_v1.sql` |
| HTTP proof | `backend/platform-api/scripts/staging_markets_http_proof.py` |

**Состояние:** `staging`/`local` — **работает** (markets tests PASS 390/390).

### 2.2. Local Core lab

| Параметр | Значение |
|----------|----------|
| Port | `:8080` (default lab) |
| Docs | `backend/platform-api/docs/LOCAL_CORE_LAB_PREREQUISITES.md` |
| E2E reports | `backend/platform-api/reports/local_core_e2e/` |
| Journey E2E | `n8n/current/whieda_staging_journey_e2e_2026-08-07.py --base-url http://127.0.0.1:8080` |

### 2.3. Remote staging stack (prepared, not production)

| Параметр | Значение |
|----------|----------|
| Host | `whieda-n8n` (`185.252.232.93`) |
| Path | `/opt/whieda-platform-staging` |
| API port | **8081** |
| Deploy script | `n8n/current/deploy_platform_core_staging_2026-08-09.py` (default `--dry-run`) |
| nginx upstream | `backend/deploy/staging/sysarchn8n.nginx-staging-upstream.conf` |
| Health | `https://sysarchn8n.duckdns.org/whieda-platform-staging/health/live` |

**Состояние:** artifacts exist; **deploy not applied** without owner OK (`backend/deploy/staging/README.md`).

### 2.4. Site staging hooks

| Asset | Location |
|-------|----------|
| Runtime flags | `03_Website/wwc-best/public/wwc-runtime-config.json` |
| Journey client | `public/wwc-api/journey.js` |
| Metrika | injected on all pages (production today) |
| Markets nginx alias | `backend/deploy/staging/wwc.best.nginx-markets-staging.conf` (optional `staging.wwc.best`) |

**Факт production site flags:** `journeyApi: off`, `telegramLinkApi: off`, `market_centers_v1: false`, `leadApi: legacy`.

---

## 3. Целевая топология staging (кабинет + events)

```text
                    ┌─────────────────────────────┐
                    │  cabinet.staging.wwc.best   │  (P0.2 — static host, noindex)
                    │  or /admin on staging site  │
                    └──────────────┬──────────────┘
                                   │ HTTPS + admin session
                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│  Platform API staging :8081 (or local :8080)                      │
│  NEW: /v1/admin/*  +  existing public/journey/leads routes        │
└──────────────┬───────────────────────────────┬───────────────────┘
               │ RLS tenant=whieda             │
               ▼                               ▼
     ┌─────────────────┐            ┌─────────────────────┐
     │ Postgres staging │            │  n8n (unchanged)     │
     │ website_leads    │            │  lead delivery smoke │
     │ referral_*       │            │  (optional canary)   │
     │ wwc_*            │            └─────────────────────┘
     │ visitor_sessions │
     │ interaction_events│
     └─────────────────┘
               ▲
               │ journey + leads (staging flags ON)
     ┌─────────┴──────────┐
     │ staging.wwc.best    │  journeyApi=on, market_centers_v1=test
     │ or local astro preview│
     └────────────────────┘
               │
               ▼
     Yandex Metrika (separate counter or filter recommended)
```

---

## 4. Фазы развёртывания

### Phase 0 — Local only (сейчас, zero SSH)

**Scope:** P0.1 API development + pytest.

```powershell
cd D:\Projects\WHIEDA
docker compose -f postgres\docker-compose.local-staging.yml up -d
python postgres\scripts\ensure_local_core_database.py
# apply SQL chain per PLATFORM_API_CONTRACT_V1.md § SQL apply order
python backend\platform-api\scripts\staging_markets_http_proof.py
cd backend\platform-api && python -m pytest tests/ -q
```

**Data:** use existing seeds + synthetic leads via POST `/v1/leads` in tests.

**Events:** enable journey in test client / E2E scripts only — not public site.

### Phase 1 — Staging API on whieda-n8n :8081

**Prerequisite:** owner OK on deploy script.

```powershell
python n8n\current\deploy_platform_core_staging_2026-08-09.py   # no --dry-run
python n8n\current\patch_ai_nginx_markets_staging_2026-08-09.py --dry-run  # review first
```

**Post-deploy smoke:**

```bash
curl -s "https://sysarchn8n.duckdns.org/whieda-platform-staging/health/live"
curl -s -H "Host: wwc.best" \
  "https://sysarchn8n.duckdns.org/whieda-platform-staging/v1/reports/leader-digest?days=7"
```

**DB:** staging Postgres must receive same SQL chain as local; markets seed optional.

**Do NOT:** point production `wwc.best` default routes to staging upstream for leads/advisor.

### Phase 2 — Staging site flags (journey + session→lead)

**Separate host recommended:** `staging.wwc.best` with `wwc-runtime-config.staging.json`:

```json
{
  "refApi": "fallback",
  "leadApi": "v1",
  "advisorApi": "legacy",
  "journeyApi": "on",
  "telegramLinkApi": "on",
  "features": { "market_centers_v1": true },
  "endpoints": {
    "visitorSessions": "https://sysarchn8n.duckdns.org/whieda-platform-staging/api/v1/visitor-sessions",
    "interactionEvents": "https://sysarchn8n.duckdns.org/whieda-platform-staging/api/v1/interaction-events",
    "leads": "https://sysarchn8n.duckdns.org/whieda-platform-staging/api/v1/leads"
  }
}
```

**Code change (P0.1/P0.2 site track, staging only):**

1. `leads.js` — add optional `visitor_session_id` from `getVisitorSessionId()` when journey enabled.
2. Deploy staging build to staging host only — **not** production wwc.best.

**Verification:**

```powershell
python n8n\current\whieda_staging_journey_e2e_2026-08-07.py --base-url https://sysarchn8n.duckdns.org/whieda-platform-staging
```

### Phase 3 — Admin API + cabinet host

1. Implement `/v1/admin/*` on staging `:8081`.
2. nginx vhost `cabinet.staging.wwc.best` → static UI + reverse proxy `/api` → staging upstream **with auth**.
3. Telegram allow-list IDs in staging env var (not committed): `WWC_ADMIN_TELEGRAM_IDS`.

**Security checklist:**

- noindex, robots disallow
- admin routes not on public `wwc.best` nginx
- no service account JSON in UI
- rate limit admin login/token exchange

### Phase 4 — Events catalog verification (master §7)

| Event group | Staging test |
|-------------|--------------|
| Вход/навигация | `route_opened` + Metrika pageview same session |
| ref | `ref_visit` (Metrika) + `visitor_sessions.first_ref` (Core) |
| Вовлечённость | manual scroll tests — catalog events still **plan**; document as gap |
| Заявка | `lead_submit` (Metrika) + `lead_created` (Core) same idempotency window |
| Telegram | link token exchange → `telegram_identity_linked` event (**plan** name; today partial) |
| Sync | markets manual sync → registry `status=ok` visible in A8 |

**Volume guard:** run `POST /v1/pilot/refresh-metrics` on staging; assert `duplicate_events` near zero after idempotency replay test.

---

## 5. Данные staging: что копировать vs синтезировать

| Data | Strategy | Notes |
|------|----------|-------|
| `referral_profiles` / `lead_actors` | Snapshot subset from prod **read-only export** OR manual seed | No Sheets changes; use SQL INSERT scripts |
| `website_leads` | Synthetic + optional anonymized export | Mask PII in non-prod |
| `wwc_*` | `staging_seed_wwc_markets_v1.sql` + template xlsx | 36 SKU / 72 price rows |
| `visitor_sessions` / events | Generated by journey E2E | Fresh per test run |
| Metrika | Staging counter **recommended** | Avoid polluting prod counter 111158320 |

**Forbidden:** pointing staging cabinet at production Postgres.

---

## 6. Google Sheets на staging

**Rule (cabinet spec):** cabinet never reads Sheets from browser.

For markets sync on staging:

1. Owner creates **copy** of markets template spreadsheet (`WWC_MARKETS_GOOGLE_SHEETS_OWNER_SETUP.md`).
2. Service account shared to copy only.
3. `WWC_MARKETS_SYNC_MODE=google` on staging env only.
4. Partners_Ref — continue using prod sheet sync **or** freeze snapshot in Postgres for staging; **do not** run dual writers.

---

## 7. n8n на staging

**No workflow changes** per P0 constraints.

Optional read-only probes for cabinet sync screen (future):

- Script on n8n host reading last execution of `WHIEDA Structured Sync Cron` — expose via internal endpoint, not browser.

Lead delivery on staging: use test Telegram chat IDs or dry-run delivery flag if available — verify `whieda_leads_report_smoke` pattern.

---

## 8. CI / regression gates

| Gate | Command | When |
|------|---------|------|
| Unit+integration | `pytest tests/ -q` | every backend change |
| Markets HTTP | `staging_markets_http_proof.py` | markets/admin changes |
| Journey E2E | `whieda_staging_journey_e2e_2026-08-07.py` | journey/site staging changes |
| Admin security | new `tests/test_admin_*.py` | P0.1 |
| Unified smoke | `whieda_unified_smoke_pack_2026-08-01.py` | before owner demo — **production routes unchanged** |

---

## 9. Rollback

| Layer | Rollback |
|-------|----------|
| Staging API :8081 | redeploy previous bundle from deploy script backup |
| nginx staging upstream | remove include; reload nginx |
| Site staging host | revert runtime config; disable DNS |
| Postgres staging | restore snapshot; re-apply migrations from git |
| Cabinet UI | remove vhost |

Production **unaffected** — separate port, path, and host.

---

## 10. Timeline (recommended)

| Week | Deliverable | Environment |
|------|-------------|-------------|
| W0 (now) | P0.0 docs + data map | git only |
| W1 | P0.1 admin auth + A2–A4 endpoints | local `:8080` + pytest |
| W2 | A1, A5–A8 + deploy staging `:8081` | whieda-n8n staging |
| W3 | Phase 2 site flags + lead session link | `staging.wwc.best` |
| W4 | P0.2 cabinet UI against staging API | cabinet.staging |
| W5 | P0.3 owner acceptance report | staging only |

Production cutover — **explicit owner command** not included.

---

## 11. Open decisions (owner/architect)

1. Staging Metrika: separate counter vs filtered segment?
2. `staging.wwc.best` DNS — who provisions?
3. Anonymized prod lead snapshot — allowed for staging?
4. Cabinet host: `cabinet.staging.wwc.best` vs path on staging site?
5. Partners sync status in A8 — wait for n8n probe or show `gap` through P0?

---

## 12. Связанные документы

- `WWC_OWNER_CABINET_DATA_MAP_V1.md`
- `WWC_OWNER_CABINET_API_GAPS_V1.md`
- `backend/deploy/staging/README.md`
- `backend/platform-api/docs/STAGING_IDENTITY_ONBOARDING_RUNBOOK.md`
- `03_Website/wwc-best/docs/JOURNEY_CLIENT_V1.md`
- `03_Website/wwc-best/docs/ANALYTICS-PRIVACY-MAP.md`
