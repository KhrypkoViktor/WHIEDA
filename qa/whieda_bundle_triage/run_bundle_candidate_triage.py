#!/usr/bin/env python3
"""Offline bundle-candidate triage. Writes qa/whieda_bundle_triage artifacts only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from triage import (  # noqa: E402
    UNKNOWN_COLUMNS,
    TRIAGE_COLUMNS,
    default_aliases_path,
    default_input_path,
    default_products_path,
    load_catalog,
    load_rows,
    triage_rows,
    write_jsonl,
    write_report,
    write_tsv,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Local files only; never publish or call live services.",
    )
    parser.add_argument("--input", type=Path, help="TSV with staging/distillate rows")
    parser.add_argument("--aliases", type=Path, default=None)
    parser.add_argument("--products", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=HERE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.offline:
        print("refusing to run without --offline; this tool never publishes", file=sys.stderr)
        return 2
    source = args.input if args.input is not None else default_input_path()
    if not source.is_file():
        print(f"input file not found: {source}", file=sys.stderr)
        return 2
    aliases = args.aliases or default_aliases_path()
    products = args.products if args.products is not None else default_products_path()
    catalog = load_catalog(aliases_path=aliases, products_path=products)
    rows = load_rows(source)
    result = triage_rows(rows, catalog, source_path=source)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(out_dir / "bundle_candidate_triage_v1.tsv", result.triage, TRIAGE_COLUMNS)
    write_tsv(out_dir / "unknown_item_resolution_v1.tsv", result.unknown, UNKNOWN_COLUMNS)
    write_jsonl(out_dir / "active_bundle_regression_cases_v1.jsonl", result.regression)
    write_report(out_dir / "BUNDLE_CANDIDATE_TRIAGE_LOCAL_REPORT.md", result)
    print(f"read {result.rows_read} rows from {source}")
    print("counts:", result.counts)
    if result.source_missing_note:
        print(result.source_missing_note)
    print(f"wrote artifacts under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
