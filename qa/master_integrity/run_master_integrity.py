#!/usr/bin/env python3
"""Master integrity and backup local verification."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "n8n" / "current"
PLATFORM = ROOT / "backend" / "platform-api"
sys.path.insert(0, str(N8N))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_fixtures import ensure_all_fixtures  # noqa: E402
from whieda_master_integrity_lib import (  # noqa: E402
    compare_master_to_runtime,
    compare_snapshots,
    redact_sensitive_text,
    retention_plan,
    validate_snapshot_dir,
)

BASELINE = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"


def _pytest() -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(PLATFORM / "tests" / "test_master_integrity.py"), "-q"],
        cwd=str(PLATFORM),
    ).returncode


def main() -> int:
    rc = _pytest()
    if rc != 0:
        print("pytest: FAIL")
        return rc
    print("pytest: PASS")

    fixtures = ensure_all_fixtures()
    valid = fixtures / "valid"
    results: dict[str, str] = {}

    validation = validate_snapshot_dir(valid)
    results["valid_20_layer"] = "PASS" if validation["valid"] else "FAIL"

    optional = validate_snapshot_dir(valid)
    details_rows = optional["layers"]["product_details"]["rows"] if optional["valid"] else -1
    results["empty_optional_product_details"] = "PASS" if details_rows == 0 else "FAIL"

    missing = validate_snapshot_dir(fixtures / "missing_layer")
    results["missing_layer_blocking"] = "PASS" if not missing["valid"] else "FAIL"

    invalid = validate_snapshot_dir(fixtures / "invalid_tsv")
    results["invalid_tsv_blocking"] = "PASS" if not invalid["valid"] else "FAIL"

    drift = compare_snapshots(valid, fixtures / "collapsed_products")
    results["critical_collapse"] = "PASS" if drift["layers"]["products"]["classification"] == "blocking" else "FAIL"

    header_drift = compare_snapshots(valid, fixtures / "header_mutation")
    results["header_mutation"] = (
        "PASS" if header_drift["layers"]["aliases"]["classification"] == "review_required" else "FAIL"
    )

    retention = retention_plan(fixtures)
    results["retention_dry_run"] = "PASS" if "would_remove" in retention and "removed" not in retention else "FAIL"

    fake_dsn = "postgresql://user:secret-pass@db.example.com:5432/whieda"
    redacted = redact_sensitive_text(f"connect failed {fake_dsn}")
    results["dsn_redaction"] = "PASS" if "secret-pass" not in redacted and "REDACTED" in redacted else "FAIL"

    runtime_report = compare_master_to_runtime(
        valid,
        {
            "products": {"row_count": 20, "content_hash": "wrong-hash"},
            "aliases": {"row_count": 60, "content_hash": "def"},
        },
    )
    results["runtime_parity_fake"] = (
        "PASS" if runtime_report["layers"]["products"]["status"] == "stale_runtime" else "FAIL"
    )

    if BASELINE.is_dir():
        baseline_validation = validate_snapshot_dir(BASELINE, strict_files=False)
        results["architect_baseline_valid"] = "PASS" if baseline_validation["valid"] else "FAIL"
        if baseline_validation["valid"]:
            same = compare_snapshots(BASELINE, BASELINE)
            results["baseline_self_compare"] = (
                "PASS" if same["overall_classification"] == "expected_content_change" else "FAIL"
            )
    else:
        results["architect_baseline_valid"] = "SKIP"

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    failed = [name for name, status in results.items() if status not in {"PASS", "SKIP"}]
    if failed:
        print(f"MASTER_INTEGRITY: FAIL ({', '.join(failed)})")
        return 1
    print("MASTER_INTEGRITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
