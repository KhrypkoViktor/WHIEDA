#!/usr/bin/env python3
"""Compile local master-parity SQL fixture from immutable structured-master snapshot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = Path(__file__).resolve().parent / "lab"
DEFAULT_SNAPSHOT = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"
DEFAULT_SQL = Path(__file__).resolve().parent / "fixtures" / "local_master_seed.sql"
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "fixtures" / "local_master_seed_manifest.json"


def _load_compiler():
    spec = importlib.util.spec_from_file_location("master_seed_compiler", LAB / "master_seed_compiler.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile golden local master-parity seed")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--sql-out", type=Path, default=DEFAULT_SQL)
    parser.add_argument("--manifest-out", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    compiler = _load_compiler()
    try:
        manifest = compiler.compile_master_seed(
            snapshot_dir=args.snapshot,
            sql_out=args.sql_out,
            manifest_out=args.manifest_out,
        )
    except compiler.MasterSeedCompileError as exc:
        print(f"COMPILE_ABORT: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(
        {
            "status": "PASS",
            "sql_out": str(args.sql_out),
            "manifest_out": str(args.manifest_out),
            "snapshot": manifest.get("generated_from"),
            "fixture_sql_sha256": manifest.get("fixture_sql_sha256"),
            "products_accepted": manifest["layer_stats"]["products"]["accepted"],
            "aliases_accepted": manifest["layer_stats"]["aliases"]["accepted"],
            "cards_accepted": manifest["layer_stats"]["product_cards"]["accepted"],
            "resources_accepted": manifest["layer_stats"]["resources"]["accepted"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
