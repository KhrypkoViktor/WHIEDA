"""Tests for quality rules registry coverage enforcement."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DQC = ROOT / "qa" / "data_quality"
VERIFY = DQC / "verify_rules_coverage.py"
REGISTRY = DQC / "quality_rules_registry.json"
TEST_PLANE = ROOT / "backend" / "platform-api" / "tests" / "data_quality" / "test_data_quality_plane.py"


def test_rules_registry_loads():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert len(data["rules"]) >= 15
    ids = [r["rule_id"] for r in data["rules"]]
    assert len(ids) == len(set(ids))


def test_verify_rules_coverage_passes():
    proc = subprocess.run([sys.executable, str(VERIFY)], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Rules coverage status: PASS" in proc.stdout


def test_verify_rules_coverage_detects_missing_pytest(tmp_path: Path):
    sys.path.insert(0, str(DQC))
    from dqc.rules_coverage import verify_coverage

    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    broken = dict(reg)
    broken["rules"] = [dict(r, pytest_ref="missing_test_fn") for r in reg["rules"] if r["rule_id"] == "dup_sku"]
    if not broken["rules"]:
        broken["rules"] = reg["rules"]
        broken["rules"][0] = dict(broken["rules"][0], rule_id="dup_sku", status="active", fixture_scenario="dup_sku")
    broken_path = tmp_path / "broken_registry.json"
    broken_path.write_text(json.dumps(broken), encoding="utf-8")

    # Use full registry but simulate missing pytest by pointing to empty test file
    empty_test = tmp_path / "empty_test.py"
    empty_test.write_text("def test_other(): pass\n", encoding="utf-8")
    result = verify_coverage(
        registry_path=REGISTRY,
        dqc_root=DQC,
        fixtures_root=DQC / "fixtures",
        test_path=empty_test,
    )
    assert result["status"] == "FAIL"
    assert result["missing_pytest"]
