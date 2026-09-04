# No Blind Zone — local report

**Date:** 2026-08-09
**Scope:** Core-only local/staging (no prod, Telegram publish, n8n, Sheets, Dify)

## Summary

| Check | Result | Notes |
|---|---|---|
| Corpus lint (42 cases) | PASS | `qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl` |
| Unit + contract tests | PASS | 21 tests in `tests/test_no_blind_zone.py` |
| Offline runner | PASS | `python qa/no_blind_zone/run_no_blind_zone.py --offline` |
| HTTP corpus (live Core) | PASS | 42/42 — P0 26/26, P1 16/16 on `127.0.0.1:8080` |
| Gap DB proof (live Core) | PASS | Two identical gaps created one event with `repeat_count=2` and hashed `session_ref` |
| Full pytest | PASS | 487 tests in `backend/platform-api/tests` |

## Behaviour delivered

- Six `gap_kind` values on unresolved paths; `knowledge_gap` answer_mode kept for unknown/unsupported catalogue gaps.
- Prohibited dead-end fragments stripped via `sanitize_user_text` and seed update (`knowledge_gap_generic`).
- `advisor_gap` events use existing `interaction_events`, a privacy-safe `session_ref` hash and a 30-minute deduplication window.
- Gap write failures log warning and never block user response.
- Bare follow-ups (`цена`, `видео`, `фото`, …) → `unknown_followup` before generic errors.
- Missing photo/video/certificate on known SKU → `missing_resource` with guided next steps.

## Commands

```powershell
python -m pytest backend/platform-api/tests/test_no_blind_zone.py -q
python qa/no_blind_zone/run_no_blind_zone.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --no-blind-zone
```

## Limitations

- All proof runs are local Docker only. They do not publish to production, n8n, Telegram, Sheets or Dify.
- Operator sample uses fixtures only — see `NO_BLIND_ZONE_OPERATOR_SAMPLE.md`.
- Parity corpus expectations unchanged except guard test for price missing text (no «в базе»).

## Files touched (this block)

- `backend/platform-api/app/advisor/gap.py`
- `backend/platform-api/app/advisor/sql/engine.py`
- `backend/platform-api/app/advisor/sql/formatters.py`
- `postgres/scripts/staging_seed_whieda_advisor_local_v1.sql`
- `backend/platform-api/tests/test_no_blind_zone.py`
- `backend/platform-api/tests/parity/test_parity_guards.py`
- `qa/no_blind_zone/` (corpus, runner, lab)
- `backend/platform-api/scripts/run_local_core_lab.py`
- `backend/platform-api/scripts/local_core_lab/` (orchestrator, constants, e2e_report)
- `NO_BLIND_ZONE_LOCAL_REPORT.md`, `NO_BLIND_ZONE_OPERATOR_SAMPLE.md`
