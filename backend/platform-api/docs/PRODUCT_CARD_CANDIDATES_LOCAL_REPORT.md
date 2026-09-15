# Product Card Candidates — Local Report

Generated: 2026-08-14T11:59:28Z

## Scope

Upload-ready **draft** Product_Cards rows for 16 consumer SKUs missing from master `product_cards.tsv`.
Separate BEM evidence in `qa/product_card_candidates/BEM_CATALOG_EVIDENCE_2026-08-14.md`.

## Safety

- No Sheets, Postgres, n8n, Dify, Telegram or production writes were performed.
- No edits under `backend/platform-api/app/**`.
- Existing approved cards were not rewritten.

## Snapshot

- `20260811T172219Z`

## Outputs

- `qa/product_card_candidates/PRODUCT_CARDS_CANDIDATES_2026-08-14.tsv`
- `qa/product_card_candidates/PRODUCT_CARDS_CANDIDATES_REVIEW_2026-08-14.md`
- `qa/product_card_candidates/BEM_CATALOG_EVIDENCE_2026-08-14.md`

## Summary

- Candidates: **16** / 16 expected

### By provenance_status

- `needs_owner_review`: 7
- `ready_for_owner_upload`: 9

Validation: **pass** (schema + source refs).

## Reproduce

```bash
python qa/product_card_candidates/build_product_card_candidates.py
pytest qa/product_card_candidates/tests/test_product_card_candidates.py -q
```
