# WWC Markets Google Sheets import template

Generated from the live site catalog (`03_Website/wwc-best/src/data/products.js`).

## Files

| File | Tab name in Google Sheets |
|------|---------------------------|
| `WWC_MARKETS_SHEETS_TEMPLATE.xlsx` | all five tabs in one workbook |
| `markets.csv` | `markets` |
| `ref_structures.csv` | `ref_structures` |
| `service_centers.csv` | `service_centers` |
| `service_center_coverage.csv` | `service_center_coverage` |
| `product_prices.csv` | `product_prices` |

## Regenerate after price changes on site

```bash
python backend/platform-api/scripts/generate_wwc_markets_sheets_template.py
```

Requires `openpyxl` for `.xlsx` output (`pip install openpyxl`).

Owner setup: `backend/platform-api/docs/WWC_MARKETS_GOOGLE_SHEETS_OWNER_SETUP.md`
