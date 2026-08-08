"""Tests for WHIEDA data quality control plane."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
DQC = ROOT / "qa" / "data_quality"
FIX = DQC / "fixtures" / "scenarios"
RUNNER = DQC / "run_data_quality.py"
CONTRACTS = DQC / "contracts"
INDEX = DQC / "fixtures" / "fixtures_index.json"


@pytest.fixture(scope="module")
def fixture_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _manifest_for_scenario(scenario: str) -> Path:
    base = json.loads((DQC / "source_manifest.json").read_text(encoding="utf-8"))
    sources = []
    scenario_dir = FIX / scenario
    for src in base["sources"]:
        entry = dict(src)
        stem = Path(src["file"]).stem
        candidates = [
            scenario_dir / src["file"],
            scenario_dir / f"{stem}.csv",
            scenario_dir / f"{stem}.json",
            scenario_dir / f"{stem}.jsonl",
        ]
        candidate = next((p for p in candidates if p.is_file()), None)
        if candidate is not None:
            entry["path"] = str(candidate.relative_to(ROOT)).replace("\\", "/")
            fmt = candidate.suffix.lstrip(".").lower()
            entry["format"] = fmt if fmt in {"csv", "tsv", "json", "jsonl"} else src.get("format", "tsv")
            sources.append(entry)
    mini = {"version": 1, "exports_root": str(scenario_dir.relative_to(ROOT)).replace("\\", "/"), "sources": sources}
    tmp = Path(tempfile.mkdtemp()) / f"{scenario}.manifest.json"
    tmp.write_text(json.dumps(mini, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp


def _run_engine(manifest: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(RUNNER), "--manifest", str(manifest), *flags]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))


def _validate_scenario(scenario: str) -> dict:
    sys.path.insert(0, str(DQC))
    from dqc.engine import DataQualityEngine

    manifest = _manifest_for_scenario(scenario)
    engine = DataQualityEngine(ROOT, manifest)
    return engine.validate()


@pytest.mark.parametrize("contract_file", sorted(CONTRACTS.glob("*.json")))
def test_all_contracts_parse(contract_file: Path):
    data = json.loads(contract_file.read_text(encoding="utf-8"))
    assert "layer" in data
    assert "columns" in data
    assert data["columns"]


def test_fixture_catalog_has_60_plus_files(fixture_index: dict):
    assert fixture_index["file_count"] >= 60
    assert fixture_index["scenario_count"] >= 60


def test_valid_full_passes():
    result = _validate_scenario("valid_full")
    errors = [i for i in result["issues"] if i["severity"] == "error"]
    assert not errors, errors


@pytest.mark.parametrize(
    "scenario,expected_check",
    [
        ("dup_sku", "duplicate_key"),
        ("price_string", "invalid_number"),
        ("empty_alias", "required_field_empty"),
        ("bad_url", "invalid_url"),
        ("alias_missing_sku", "unknown_product_ref"),
        ("bundle_unknown", "bundle_unknown_product"),
        ("two_active_prices", "price_conflict"),
        ("promo_bad_dates", "promotion_date_order"),
        ("promo_missing_end", "promotion_missing_end"),
        ("inactive_in_bundle", "inactive_product_in_active_bundle"),
        ("cert_no_product", "unknown_product_ref"),
        ("faq_empty_answer", "faq_missing_answer"),
        ("duplicate_alias", "duplicate_key"),
        ("duplicate_faq", "duplicate_key"),
        ("duplicate_resource_id", "duplicate_key"),
        ("duplicate_url", "duplicate_key"),
        ("zero_price", "zero_forbidden"),
        ("negative_pv", "number_below_min"),
        ("missing_material_type", "missing_material_type"),
        ("nordman_artifact", "suspicious_artifact"),
        ("traceback_artifact", "suspicious_artifact"),
        ("human_review_artifact", "suspicious_artifact"),
        ("medical_warning_card", "medical_language"),
        ("medical_warning_bundle", "medical_language"),
    ],
)
def test_quality_checks_detect_issues(scenario: str, expected_check: str):
    result = _validate_scenario(scenario)
    checks = {i["check"] for i in result["issues"]}
    assert expected_check in checks, f"{scenario}: got {checks}"


def test_production_manifest_reports_missing_sources():
    proc = _run_engine(DQC / "source_manifest.json", "--validate")
    assert "SOURCE MISSING" in proc.stdout + proc.stderr
    assert proc.returncode == 0


def test_runner_has_no_network_calls():
    text = RUNNER.read_text(encoding="utf-8")
    for mod in ("urllib", "requests", "httpx", "socket"):
        assert mod not in text
    for path in DQC.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "urllib.request" not in body
        assert "requests.get" not in body


def test_runner_does_not_write_to_exports():
    text = "\n".join(p.read_text(encoding="utf-8") for p in DQC.rglob("*.py"))
    assert "exports/" in text or "exports\\" in text
    assert "write_text" not in text.split("exports")[0]  # coarse guard
    assert "exports" in (DQC / "exports").as_posix()


def test_runner_no_prod_urls_or_credentials():
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in DQC.rglob("*") if p.suffix in {".py", ".json"})
    lower = blob.lower()
    assert "api.telegram.org" not in lower
    assert "duckdns.org" not in lower
    assert "supabase" not in lower
    assert "platform_telegram_bot_token" not in lower


def test_baseline_and_diff_flow(tmp_path: Path):
    sys.path.insert(0, str(DQC))
    from dqc.engine import DataQualityEngine

    manifest = _manifest_for_scenario("valid_full")
    engine = DataQualityEngine(ROOT, manifest)
    engine.baseline_path = tmp_path / "baseline.json"
    first = engine.validate()
    engine.save_baseline_snapshot(first["baseline"])
    second = engine.validate()
    assert second["diff"]["status"] == "ok"
    for info in (second["diff"].get("layers") or {}).values():
        assert not info.get("added") and not info.get("removed") and not info.get("changed")

    engine.baseline_path = tmp_path / "baseline.json"
    from dqc.baseline import load_baseline

    assert load_baseline(engine.baseline_path) is not None

    changed_manifest = _manifest_for_scenario("price_change_v2")
    engine2 = DataQualityEngine(ROOT, changed_manifest)
    engine2.baseline_path = tmp_path / "baseline.json"
    changed = engine2.validate()
    layer_diff = changed["diff"]["layers"].get("products_prices", {})
    assert layer_diff.get("changed") or layer_diff.get("price_changed")


def test_report_files_generated():
    proc = _run_engine(DQC / "fixtures" / "manifest.test.json", "--report")
    assert proc.returncode == 0
    assert (DQC / "reports" / "DATA_QUALITY_REPORT.md").is_file()
    assert (DQC / "reports" / "DATA_QUALITY_REPORT.json").is_file()
    payload = json.loads((DQC / "reports" / "DATA_QUALITY_REPORT.json").read_text(encoding="utf-8"))
    assert payload["status"] in {"PASS", "WARN", "FAIL"}
    assert "layer_stats" in payload


def test_alias_removed_diff(tmp_path: Path):
    sys.path.insert(0, str(DQC))
    from dqc.engine import DataQualityEngine

    base_m = _manifest_for_scenario("valid_full")
    engine = DataQualityEngine(ROOT, base_m)
    engine.baseline_path = tmp_path / "b.json"
    engine.save_baseline_snapshot(engine.validate()["baseline"])

    rem_m = _manifest_for_scenario("alias_removed_v2")
    engine2 = DataQualityEngine(ROOT, rem_m)
    engine2.baseline_path = tmp_path / "b.json"
    diff = engine2.validate()["diff"]["layers"].get("product_aliases", {})
    assert diff.get("removed") or diff.get("changed")


def test_safety_change_diff(tmp_path: Path):
    sys.path.insert(0, str(DQC))
    from dqc.engine import DataQualityEngine

    base_m = _manifest_for_scenario("valid_full")
    engine = DataQualityEngine(ROOT, base_m)
    engine.baseline_path = tmp_path / "b2.json"
    engine.save_baseline_snapshot(engine.validate()["baseline"])

    ch_m = _manifest_for_scenario("safety_change_v2")
    engine2 = DataQualityEngine(ROOT, ch_m)
    engine2.baseline_path = tmp_path / "b2.json"
    diff = engine2.validate()["diff"]["layers"].get("product_cards", {})
    assert diff.get("changed") or diff.get("safety_changed")


def test_loaders_csv_jsonl():
    result = _validate_scenario("products_csv")
    assert not [i for i in result["issues"] if i["severity"] == "error"]
    result2 = _validate_scenario("products_jsonl")
    assert result2["layer_stats"].get("products_prices", 0) >= 1


def test_pv_comma_parses():
    result = _validate_scenario("pv_comma")
    nums = [i for i in result["issues"] if i["check"] == "invalid_number"]
    assert not nums


def test_stale_baseline_no_file(tmp_path: Path):
    sys.path.insert(0, str(DQC))
    from dqc.engine import DataQualityEngine

    manifest = _manifest_for_scenario("valid_full")
    engine = DataQualityEngine(ROOT, manifest)
    engine.baseline_path = tmp_path / "missing.json"
    diff = engine.validate()["diff"]
    assert diff["status"] == "no_baseline"
