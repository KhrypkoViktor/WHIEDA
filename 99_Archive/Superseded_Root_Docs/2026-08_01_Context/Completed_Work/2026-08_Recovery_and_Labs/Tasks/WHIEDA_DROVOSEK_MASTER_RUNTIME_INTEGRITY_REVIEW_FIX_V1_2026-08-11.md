# WHIEDA Drovosek: Master Runtime Integrity Review Fix

## Goal

Tighten the new read-only master/runtime auditor so it distinguishes real data
loss from harmless presentation differences and duplicate TSV rows.

The current live read-only run is already useful, but it still produces false
`runtime_stale` on layers that are semantically equal after normalization.

## Hard boundaries

- Work only in `n8n/current/`, `qa/master_integrity/`, and related tests/docs.
- Do not modify production data, Google Sheets, n8n workflows, cron, Telegram,
  Dify, or website files.
- No `INSERT`, `UPDATE`, `DELETE`, DDL, sync, or reconciliation against runtime.
- Runtime access remains only through `WHIEDA_RUNTIME_READONLY_DSN`.
- No secrets, DSNs, tokens, phone numbers, raw Telegram IDs, or question texts
  in logs/reports.

## Verified facts from architect review

Real read-only checks on `2026-08-11` showed:

1. `aliases` is **not** a real missing-data case.
   - Snapshot raw rows report `120`
   - Snapshot unique business pairs `(alias, canonical_sku)` = `118`
   - Runtime unique business pairs = `118`
   - Missing = `0`, extra = `0`

2. `products`, `resources`, and `product_cards` are currently red only because
   of display-title punctuation for the same SKU / resource IDs:
   - master: `МИНИСАУНА "БА-ГУА"`
   - runtime: `МИНИСАУНА БА-ГУА`

3. This is **not** evidence of lost products/resources/cards. It is a
   normalization problem in the auditor.

## Task A — business-key parity for aliases

Fix the auditor so alias parity is based on the business identity:

- key = `(alias, canonical_sku)`
- report both:
  - `raw_row_count`
  - `unique_business_row_count`

Rules:

- If raw TSV rows differ but unique business rows match runtime exactly, do not
  mark layer `runtime_stale`.
- Report this as:
  - `in_sync` with a note, or
  - `master_review_required` only if duplicate rows in master need cleanup.

Required report fields for aliases:

- `master_raw_rows`
- `master_unique_rows`
- `runtime_unique_rows`
- `duplicate_rows_in_master`
- `duplicate_examples` capped to 10

## Task B — semantic title normalization

Add a narrow normalization layer for parity hashing and key comparison on
display titles.

The goal is **not** broad fuzzy matching. Only normalize presentation-only
noise such as:

- double quotes / angled quotes / no quotes;
- repeated internal whitespace;
- leading/trailing whitespace.

Do **not** collapse different SKUs or different resource IDs.

Apply this only where a stable business identifier already matches:

- `products`: same `sku`
- `product_cards`: same `sku`
- `resources`: same `resource_id`

Expected effect:

- `M014-00` title difference with/without quotes becomes equivalent
- the three `Ба-Гуа` image resources become equivalent

## Task C — new layer states / reasons

Keep the existing states, but add explicit reasons so the owner can tell what
kind of mismatch happened.

Examples:

- `equivalent_after_normalization`
- `master_duplicate_rows`
- `true_runtime_missing_ids`
- `true_runtime_extra_ids`
- `true_content_mismatch_after_normalization`

If a layer matches after normalization, final layer `status` should be
`in_sync`, not `runtime_stale`.

## Task D — tests

Add tests covering:

1. alias raw rows 120 vs unique rows 118 vs runtime 118 → not stale;
2. duplicated alias rows in master are surfaced in report;
3. quoted vs unquoted `БА-ГУА` title → equivalent after normalization;
4. same `resource_id` with quoted/unquoted title → equivalent;
5. genuinely different title after normalization still remains mismatch;
6. existing true stale / extra / missing cases still fail correctly.

## Deliverables

1. Updated read-only auditor and library
2. Updated tests
3. Updated local report
4. Focused commit(s)

## Acceptance

On the same live snapshot/runtime pair used today:

- `aliases` must no longer show false `runtime_stale`
- `products`, `resources`, and `product_cards` must become `in_sync` if the
  only difference is quoted/unquoted `БА-ГУА`
- no live writes, no deploy, no cron, no runtime mutations
