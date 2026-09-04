# Gap Operator Control Plane — local report

**Date:** 2026-08-10  
**Scope:** local/staging only — not production-ready

## Migration

- `postgres/sql/platform_advisor_gap_review_v1.sql` (registered in `staging_proof_lib.APPLY_ORDER`)

## Endpoints

- `GET /v1/admin/advisor-gaps/summary`
- `GET /v1/admin/advisor-gaps`
- `GET /v1/admin/advisor-gaps/{id}`
- `GET /v1/admin/advisor-gaps/export?format=md|csv`
- `PATCH /v1/admin/advisor-gaps/{id}`

Contract: `backend/platform-api/docs/ADVISOR_GAP_OPERATOR_API_V1.md`

## What was run locally

| Check | Result |
|-------|--------|
| `pytest tests/test_advisor_gap_operator.py` | **18 passed** |
| `pytest backend/platform-api/tests -q` (excl. acceptance/e2e) | **370 passed** (4 pre-existing env failures in parity runner import / integration) |
| `qa/gap_operator/run_gap_operator.py` | **PASS** (refresh insert=37, second unchanged=37, RLS, export redaction) |

## Redaction proof

- API `_public_item()` omits session/contact fields.
- Markdown/CSV export uses volunteer-facing columns only (no UUID in main MD table).
- Export tests assert absence of `session_ref`, `trace:` raw dumps, phone patterns.

## Not verified / out of scope

- Production deploy or live migration apply
- Browser UI for operators
- Automatic promotion to Sheets, RAG, aliases or FAQ runtime
- Telegram notifications from export

## Commands

```powershell
python -m pytest backend/platform-api/tests/test_advisor_gap_operator.py -q
python backend/platform-api/scripts/refresh_advisor_gap_review_queue.py --tenant whieda --dry-run
python backend/platform-api/scripts/export_advisor_gap_review.py --tenant whieda --format md
python qa/gap_operator/run_gap_operator.py
python backend/platform-api/scripts/run_local_core_lab.py --e2e --gap-operator
```
