# WHIEDA Drovosek: Product Discovery Map V1

## Goal

Prepare a reviewable, deterministic data layer for people who do not know an
exact WHIEDA product name. This is a **data/QA task only**. It must help the
Core owner later implement the second advisor rail: "not sure about the name ->
show 2-3 relevant products".

## Strict scope

May create or change only:

- `qa/product_discovery/`
- `backend/platform-api/docs/PRODUCT_DISCOVERY_MAP_LOCAL_REPORT.md`
- tests under `backend/platform-api/tests/test_product_discovery_*.py`

Must not change:

- `backend/platform-api/app/**`
- `n8n/**`, `postgres/**`, Google Sheets, master snapshots
- production, Docker compose, Telegram, Dify
- the golden/Human Language Rails corpora

## Source of truth (read only)

Use newest valid snapshot under:

`n8n/live-exports/structured-master/<snapshot-id>/`

Required inputs:

- `products.tsv`
- `product_aliases.tsv`
- `product_cards.tsv`
- `resources.tsv`

Record source snapshot id and SHA-256 hashes. Never invent a SKU. If evidence
is insufficient, keep a candidate in `needs_owner_review` rather than mapping
it to a product.

## Deliverable A: discovery-map candidates

Create `qa/product_discovery/PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv` with one
row per discovery phrase and candidate SKU. Required columns:

`phrase, normalized_phrase, discovery_group, candidate_rank, sku,
canonical_name, confidence, status, evidence, source_snapshot_id, notes`

Allowed `status`:

- `ready_for_core_review`
- `needs_owner_review`
- `generic_category`
- `do_not_resolve`

Allowed `confidence`: `high`, `medium`, `low`.

### Mandatory discovery phrases

Build rows and rankings for at least these groups, using only snapshot evidence:

1. `эликсир`, `красный эликсир`, `зелёный эликсир`, `синий эликсир`.
2. `активатор`, `активатор про`, `pro`.
3. `бэм`, `magic`, `массажер`.
4. `паста`, `зубная паста`, `цинфэн`, `паста с полынью`.
5. `стельки`, `очки`, `пояс`, `наколенники`, `шейная накладка`.
6. `косметика`, `маска`, `гель`, `шампунь`, `крем`.
7. `капсулы`, `чай`, `кофе`, `бад`.
8. `сауна`, `сон`, `прибор для дома`, `подарок`.

Rules:

- Exact named product can be `ready_for_core_review` and rank 1.
- Broad words such as `капсулы`, `косметика`, `подарок`, `бад` must remain
  category/generic unless a single catalog product is unambiguously evidenced.
- One-word colours (`красный`, `зелёный`, `синий`) must be `do_not_resolve`.
- Each phrase may have at most three ordered candidate products.
- Do not use medical claims, indications, or advice in this artifact.

## Deliverable B: resolution policy

Create `qa/product_discovery/PRODUCT_DISCOVERY_RESOLUTION_POLICY_V1.md`.
It must say, in plain Russian:

- when Core may open a product card directly;
- when it should show 2-3 choices;
- when it should open task selection instead;
- why colours and generic categories must not become a random product;
- examples for each result.

## Deliverable C: HLR failure triage, read only

Read most recent `qa/human_language_rails/reports/HLR_HTTP_REPORT_*.json`.
Create `qa/product_discovery/HLR_DISCOVERY_TRIAGE_V1.md` with every current
failure from these buckets:

- `product_choices`;
- typo product direct answers;
- exact product vs generic-category expectation mismatch.

For each case: `case_id`, user text, observed answer mode, classification
(`core_resolver_gap`, `corpus_expectation_gap`, `fixture_data_gap`,
`owner_decision`), and a one-sentence reason. Do not modify corpus or Core.

## Tooling and tests

Implement:

- `qa/product_discovery/build_product_discovery_map.py`
- `qa/product_discovery/run_product_discovery_map.py --offline`
- deterministic snapshot selection and SHA verification;
- lint that rejects unknown SKUs, more than 3 candidates per phrase, duplicate
  `(normalized_phrase, sku)`, invalid status/confidence, or missing evidence.

Add pytest coverage for all lint failures and for mandatory phrase groups.

## Acceptance

```bash
cd /d/Projects/WHIEDA
python qa/product_discovery/build_product_discovery_map.py
python qa/product_discovery/run_product_discovery_map.py --offline
python -m pytest backend/platform-api/tests/test_product_discovery_*.py -q
```

Report actual totals: phrases, candidate rows, ready/review/generic/do-not-
resolve, groups covered, source snapshot id, and all HLR classifications.

No commit is required until architect review. Do not claim that this is wired
into runtime: it is an input artifact for the Core owner.
