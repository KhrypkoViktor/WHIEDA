#!/usr/bin/env python3
"""Read-only WHIEDA asset inventory — generates manifest and feature maps."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

INVENTORY_ROOT = Path(__file__).resolve().parent
QA_ROOT = INVENTORY_ROOT.parent
REPO_ROOT = QA_ROOT.parent
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from inventory.scanner import run_inventory_scan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA read-only asset inventory scan")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT,
        help="Directory for CSV/MD reports (default: repo root)",
    )
    args = parser.parse_args()

    print("=== WHIEDA asset inventory (read-only) ===")
    print(f"Output dir: {args.output_dir.resolve()}")

    result = run_inventory_scan(args.output_dir)

    print(f"Manifest: {result.manifest_path}")
    print(f"Feature matrix: {result.feature_matrix_path}")
    print("Data map:", result.data_map_path)
    print(f"Logical sources: {len(result.manifest_rows)}")
    print(f"Checks passed: {result.checks_passed}")

    if result.warnings:
        print("Warnings:")
        for warn in result.warnings:
            print(f"  - {warn}")

    if result.errors:
        print("ERRORS:", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("=== INVENTORY SCAN: PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
