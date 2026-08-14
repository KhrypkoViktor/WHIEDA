# HLR Discovery Triage V1

Read-only triage from **live** Human Language Rails HTTP acceptance.

- HLR report: `HLR_HTTP_REPORT_20260814T154952Z-f5e9841f.json`
- Run ID: `20260814T154952Z-f5e9841f`
- Report status: `FAIL`
- Discovery-relevant failures: 23 / 30 total live failures

## Classification summary

| classification | count |
|---|---:|
| `core_resolver_gap` | 20 |
| `corpus_expectation_gap` | 1 |
| `fixture_data_gap` | 2 |

## product_choices failures

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|
| HLR-P0-035 | красный | clarification | corpus_expectation_gap | missing_any ['уточн'] |
| HLR-P0-150 | pro | structured_card | core_resolver_gap | mode expected clarification, got structured_card |
| HLR-P0-151 | эликсир | clarification | core_resolver_gap | missing_any ['эликсир'] |
| HLR-P0-152 | стельки | structured_card | core_resolver_gap | mode expected clarification, got structured_card |
| HLR-P0-153 | очки | structured_card | core_resolver_gap | mode expected clarification, got structured_card |
| HLR-P0-154 | маска | clarification | core_resolver_gap | missing_any ['маск', 'Fundesee'] |
| HLR-P0-155 | гель | clarification | core_resolver_gap | missing_any ['гель', 'Foherb'] |
| HLR-P0-156 | шампунь | clarification | core_resolver_gap | missing_any ['шампун'] |
| HLR-P0-157 | набор | clarification | fixture_data_gap | missing_any ['набор'] |
| HLR-P0-158 | бад | clarification | core_resolver_gap | missing_any ['уточн', 'капсул'] |
| HLR-P0-159 | кофе | clarification | core_resolver_gap | missing_any ['кофе', 'корди'] |
| HLR-P0-160 | капсулы | clarification | core_resolver_gap | missing_any ['капсул'] |
| HLR-P0-161 | чай | clarification | core_resolver_gap | missing_any ['чай'] |
| HLR-P0-162 | линчжи | structured_card | core_resolver_gap | mode expected clarification, got structured_card |
| HLR-P0-163 | прокладки | structured_card | fixture_data_gap | mode expected clarification, got structured_card |
| HLR-P0-202 | сертификатик | knowledge_gap | core_resolver_gap | mode expected clarification, got knowledge_gap |
| HLR-P0-206 | пасту | clarification | core_resolver_gap | missing_any ['паст'] |
| HLR-P0-207 | поис | clarification | core_resolver_gap | missing_any ['пояс'] |
| HLR-P0-208 | актив | clarification | core_resolver_gap | missing_any ['активатор'] |

## direct_answer / typo product failures

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|
| HLR-P0-147 | активatr | clarification | core_resolver_gap | mode expected structured_card, got clarification; missing_any ['Активатор'] |
| HLR-P0-194 | спирулинa | clarification | core_resolver_gap | mode expected structured_card, got clarification; missing_any ['Спирулин'] |
| HLR-P0-225 | цена | clarification | core_resolver_gap | mode expected structured_price, got clarification; missing_any ['BYN'] |

## task_selection vs product-card auto-open

| case_id | user_text | observed answer_mode | classification | reason |
|---|---|---|---|---|
| HLR-P0-110 | стельки | structured_card | core_resolver_gap | mode expected clarification, got structured_card; missing_any ['подбер', 'направлен'] |

## Notes for Core + discovery map

- Live run shows Core often **auto-opens** `structured_card` where HLR expects `product_choices` (активатор, pro, стельки, очки, линчжи, прокладки).
- Goal phrases («нужен подарок», budget/task flows) fail when Core opens a product card instead of `task_selection`. Map row «подарок» is `generic_category` and must not drive auto-open.
- `красный` failure is `corpus_expectation_gap`: Core asks about red elixir specifically; corpus expects generic «уточн» wording — align after owner review.
- `набор`, `прокладки`, `пептид` are **fixture_data_gap** for map V1 (not in mandatory phrases).

Regenerate after new live run:

```bash
python qa/product_discovery/run_product_discovery_map.py --offline
```
