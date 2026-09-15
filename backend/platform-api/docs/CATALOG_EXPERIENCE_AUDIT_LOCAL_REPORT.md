# Catalog Experience Audit — Local Report

Date: 2026-08-14
Task: `WHIEDA_DROVOSEK_CATALOG_EXPERIENCE_AUDIT_TASK_V1_2026-08-14.md`

## Summary

- Snapshot: `20260811T172219Z` (captured 2026-08-11T17:22:19.486277+00:00)
- Products audited: **40**
- Cards present: **23** / missing: **17**
- Grades: showcase_ready=23, usable=0, thin=0, blocked=17
- Renderer defects on otherwise complete cards: **0**
- Backlog items: **35**

## Key findings

1. **Card coverage gap (17 SKUs):** products exist in `products.tsv` but have no `product_cards.tsv` row — Telegram cannot render a structured card.
2. **Product_Details layer empty:** `product_details.tsv` has zero rows; all presentation comes from Product_Cards.
3. **Existing 23 cards render cleanly:** no literal `**`, no Telegram size overflows; longest card ~1730 chars (Activator base).
4. **Price gaps on accessories:** T001/T002/T003 have dash partner BYN in products.tsv.
5. **BEM not in catalogue:** no SKU/name match in snapshot; cannot include in showcase.

## Artifacts

- `qa/catalog_experience/run_catalog_experience_audit.py`
- `qa/catalog_experience/CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv`
- `qa/catalog_experience/CATALOG_EXPERIENCE_BACKLOG_2026-08-14.md`
- `qa/catalog_experience/CATALOG_EXPERIENCE_SHOWCASE_2026-08-14.md`

## Command

```bash
cd /d/Projects/WHIEDA
python qa/catalog_experience/run_catalog_experience_audit.py
```

## Showcase selection

1. `активатор` → Активатор клеток (M015-00, showcase_ready)
2. `активатор pro` → Активатор клеток PRO (комплект) (EU-N000031-25, showcase_ready)
3. `ба-гуа` → МИНИСАУНА "БА-ГУА" (M014-00, showcase_ready)
4. `вэнтун` → Набор прибора Вэнтун 1.0 (EU-N000024-24, showcase_ready)
5. `спирулина` → Спирулина (F036-00, showcase_ready)
6. `стельки` → Стельки с анионами (D013, showcase_ready)
7. `паста цинфэн` → Паста Цинфэн (F071-00, showcase_ready)
8. `magic foherb` → Набор Массажёра Magic Foherb 3.0 (TUV) (EU-N000021-24, showcase_ready)
9. `прокладки` → Анионовые прокладки (Бокс 19 шт) (D003-00, showcase_ready)
10. `палантин` → Палантин (T015, showcase_ready)
11. `очки` → Высокотехнологичные компьютерные очки (D014, showcase_ready)
12. `соевый пептид` → Низкомолекулярный соевый пептид (F038-00, showcase_ready)

No production, Sheets, Postgres, or runtime changes were made.
