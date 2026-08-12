# Telegram Golden Corpus — Local Report

Date: 2026-08-12 (v1.1 review fix)  
Task: `WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_TASK_V1_2026-08-12.md`  
Review: `WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_REVIEW_FIX_V1_2026-08-12.md`  
Scope: `qa/telegram_golden/` + offline tests only (no `app/**`, n8n, postgres)

## Summary

Positive golden corpus + internal-only negative safety fixtures. Offline lab passes without HTTP.

**Status:** PASS (local)

## Positive corpus

| Metric | Count |
|--------|------:|
| Single-turn cases | **187** |
| Multi-turn flows | **35** |
| Snapshot cards | **13** |
| Service reply intents | 8 |

### Cases by class (P0 floors met)

All 11 classes remain above task minimums after rebuild.

## Negative fixtures (internal-only)

| Metric | Count |
|--------|------:|
| RAG observed_dialogue safety fixtures | **5** |
| File | `whieda_telegram_golden_negative_fixtures_v1.jsonl` |
| `review_status` | `blocked_raw_internal_only` |

Source: `RAG/.../18_DIALOGUE_FLOWS.tsv` — only `flow_type=observed_dialogue` + `provenance_verified=да` (FLOW-OBS-001 … FLOW-OBS-005).

**Not positive golden.** Raw dialogue answers are **not** copied as etalon text. Fixtures assert route boundary only: no product card, no price, no treatment instruction; `expected_gap_kind=medical_or_safety_boundary`.

## Snapshot cards (≥12)

Added from master export `n8n/live-exports/structured-master/20260810T083328Z/product_cards.tsv`:

| Slug | SKU | Product |
|------|-----|---------|
| soy_peptide | F038-00 | Низкомолекулярный соевый пептид |
| foher_elixir | F001-02 | Эликсир Фохоу |
| treasures_elixir | F002-02 | Эликсир 3 Драгоценности |
| sancin_elixir | F003-02 | Эликсир Саньцин |
| tsinfeng_paste | F071-00 | Паста Цинфэн |

Plus existing 8 device/supplement cards (activator … spirulina).

## Provenance fix (Block C)

`service_intent_fuzz` cases are imported deterministically from `backend/platform-api/tests/test_telegram_service_intent_fuzz.py::INTENT_CASES` with explicit `source.provenance`. Corpus lint rejects missing/invalid source kinds and claims to non-existent upstream files.

## Hygiene

- `qa/telegram_golden/.gitignore` — `reports/`, `__pycache__/`
- Generated `reports/build_lint.json` stays local-only

## Acceptance commands (executed)

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\build_golden_corpus.py
python qa\telegram_golden\run_telegram_golden.py --offline
python -m pytest backend\platform-api\tests\test_telegram_golden_corpus.py -q
```

## NOT DONE

- Tier-2 RAG RAW positive mining (`sources/rag_curated_v1.md`)
- `run_local_core_lab.py --e2e --telegram-golden` hook
- Runtime / production / live Telegram / Sheets
- **Zero diff** in `backend/platform-api/app/**` for this block
