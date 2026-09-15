# Company Knowledge Pack — Review Fix Local Report

V1.1 layer split: public company / partner network / platform internal. **Not wired to runtime.**

- Snapshot: `20260811T172219Z`
- Lint: PASS

## Audience layers

| Layer | Facts | Questions |
| --- | ---: | ---: |
| `company_public` | 11 | 19 |
| `partner_network` | 24 | 21 |
| `platform_internal` | 9 | 8 |
| `pending_owner` (rollup) | — | — |

Pending-owner rollup (facts/questions/public replies): **23**

## New artifacts

- Public reply intents: 6
- Partner network contacts: 10

## Block E checks

- Accepted `company_public` facts require non-empty text and source.
- `platform_internal` and `partner_network` facts excluded from public reply TSV.
- Pending-owner public replies have empty `answer_text`.
- V1 `fact_id` registry preserved.

## Reproduce

```bash
python qa/company_knowledge/build_company_knowledge_pack.py
python qa/company_knowledge/run_company_knowledge_pack.py --offline
python -m pytest backend/platform-api/tests/test_company_knowledge_pack.py -q
```
