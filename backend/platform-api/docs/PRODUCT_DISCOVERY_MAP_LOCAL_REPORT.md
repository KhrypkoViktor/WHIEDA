# Product Discovery Map — Local Report

Mode: read-only QA artifact for Core owner review. **Not wired into runtime.**

- Source snapshot: `20260811T172219Z`
- products sha256: `1f71422a956f63350063223513fec1a0b3a65eb8773bb58bebecb1c6b658da83`
- aliases sha256: `3a58c76240c362e203bbb18eade7d486f14b5a02cd8120b6af5e17da2444e5a0`
- product_cards sha256: `db30e469fa85cf8ed522b582a65d916747fe25c9b2577f666469181a68c66dfb`
- resources sha256: `d9f0ac626da3414db4e9593819e7345efa3019a811ca6a6442cbfbf48e4c82e6`
- Discovery phrases: 35
- Candidate rows: 47
- Discovery groups: 9
- Lint: PASS

- HLR live report: `HLR_HTTP_REPORT_20260814T154952Z-f5e9841f.json`

## Status breakdown

- `do_not_resolve`: 3
- `generic_category`: 17
- `needs_owner_review`: 5
- `ready_for_core_review`: 22

## Confidence breakdown

- `high`: 16
- `low`: 6
- `medium`: 25

## Artifacts

- `qa/product_discovery/PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv`
- `qa/product_discovery/PRODUCT_DISCOVERY_RESOLUTION_POLICY_V1.md`
- `qa/product_discovery/HLR_DISCOVERY_TRIAGE_V1.md`

## Reproduce

```bash
python qa/product_discovery/build_product_discovery_map.py
python qa/product_discovery/run_product_discovery_map.py --offline
python -m pytest backend/platform-api/tests/test_product_discovery_map.py -q
```
