# WHIEDA Drovosek: Product Card Candidates V1

## Purpose

Prepare upload-ready **draft candidates** for the 17 active catalogue SKUs that
have prices/resources but no `Product_Cards` row. This is a content extraction
and provenance task, not a runtime or Sheets update. The owner will review and
decide what enters the master sheet.

## Canonical inputs

Read only:

- latest valid snapshot: `n8n/live-exports/structured-master/20260811T172219Z/`
- `qa/catalog_experience/CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv`
- `qa/catalog_experience/CATALOG_EXPERIENCE_BACKLOG_2026-08-14.md`
- `WHIEDA_PRODUCT_DIRECTION.md`
- `WHIEDA_HOW_WE_WRITE_AND_ADVISE.md`
- `WHIEDA_MARKETING_VOICE_AND_CONTENT_SYSTEM_V1.md`
- `RAG/` and `D:\Obsidian\WHIEDA\05_WHIEDA_RAG\` source materials

Use Git Bash. No network scrape is required. Do not use a source unless the
product/SKU linkage is explicit or confidently identified by canonical product
name; mark uncertain linkage for review instead of guessing.

## Scope

### A. Candidate rows for missing cards

Create one candidate for each active SKU without a master `Product_Cards` row:

`C002-00, C006-00, C033-00, C061-00, C065-00, D004-00, D013-06,
EU-N000014-24, EU-N000030-25, EU-N000032-25, EU-N000036-26, F087-00,
T001, T002-00, T003, T023`.

Treat `E028-00` (catalogue PDF) separately as a resource/catalogue asset, not a
consumer product card.

For every candidate produce the real Product_Cards columns used by the runtime:

- `project_id`, `sku`, `canonical_name`, `short_name`;
- `what_it_is`, `who_asks_about_it`, `common_use_cases`, `how_to_use_short`,
  `what_to_expect_soft`, `contraindications_short`;
- `primary_image_url` only when confirmed by `resources.tsv`;
- `source_refs`, `provenance_status`, `review_notes` as review-only columns.

Follow the existing strong Telegram card shape: concise title, human use cases,
scannable semicolon-delimited bullets, no raw markdown syntax, no invented
facts. Keep each field factual and specific. Do not turn a missing fact into a
generic sentence merely to fill a cell.

### B. BEM catalogue investigation

Master snapshot has **no BEM SKU**. Search only existing local sources:

- structured exports, prices, aliases, old canonical/card sources, RAG,
  historical project TSV/SQL files.

Produce a separate evidence row:

- found canonical name/SKU + current price/PV source; or
- `not_in_current_master` with all candidate historical names/SKUs and exact
  source locations.

Never add a fictional BEM SKU to a candidate card.

### C. Source confidence and review queue

For every non-empty candidate field record one or more source references. Use:

- `confirmed` — direct current/historical approved source;
- `needs_owner_review` — product linkage or wording is not fully certain;
- `missing_source` — leave runtime field blank and explain.

Build review slices:

1. ready for owner upload;
2. needs source/wording confirmation;
3. blocked because the product is absent from the current master.

## Deliverables

Create only:

- `qa/product_card_candidates/PRODUCT_CARDS_CANDIDATES_2026-08-14.tsv`
- `qa/product_card_candidates/PRODUCT_CARDS_CANDIDATES_REVIEW_2026-08-14.md`
- `qa/product_card_candidates/BEM_CATALOG_EVIDENCE_2026-08-14.md`
- `qa/product_card_candidates/build_product_card_candidates.py`
- `qa/product_card_candidates/tests/test_product_card_candidates.py`
- `backend/platform-api/docs/PRODUCT_CARD_CANDIDATES_LOCAL_REPORT.md`

The TSV must preserve all product-card runtime columns at the front and must
not be uploaded automatically. The report must state that no master/runtime
data was changed.

## Hard boundaries

- No Sheets, Postgres, n8n, Dify, Telegram or production writes.
- No edits under `backend/platform-api/app/**`.
- No rewrites of current approved product cards.
- No commits.

## Acceptance

One reproducible command validates the TSV schema, checks every source ref,
and reports candidate counts by confidence. The owner can review the 16 missing
consumer card candidates and the separate BEM evidence in one pass.
