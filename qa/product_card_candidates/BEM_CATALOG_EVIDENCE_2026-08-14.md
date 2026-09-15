# BEM Catalog Evidence — 2026-08-14

Snapshot: `n8n/live-exports/structured-master/20260811T172219Z`  
Mode: read-only local investigation. No master/runtime writes.

## Verdict

**BEM is not a separate SKU in the current master.**

The conversational aliases «бэм», «бэмчик», «вэм», «magic foherb» and related forms resolve to the existing product:

| Field | Value | Source |
|-------|-------|--------|
| **Canonical SKU** | `EU-N000021-24` | `products.tsv` |
| **Canonical name** | Набор Массажёра Magic Foherb 3.0 (TUV) | `products.tsv` |
| **Retail BYN** | 2275 | `products.tsv` |
| **Partner BYN** | 1750 | `products.tsv` |
| **PV** | 500 | `products.tsv` (`partner_points`) |
| **Product card** | **Present** — approved draft 2026-07-13 | `product_cards.tsv` |
| **Primary image** | `https://mlm.sysarch.pro/whieda-media/magic-foherb.jpg` | `product_cards.tsv` |

## Alias evidence (master)

Active aliases in `aliases.tsv` pointing to `EU-N000021-24`:

| Alias | Priority | Notes |
|-------|----------|-------|
| `magic foherb` | 100 | Magic FoHerb основной латинский алиас |
| `magic foherb 3.0` | 98 | версия 3.0 |
| `бэм` | 95 | Биоэнергомассажер сокращённо |
| `биоэнергомассажер` | 90 | русский общий алиас |
| `массажер magic` | 85 | разговорный алиас |
| `бэмчик` | 85 | candidate from raw dialogues |
| `вэм` | 85 | candidate from raw dialogues |

## Historical / corpus cross-check

- `qa/telegram_golden/fixtures/snapshot_cards/bem.json` — golden fixture maps slug `bem` → `EU-N000021-24` with the same prices/card shape.
- Partner chat corpus references «БЭМ» as the Magic Foherb device and mentions compatible accessories (носки, наколенники) — not a separate catalog row.
- No row in `products.tsv`, `aliases.tsv`, or `product_cards.tsv` uses SKU or canonical name «BEM» as a standalone product.

## Recommendation for owner

1. **Do not add** a fictional BEM SKU to Product_Cards or Products.
2. **Keep** alias coverage on `EU-N000021-24` (`бэм`, `бэмчик`, `вэм` already active).
3. If business intent is to sell BEM as a distinct bundle/SKU again, **re-introduce it in master** (Products + Aliases + Product_Cards) first; until then Telegram should continue resolving BEM queries to `EU-N000021-24`.

## What was not found

- No separate BEM SKU with its own price/PV row in the 2026-08-11 structured export.
- No missing Product_Cards row for BEM — the card for `EU-N000021-24` is already approved in master.
