# WHIEDA Quality Lab — Delivery Report

**Date:** 2026-08-08  
**Scope:** offline QA only — no prod, no publish, no bot changes.

## Commands

```powershell
cd D:\Projects\WHIEDA
.\qa\run_all_qa.ps1
```

## Actual output

```
VALIDATION: PASS (264 cases)
pytest tests/qa/: 11 passed
=== WHIEDA Quality Lab: PASS ===
```

## Corpus stats

| Group | Cases | Min required |
|---|---:|---:|
| catalog_card | 38 | 35 |
| catalog_price | 36 | 35 |
| aliases_typo | 38 | 35 |
| followup_context | 31 | 30 |
| photo_video_certificate | 26 | 25 |
| comparison | 21 | 20 |
| cart_and_basket | 21 | 20 |
| business_faq | 21 | 20 |
| promotion_event | 16 | 15 |
| safety_and_clarification | 16 | 15 |
| **Total** | **264** | **250** |

**P0:** 141 · **P1:** 105 · **P2:** 18

## Deliverables

- `qa/cases/whieda_regression_cases_v1.jsonl` — 264 curated cases
- `qa/run_whieda_regression.py` — `--validate`, `--summary`, `--report`
- `qa/run_all_qa.ps1` — one-command offline QA
- `qa/WHIEDA_QA_COVERAGE_MATRIX.md` — coverage + release gate 20
- `backend/platform-api/tests/qa/test_regression_corpus.py` — 11 contract tests
- `qa/build_regression_corpus_v1.py` — maintainer regen tool (offline)
- `qa/reports/WHIEDA_REGRESSION_CORPUS_REPORT.md` — generated report

## Known gaps (not hidden)

1. Corpus validates **expectations offline** — does not call advisor API or score live answers.
2. Media cases assert **mode contract**, not CDN byte delivery.
3. Promotion/community cases are **P2** — content goes stale without live refresh.
4. Live execution layer (Core HTTP smoke / n8n) remains separate from this corpus.

## Release gate

20 case IDs listed in `qa/WHIEDA_QA_COVERAGE_MATRIX.md` (CAT-001 … CRT-021).
