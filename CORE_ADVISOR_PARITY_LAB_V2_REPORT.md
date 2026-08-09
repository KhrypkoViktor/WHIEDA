# Core Advisor Parity Lab V2 — Report

Date: 2026-08-09  
Branch: `feat/platform-scale-core`

## 1. What existed before

- Local Core E2E lab (`run_local_core_lab.py --e2e`) with HTTP smoke + 8-case acceptance seed.
- Minimal advisor local schema (`platform_advisor_structured_local_v1.sql`) and 5-product seed.
- Acceptance lab (`qa/acceptance/`) with configurable target and P0 regression corpus (production-oriented).
- Core SQL advisor engine with structured modes but no dedicated local parity corpus.
- n8n-side parity scripts (`whieda_advisor_parity_matrix_2026-08-03.py`) — not integrated into local lab.

## 2. Existing modules reused

| Module | Use |
|--------|-----|
| `qa/acceptance/lab/target.py` | Build advisor HTTP requests |
| `qa/acceptance/lab/transport.py` | `UrllibTransport` (localhost only) |
| `qa/acceptance/lab/corpus.py` | JSONL load/filter |
| `app/advisor/sql/engine.py` | Structured routing under test |
| `app/advisor/parity/media_assertions.py` | Service-intent media guard patterns |
| `postgres/scripts/ensure_local_core_database.py` | Apply platform + advisor local fixtures |
| `scripts/local_core_lab/orchestrator.py` | Docker lab orchestration |

## 3. Files added/changed

**Added**

- `qa/parity/CORE_ADVISOR_CAPABILITY_MATRIX_V2.json`
- `qa/parity/core_local_parity_cases_v2.jsonl` (85 cases)
- `qa/parity/generate_corpus_v2.py`
- `qa/parity/run_core_local_parity.py`
- `qa/parity/lab/` (assertions, runner, check_target, baseline_gate, report)
- `backend/platform-api/tests/parity/test_parity_guards.py` (25 tests)
- `CORE_ADVISOR_PARITY_LAB_V2_REPORT.md`

**Changed**

- `postgres/sql/platform_advisor_structured_local_v1.sql` — extended local tables (promotions, events, community, basket, recommendations)
- `postgres/scripts/staging_seed_whieda_advisor_local_v1.sql` — full synthetic catalog (NOT PRODUCTION DATA)
- `backend/platform-api/scripts/local_core_lab/orchestrator.py` — `--parity` integration
- `backend/platform-api/scripts/run_local_core_lab.py` — `--parity` flag (requires `--e2e`)
- `.gitignore` — parity raw responses/reports/baselines

## 4. Capability matrix (computed)

| Status | Count / 24 |
|--------|------------|
| `covered_by_http_e2e` | 22 |
| `implemented_local` | 2 (CAP-16 multi-intent, CAP-23 provider blackhole) |
| `missing` | 0 |
| `blocked` | 0 |

HTTP E2E coverage ratio: **22/24 = 91.7%** (from matrix statuses, not hand-waved).

## 5. Pytest result

**423 passed** (includes 25 new parity guard tests + existing suite).

Parity guard tests validate corpus size, capability mapping, localhost guard, baseline gate, and report honesty — **not** live HTTP.

## 6. Docker E2E result

**NOT RUN**

Reason: `docker` CLI not available on the agent host (`docker info` → command not found). No containers were started; no live parity HTTP report was produced.

## 7. Real failures

None observed — Docker parity pipeline was not executed.

Expected risk on first real run (not verified here): some P0 cases may fail until engine/seed alignment is tuned (e.g. `активатор` clarification vs card, certificate empty messaging, starter basket wording). Failures would appear in `qa/parity/reports/PARITY_REPORT_*.md`.

## 8. What blocks production

Unchanged from canon — this lab is **local-only**:

- No production/Supabase/Sheets/n8n/Telegram deploy.
- Production catalog remains owner-locked; local fixtures are synthetic.
- Telegram cutover still requires measured parity on live legacy route + owner decision.

## 9. Consciously not checked

- Live legacy n8n SQL advisor comparison (dual-run parity).
- Production Google Sheets catalog sync.
- Telegram photo delivery pipeline.
- Dify / deep coach path.
- Load/stress gates under n8n queue pressure.
- Full production alias table and medical review queue content.

## 10. Recommended next task

After Docker Desktop install on owner machine:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --parity
```

Review `qa/parity/reports/PARITY_REPORT_*.md`, fix any real P0 failures against synthetic seed (not production cards), then optionally add `--accept-baseline` only if all 85 cases PASS with zero unasserted.

Owner command:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --parity
```
