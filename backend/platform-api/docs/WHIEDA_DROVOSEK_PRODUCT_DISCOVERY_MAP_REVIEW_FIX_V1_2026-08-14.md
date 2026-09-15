# Product Discovery Map V1: Review Fix

## Scope

Fix only the data/QA artifact from `WHIEDA_DROVOSEK_PRODUCT_DISCOVERY_MAP_TASK_V1_2026-08-14.md`.

Allowed:

- `qa/product_discovery/**`
- `backend/platform-api/tests/test_product_discovery_map.py`
- `backend/platform-api/docs/PRODUCT_DISCOVERY_MAP_LOCAL_REPORT.md`

Forbidden:

- `backend/platform-api/app/**`
- Core runtime, n8n, Postgres, Sheets, snapshots, production, corpora.

## Required fixes

### 1. Triage must use the real latest HLR report

The repository now has real reports under:

`qa/human_language_rails/reports/HLR_HTTP_REPORT_*.json`

Use the newest valid one (currently `20260814T154952Z-f5e9841f.json`) as the
primary input for `HLR_DISCOVERY_TRIAGE_V1.md`.

- Do not say `Live HTTP: no files` when a report exists.
- Classify every actual failure whose expected rail is `product_choices`, plus
  typo/direct-product failures and exact-product/generic-category mismatches.
- Include `observed answer_mode` and actual failure reason.
- Keep `corpus_proxy` only as an explicit fallback when there are no reports.

### 2. Remove map/policy contradictions

The following must be consistent across TSV, policy and report:

- `подарок`: it is a task-selection entry on first turn. Change its rows to
  `generic_category` or otherwise make policy and status unambiguous: Core must
  not auto-open a gift SKU for `нужен подарок`.
- `стельки`: policy currently claims `D013 + D013-06`, but TSV has only D013.
  Either add snapshot-backed D013-06 with evidence, or remove that claim. No
  undocumented SKU in examples.
- `очки`, `маска`, `шампунь`, `чай`, `кофе`, `линчжи`, `прокладки`: if one
  strong exact product is snapshot-backed, distinguish this from a generic
  category in policy. Do not declare every exact product a mandatory choice.
- `активатор`: explain that base+PRO is intentionally an ambiguity despite base
  rank 1; Core must not silently auto-open it.

### 3. Status and confidence validation

Add lint/tests which reject:

- phrase `подарок` marked ready/direct while policy says task selection;
- policy examples mentioning a SKU not in the TSV/snapshot;
- a `generic_category` row with exactly one SKU unless `notes` says why it is
  still generic;
- `ready_for_core_review` short bare tokens (`pro`, `magic`, `массажер`) with
  low/medium confidence and no explicit direct-safe note.

### 4. Human-readable integration table

Append a compact table to the policy:

`phrase -> Core action -> why -> up to 3 SKU buttons`

It must cover `активатор`, `паста`, `эликсир`, colours, `гель`, `капсулы`,
`подарок`, `крем`, and `цена` without product context.

Actions limited to:

- `open_card`
- `show_choices`
- `task_selection`
- `universal_menu`
- `clarify_only`

## Acceptance

```bash
cd /d/Projects/WHIEDA
python qa/product_discovery/build_product_discovery_map.py
python qa/product_discovery/run_product_discovery_map.py --offline
python -m pytest backend/platform-api/tests/test_product_discovery_map.py -q
```

Report both source snapshot and exact live HLR report used. Do not claim that
the map is wired into Core. Commit only after all checks are green.
