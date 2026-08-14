#!/usr/bin/env python3
"""Build PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv from master snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG))

from builder import build_rows  # noqa: E402
from lint import lint_rows  # noqa: E402
from snapshot_loader import load_bundle  # noqa: E402
from tsv_io import write_rows  # noqa: E402

OUT_TSV = PKG / "PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv"
META_JSON = PKG / "PRODUCT_DISCOVERY_MAP_META.json"


def main() -> int:
    argp = argparse.ArgumentParser(description="Build product discovery map candidates")
    argp.add_argument("--snapshot", type=Path, default=None)
    argp.add_argument("--out", type=Path, default=OUT_TSV)
    args = argp.parse_args()

    bundle = load_bundle(args.snapshot)
    rows = [row.as_dict() for row in build_rows(bundle)]
    issues = lint_rows(rows, bundle)
    if issues:
        for issue in issues:
            print(f"LINT {issue.code}: {issue.message}", file=sys.stderr)
        return 1

    write_rows(args.out, rows)
    meta = {
        "snapshot_id": bundle.snapshot_id,
        "layer_hashes": {
            "products": bundle.layer_hashes.get("products", ""),
            "aliases": bundle.layer_hashes.get("aliases", ""),
            "product_cards": bundle.layer_hashes.get("product_cards", ""),
            "resources": bundle.layer_hashes.get("resources", ""),
        },
        "phrase_count": len({r["phrase"] for r in rows}),
        "row_count": len(rows),
        "status_counts": {},
    }
    for row in rows:
        meta["status_counts"][row["status"]] = meta["status_counts"].get(row["status"], 0) + 1
    META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {args.out} ({len(rows)} rows, snapshot {bundle.snapshot_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
