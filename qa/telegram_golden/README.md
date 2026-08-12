# Telegram Golden Corpus (offline)

Owner-language regression layer for Telegram advisor UX. **Does not modify Core runtime.**

Spec: [`backend/platform-api/docs/WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_TASK_V1_2026-08-12.md`](../../backend/platform-api/docs/WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_TASK_V1_2026-08-12.md)  
Review fix: [`backend/platform-api/docs/WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_REVIEW_FIX_V1_2026-08-12.md`](../../backend/platform-api/docs/WHIEDA_DROVOSEK_TELEGRAM_GOLDEN_CORPUS_REVIEW_FIX_V1_2026-08-12.md)

## Full offline run (one command)

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\run_telegram_golden.py --offline
```

This rebuilds positive corpus, negative safety fixtures, runs lint + pytest hook, prints `TELEGRAM_GOLDEN: PASS`.

## Layout

- `whieda_telegram_golden_cases_v1.jsonl` — positive single-turn golden cases
- `whieda_telegram_golden_flows_v1.jsonl` — positive multi-turn flows
- `whieda_telegram_golden_negative_fixtures_v1.jsonl` — **internal-only** raw safety routes (not positive golden)
- `fixtures/snapshot_cards/` — approved product card snapshots (≥12 SKUs)
- `fixtures/snapshot_service_replies/` — greeting/capabilities/help etalon texts
- `lab/` — import, lint, offline runner (no HTTP)
- `reports/` — local build lint (gitignored)

## Classes (positive corpus)

`greeting`, `capabilities`, `company`, `catalog`, `product_card`, `price`, `media`, `clarification`, `basket`, `business`, `safe_boundary`

## Manual steps

```powershell
python qa\telegram_golden\build_golden_corpus.py
python qa\telegram_golden\build_negative_fixtures.py
python qa\telegram_golden\compile_snapshot_fixtures.py
```
