# HLR Discovery Triage V1

Read-only triage from **live** Human Language Rails HTTP acceptance.

- HLR report: `HLR_HTTP_REPORT_20260814T191033Z-9ded1af4.json`
- Run ID: `20260814T191033Z-9ded1af4`
- Report status: `PASS`
- Discovery-relevant failures: 0 / 0 total live failures

## Classification summary

| classification | count |
|---|---:|

## product_choices failures

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|

## direct_answer / typo product failures

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|

## task_selection vs product-card auto-open

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|

## Notes for Core + discovery map

- Live run shows Core often **auto-opens** `structured_card` where HLR expects `product_choices` (активатор, pro, стельки, очки, линчжи, прокладки).
- Goal phrases («нужен подарок», budget/task flows) fail when Core opens a product card instead of `task_selection`. Map row «подарок» is `generic_category` and must not drive auto-open.
- `красный` failure is `corpus_expectation_gap`: Core asks about red elixir specifically; corpus expects generic «уточн» wording — align after owner review.
- `набор`, `прокладки`, `пептид` are **fixture_data_gap** for map V1 (not in mandatory phrases).

Regenerate after new live run:

```bash
python qa/product_discovery/run_product_discovery_map.py --offline
```
