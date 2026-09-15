# WHIEDA Drovosek: Product Card Wording Audit V1

## Purpose

Audit current approved product cards and 16 candidate cards for editorial
review. This is an inspection tool, not an automatic editor. It must help the
owner see where a card is repetitive, vague, bureaucratic, too long, missing a
useful section, or contains a phrase worth a human rewrite.

Do not rewrite any source text. Do not publish anything.

## Read-only sources

- newest valid snapshot under `n8n/live-exports/structured-master/`;
- `qa/product_card_candidates/PRODUCT_CARDS_CANDIDATES_2026-08-14.tsv`;
- `WHIEDA_HOW_WE_WRITE_AND_ADVISE.md`;
- `WHIEDA_MARKETING_VOICE_AND_CONTENT_SYSTEM_V1.md`.

Use the snapshot manifest SHA-256 gate. Abort with a clear report if the newest
snapshot is invalid and choose the newest valid one instead.

## Hard scope

Allowed:

- `qa/product_card_wording_audit/**`
- `backend/platform-api/tests/test_product_card_wording_audit.py`
- `backend/platform-api/docs/PRODUCT_CARD_WORDING_AUDIT_LOCAL_REPORT.md`

Forbidden:

- Sheets, snapshot input, `backend/platform-api/app/**`, `n8n/**`,
  `postgres/**`, production, runtime DB, Dify, Telegram, deploy.

## Audit model

Create a deterministic rule-based audit. Each finding must include:

- `finding_id`, severity (`review` or `attention`), SKU, source layer,
  section/field, exact matched span, rule id, and a short human-readable reason;
- no proposed replacement copy beyond an optional neutral label such as
  `needs human rewrite`.

Rules must flag, not delete or correct:

1. **Repeated wording** inside a card and across cards: repeated sentences,
   duplicated bullets, repeated opening formulae.
2. **Vague/cliche wording**: `работа с зоной дискомфорта`, `мягкая поддержка`,
   `локальная работа`, generic claims with no concrete context, and words from
   the canonical writing documents where a human review is required.
3. **Editorial shape**: empty important section, one giant unbroken paragraph,
   malformed bullet separators, footer duplicated inside a field, excessive
   length, title copied into body repeatedly.
4. **Potentially unclear restrictions**: a restriction-like field with vague
   wording or a missing context. Mark `review`; do not judge truth and do not
   add medical policy.
5. **Candidate provenance**: missing source/ref/status must be `attention` and
   makes the candidate visible in a separate review list.

The audit is evidence, not a score pretending to be objective. Do not call a
card bad merely because it uses a short phrase once.

## Deliverables

`qa/product_card_wording_audit/`:

- `run_product_card_wording_audit.py`;
- `lab/` parser/rules/report modules;
- `PRODUCT_CARD_WORDING_AUDIT_FINDINGS_<snapshot>.csv`;
- `PRODUCT_CARD_WORDING_AUDIT_REVIEW_<snapshot>.md` grouped by SKU;
- `PRODUCT_CARD_WORDING_AUDIT_SUMMARY_<snapshot>.json`;
- fixtures and tests.

The generated reports must be gitignored. Commit only code, fixtures and a
short static local report, never live exports or generated audit output.

Report totals separately for:

- approved cards;
- candidate cards;
- findings by rule/severity;
- no-finding cards;
- cards without a product-card source row.

## Tests and acceptance

Use fixture cards that cover repeated copy, a flagged phrase, clean card,
missing provenance and malformed bullets.

```bash
cd /d/Projects/WHIEDA
python qa/product_card_wording_audit/run_product_card_wording_audit.py
python -m pytest backend/platform-api/tests/test_product_card_wording_audit.py -q
```

Both must pass. State plainly that this produces review markings only and has
not changed product cards, Google Sheets, runtime or Telegram. One focused
commit.
