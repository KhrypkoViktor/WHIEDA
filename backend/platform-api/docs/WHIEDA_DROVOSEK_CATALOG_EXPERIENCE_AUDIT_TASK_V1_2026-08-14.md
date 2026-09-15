# WHIEDA Drovosek: Catalog Experience Audit V1

## Outcome

Create an owner-readable repair map for the Telegram product catalogue. The
purpose is to identify the exact data gaps that make a card look like a wall of
text or prevent a useful photo/video/certificate response. This task does not
rewrite product content and does not change runtime.

## Required reading

- `00_READ_FIRST_WHIEDA_CANON.md`
- `WHIEDA_PRODUCT_DIRECTION.md`
- `WHIEDA_HOW_WE_WRITE_AND_ADVISE.md`
- `WHIEDA_LIVE_STATUS.md`
- `qa/telegram_golden/fixtures/local_master_seed_manifest.json`
- `qa/telegram_golden/fixtures/local_master_seed.sql`
- `backend/platform-api/app/advisor/telegram_card.py`
- `backend/platform-api/app/telegram/navigation.py`

Use Git Bash. Work from the most recent valid local master snapshot in
`n8n/live-exports/structured-master/`. If no snapshot is available, stop and
report that fact; do not use runtime writes or Sheets.

## Work

### A. Catalogue coverage matrix

For every active product/SKU create one normalized row with:

- SKU and canonical product name;
- category/type where derivable;
- active aliases count and two representative aliases;
- card presence;
- primary photo presence;
- extra photo count;
- video count;
- certificate/PDF count;
- retail/partner price presence and PV presence;
- card-section coverage: `what_it_is`, `who_asks_about_it`,
  `common_use_cases`, `how_to_use_short`, `what_to_expect_soft`,
  `contraindications_short`;
- presentation grade: `showcase_ready`, `usable`, `thin`, `blocked`;
- exact missing fields and source references.

Do not interpret an intentionally empty clinical field as a failure. Grade for
Telegram presentation: a product can be `showcase_ready` without every field.

### B. Telegram renderer preview audit

Run the existing renderer for every card using fixture/master data. Do not
change `app/**`.

For each product flag only observable defects:

- empty heading or fallback label;
- repeated section/content;
- raw delimiters (`;`, `|`, literal `**`) visible in the rendered text;
- section with no useful value;
- answer exceeds Telegram-safe size;
- primary photo data missing while text claims a photo can be shown.

Store short, redacted text previews only for the 15 highest-priority products;
do not duplicate the full catalogue content into reports.

### C. Prioritized repair backlog

Build a backlog grouped by source-of-truth layer, not by invented code fixes:

1. `Product_Details` data needed for a better card;
2. `Product_Aliases` data needed for real user wording;
3. `Resources` needed for photo/video/PDF;
4. `Prices/PV` inconsistencies;
5. code-level renderer defect, only if reproducible from otherwise complete
   master data.

For every P0/P1 item include SKU, owner-visible consequence, exact source field
to fill, and evidence. Never invent product facts or write proposed medical
claims.

### D. Showcase selection

Select 12 products that could demonstrate the bot right now. Selection must
have broad categories (devices, home/wellness, consumables, cosmetics where
available) and include Activator base/PRO, BEM, Ba-Gua and Wentong when data
exists. For each give the exact test phrase to demonstrate it in Telegram.

## Deliverables

Create only these new files:

- `qa/catalog_experience/run_catalog_experience_audit.py`
- `qa/catalog_experience/CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv`
- `qa/catalog_experience/CATALOG_EXPERIENCE_BACKLOG_2026-08-14.md`
- `qa/catalog_experience/CATALOG_EXPERIENCE_SHOWCASE_2026-08-14.md`
- `backend/platform-api/docs/CATALOG_EXPERIENCE_AUDIT_LOCAL_REPORT.md`
- focused tests under `backend/platform-api/tests/` or `qa/catalog_experience/tests/`

The runner must be offline/read-only and exit non-zero only for malformed input,
not for content gaps. Reports must distinguish actual source gaps from renderer
defects.

## Hard boundaries

- Do not edit `backend/platform-api/app/**`.
- Do not edit snapshots, Sheets, Postgres, n8n workflows or production.
- Do not deploy, restart or call Telegram.
- No generated reports outside `qa/catalog_experience/` and the one local doc.
- No commits.

## Acceptance

One command produces the matrix and backlog reproducibly from the snapshot, and
an owner can pick 12 strong products for a Telegram demo without guessing.
