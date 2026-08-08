#!/usr/bin/env python3
"""WHIEDA Data Quality Control Plane (local, read-only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DQC_ROOT = ROOT / "qa" / "data_quality"
DEFAULT_MANIFEST = DQC_ROOT / "source_manifest.json"

sys.path.insert(0, str(DQC_ROOT))

from dqc.engine import DataQualityEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA data quality control plane")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true")
    group.add_argument("--baseline", action="store_true")
    group.add_argument("--diff", action="store_true")
    group.add_argument("--report", action="store_true")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()

    engine = DataQualityEngine(ROOT, args.manifest.resolve())
    result = engine.validate()
    payload = result["payload"]

    if args.baseline or args.all:
        engine.save_baseline_snapshot(result["baseline"])
        print(f"Baseline saved: {engine.baseline_path.relative_to(ROOT)}")

    if args.diff or args.all:
        diff = result["diff"]
        print(f"Diff status: {diff.get('status')}")
        for layer, info in (diff.get("layers") or {}).items():
            print(
                f"  {layer}: +{len(info.get('added', []))} -{len(info.get('removed', []))} ~{len(info.get('changed', []))}"
            )

    if args.report or args.all:
        engine.write_report(payload)
        print(f"Report: {engine.report_md.relative_to(ROOT)}")
        print(f"Report: {engine.report_json.relative_to(ROOT)}")

    if args.validate or args.all:
        print(f"Status: {payload['status']}")
        print(f"Errors: {payload['error_count']}  Warnings: {payload['warning_count']}")
        for item in result["missing"]:
            print(f"SOURCE MISSING: {item['layer']} -> {item['expected_path']}")

    return 0 if payload["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
