# Task: Golden Local Master Parity and Runner Fix V1

Исполнитель: Дровосек  
Дата: 2026-08-13  
Основание: Golden HTTP report `20260812T132043Z-a3eb6744`.

## Вердикт до начала работ

Текущие `38/107` P0 failures нельзя называть 38 дефектами Core. Golden corpus
создан из реального master snapshot, но local Core lab поднимается на маленьком
synthetic seed (`postgres/scripts/staging_seed_whieda_advisor_local_v1.sql`).
В нём нет части golden SKU/aliases/cards/resources. Это fixture parity gap.

Задача: сделать честный local master-parity fixture и исправить harness, чтобы
после следующего прогона отчёт показывал **реальные** Core defects, а не
отсутствующие строки seed.

## Жёсткие границы

Разрешено:

- `qa/telegram_golden/**`;
- `postgres/scripts/ensure_local_core_database.py`;
- `backend/platform-api/scripts/local_core_lab/**` и runner hook;
- тесты и docs только для этой local lab.

Запрещено:

- `backend/platform-api/app/**`;
- production / SSH / n8n / Sheets writes;
- runtime PostgreSQL / migration schemas;
- замена обычного synthetic seed по умолчанию;
- изменение Golden expected или owner-locked texts ради PASS.

## A. Deterministic master-parity fixture

### Source (read-only)

Только snapshot:

`n8n/live-exports/structured-master/20260810T083328Z/`

Использовать manifest SHA-256. Если manifest/required TSV отсутствует или hash
не совпадает, builder должен abort до генерации seed.

### New files

```text
qa/telegram_golden/compile_local_master_seed.py
qa/telegram_golden/fixtures/local_master_seed.sql
qa/telegram_golden/fixtures/local_master_seed_manifest.json
```

Seed is **local fixture**, not production migration. It must be generated
deterministically from TSV and committed only if it contains no secrets.

### Data to load

For tenant/client `whieda`, compile all valid rows needed by Golden:

1. `products.tsv` -> `advisor_structured_products` (SKU + every available
   numeric price/PV/W$ independently; missing price stays NULL);
2. `aliases.tsv` -> `advisor_structured_aliases` (dedupe by alias + canonical SKU);
3. `product_cards.tsv` -> `advisor_structured_product_cards`;
4. `resources.tsv` -> `advisor_structured_resources` (active resources only);
5. `business_faq.tsv`, `business_objections.tsv`, `capability_responses.tsv`,
   `clarification_prompts.tsv`, `product_comparisons.tsv`,
   `starter_basket_templates.tsv`, `promotions.tsv`, `events.tsv`,
   `community_resources.tsv` where source tables map to existing local schema.

Do not invent mappings. If a TSV field/table cannot map to existing local
schema, record it under `unmapped_source_layers` in the fixture manifest and
do not silently substitute synthetic data.

Requirements:

- SQL literals safely escaped; no shell SQL;
- `BEGIN/COMMIT`, idempotent upsert only for tenant `whieda`;
- never DELETE existing synthetic fixtures;
- source row count, accepted/deduped/rejected count and SHA-256 in manifest;
- row-level reject reason with source locator (not raw sensitive content).

## B. Explicit local Core Lab mode

Add one opt-in flag:

```powershell
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
```

Rules:

- default lab remains exactly as before with synthetic `staging_seed...`;
- only `--golden-master-seed` runs builder then applies generated fixture after
  existing schema + synthetic seed;
- generated fixture applies only to local DB `whieda_platform_local_core` on
  local Docker port `55432` through existing guarded flow;
- command prints fixture manifest identity and source snapshot id;
- no flag -> no master fixture application;
- Docker unavailable -> honest NOT_RUN.

## C. Fix Golden runner completeness

Current bugs:

1. `--priority P0` does `flows=[]`, so P0 flow turns/context are skipped.
2. Local lab stops after P0 fail, so full corpus and negative fixtures never
   reveal their actual state.

Fix:

- priority run includes every whole flow that has at least one turn of selected
  priority; setup turns execute even if another priority;
- report identifies setup turns vs selected assertion turns;
- local lab sequence: target check -> P0 smoke -> full positive -> negative,
  regardless of P0 result;
- the final lab status is FAIL if any phase fails, but later phases must still
  run unless infrastructure itself cannot start;
- no `PASS` may be inferred from skipped phase.

## D. Tests

Add tests proving:

1. compiler refuses missing/hash-invalid snapshot;
2. compiler deterministic: two runs byte-identical seed + manifest;
3. every Golden snapshot SKU resolves to a local seed product/card;
4. aliases/resources/cards are tenant-safe and idempotent;
5. source price NULL stays NULL, not zero;
6. default lab does not apply master fixture;
7. opt-in applies only local fixture;
8. P0 priority retains complete relevant flow context;
9. full and negative phases execute after P0 assertion failure;
10. report labels fixture parity prerequisites and does not expose full text/secrets.

## Acceptance

Run the commands from Bash (Git Bash or WSL Bash), not PowerShell. The repo has
paths and pytest globs that are less error-prone there. Every documented command
must also work as pasted in Bash from the repository root.

```powershell
cd D:\Projects\WHIEDA
python qa\telegram_golden\compile_local_master_seed.py
python qa\telegram_golden\run_telegram_golden.py --offline
python -m pytest backend\platform-api\tests\test_telegram_golden*.py -q
python backend\platform-api\scripts\run_local_core_lab.py --e2e --telegram-golden --golden-master-seed
```

Deliver:

1. focused commit;
2. report `TELEGRAM_GOLDEN_LOCAL_MASTER_PARITY_REPORT.md`;
3. before/after table: fixture missing vs actual Core failures;
4. P0/full/negative all executed results;
5. no Core fixes in this task. List actual Core failures by case id for the
   Core owner.
