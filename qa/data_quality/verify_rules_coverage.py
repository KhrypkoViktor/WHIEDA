#!/usr/bin/env python3
"""Verify data quality rule registry coverage (fixtures + pytest)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DQC_ROOT = ROOT / "qa" / "data_quality"
REGISTRY = DQC_ROOT / "quality_rules_registry.json"
FIXTURES = DQC_ROOT / "fixtures"
TEST_PATH = ROOT / "backend" / "platform-api" / "tests" / "data_quality" / "test_data_quality_plane.py"

sys.path.insert(0, str(DQC_ROOT))

from dqc.rules_coverage import format_report, verify_coverage  # noqa: E402


def main() -> int:
    result = verify_coverage(
        registry_path=REGISTRY,
        dqc_root=DQC_ROOT,
        fixtures_root=FIXTURES,
        test_path=TEST_PATH,
    )
    print(format_report(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
