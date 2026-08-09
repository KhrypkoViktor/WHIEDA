"""Pytest guards against false Core parity readiness claims."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PARITY = ROOT / "qa" / "parity"
ACCEPTANCE = ROOT / "qa" / "acceptance"
CORPUS = PARITY / "core_local_parity_cases_v2.jsonl"
MATRIX = PARITY / "CORE_ADVISOR_CAPABILITY_MATRIX_V2.json"
RUNNER = PARITY / "run_core_local_parity.py"
ENGINE = ROOT / "backend" / "platform-api" / "app" / "advisor" / "sql" / "engine.py"
FORMAT_PRICE = ROOT / "backend" / "platform-api" / "app" / "advisor" / "sql" / "formatters.py"


def _load_cases() -> list[dict]:
    lines = [ln for ln in CORPUS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _load_matrix() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def _load_parity_module(name: str, rel: str):
    path = PARITY / "lab" / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_corpus_has_at_least_80_cases():
    cases = _load_cases()
    assert len(cases) >= 80


def test_corpus_p0_at_least_30():
    cases = _load_cases()
    p0 = sum(1 for c in cases if c.get("priority") == "P0")
    assert p0 >= 30


def test_corpus_p1_at_least_35():
    cases = _load_cases()
    p1 = sum(1 for c in cases if c.get("priority") == "P1")
    assert p1 >= 35


def test_corpus_p2_at_least_15():
    cases = _load_cases()
    p2 = sum(1 for c in cases if c.get("priority") == "P2")
    assert p2 >= 15


def test_every_capability_has_case():
    matrix = _load_matrix()
    caps = {c["capability_id"] for c in matrix["capabilities"]}
    case_caps = {c.get("capability_id") for c in _load_cases()}
    missing = caps - case_caps
    assert not missing, f"capabilities without cases: {missing}"


def test_cases_have_required_assertion_fields():
    for case in _load_cases():
        if case.get("send_invalid_json") or case.get("expect_http_status"):
            continue
        assert case.get("must_contain"), case["case_id"]
        assert case.get("expected_mode") or case.get("expect_http_status"), case["case_id"]
        assert case.get("expected_media") is not None, case["case_id"]
        assert case.get("max_latency_ms") is not None, case["case_id"]
        assert case.get("capability_id"), case["case_id"]


def test_no_duplicate_input_context_session():
    seen: set[tuple] = set()
    for case in _load_cases():
        key = (
            case.get("input"),
            json.dumps(case.get("context_before") or [], ensure_ascii=False),
            case.get("session"),
            case.get("host"),
        )
        assert key not in seen, f"duplicate case key {case.get('case_id')}"
        seen.add(key)


def test_localhost_guard_rejects_external():
    mod = _load_parity_module("parity_check_target", "check_target.py")
    with pytest.raises(ValueError, match="Refusing"):
        mod.validate_local_target("https://wwc.best")


def test_localhost_guard_accepts_8080():
    mod = _load_parity_module("parity_check_target", "check_target.py")
    mod.validate_local_target("http://127.0.0.1:8080")


def test_baseline_gate_blocks_without_flag():
    mod = _load_parity_module("parity_baseline_gate", "baseline_gate.py")
    payload = {"summary": {"fail": 0, "p0_fail": 0, "unasserted": 0}}
    ok, reason = mod.evaluate_baseline_acceptance(payload, dry_run=False, accept_baseline=False, local_target_ok=True)
    assert not ok
    assert "accept-baseline" in reason


def test_baseline_gate_blocks_with_failures():
    mod = _load_parity_module("parity_baseline_gate", "baseline_gate.py")
    payload = {"summary": {"fail": 1, "p0_fail": 1, "unasserted": 0}}
    ok, _ = mod.evaluate_baseline_acceptance(payload, dry_run=False, accept_baseline=True, local_target_ok=True)
    assert not ok


def test_baseline_gate_allows_clean_pass():
    mod = _load_parity_module("parity_baseline_gate", "baseline_gate.py")
    payload = {"summary": {"fail": 0, "p0_fail": 0, "unasserted": 0}}
    ok, _ = mod.evaluate_baseline_acceptance(payload, dry_run=False, accept_baseline=True, local_target_ok=True)
    assert ok


def test_service_intent_case_expects_no_photo():
    cases = [c for c in _load_cases() if c.get("case_id") == "PARITY-P0-001"]
    assert cases[0]["expected_media"]["photo"] == "none"


def test_context_override_case_exists():
    ids = {c["case_id"] for c in _load_cases()}
    assert "PARITY-P0-052" in ids


def test_price_without_pv_forbidden_in_format_price():
    text = FORMAT_PRICE.read_text(encoding="utf-8")
    assert "MISSING_PRICE_TEXT" in text
    assert "не опубликована" in text
    assert "нет в базе" not in text.lower()
    assert '"0 BYN"' not in text


def test_engine_no_external_n8n_in_hot_path():
    text = ENGINE.read_text(encoding="utf-8")
    assert "duckdns.org" not in text
    assert "post_legacy_json" not in text.split("run_structured_query")[0]


def test_runner_script_no_requests_library():
    text = RUNNER.read_text(encoding="utf-8")
    assert "import requests" not in text
    guard = (PARITY / "lab" / "check_target.py").read_text(encoding="utf-8")
    assert "127.0.0.1:8080" in guard


def test_matrix_has_24_capabilities():
    matrix = _load_matrix()
    assert len(matrix["capabilities"]) == 24


def test_matrix_readiness_computed_not_hardcoded():
    matrix = _load_matrix()
    statuses = [c["status"] for c in matrix["capabilities"]]
    implemented = sum(1 for s in statuses if s in {"implemented_local", "covered_by_http_e2e"})
    covered = sum(1 for s in statuses if s == "covered_by_http_e2e")
    readiness = {
        "implemented_or_covered": implemented / len(statuses),
        "http_e2e_covered": covered / len(statuses),
    }
    assert 0 < readiness["http_e2e_covered"] <= 1
    assert "generated_note" in matrix


def test_parity_assertions_fail_on_service_media():
    mod = _load_parity_module("parity_assertions", "assertions.py")
    case = {"case_id": "x", "must_contain": ["hi"], "expected_mode": "structured_business", "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "max_latency_ms": 3000}
    st, _ = mod.evaluate_parity_case(
        case=case,
        http_status=200,
        latency_ms=100,
        extracted={"answer_text": "hi", "answer_mode": "structured_business", "photo": "http://x"},
    )
    assert st == "FAIL"


def test_parity_assertions_pass_latency():
    mod = _load_parity_module("parity_assertions", "assertions.py")
    case = {"case_id": "x", "must_contain": ["ok"], "expected_mode": "structured_card", "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "max_latency_ms": 3000}
    st, _ = mod.evaluate_parity_case(
        case=case,
        http_status=200,
        latency_ms=500,
        extracted={"answer_text": "ok", "answer_mode": "structured_card"},
    )
    assert st == "PASS"


def test_report_summary_counts_honest():
    mod = _load_parity_module("parity_runner_mod", "runner.py")
    payload = mod.run_parity_cases(
        target_path=ACCEPTANCE / "acceptance_target.example.json",
        corpus_path=CORPUS,
        dry_run=True,
    )
    s = payload["summary"]
    assert s["total"] == s["pass"] + s["fail"] + s["skip"] + s["unasserted"]
    assert s["skip"] == len(_load_cases())


def test_seed_file_marked_not_production():
    seed = ROOT / "postgres" / "scripts" / "staging_seed_whieda_advisor_local_v1.sql"
    assert "NOT PRODUCTION DATA" in seed.read_text(encoding="utf-8")


def test_orchestrator_parity_requires_e2e_flag():
    lab = ROOT / "backend" / "platform-api" / "scripts" / "run_local_core_lab.py"
    assert "--parity" in lab.read_text(encoding="utf-8")
    assert "requires --e2e" in lab.read_text(encoding="utf-8")


def test_matrix_json_valid_status_values():
    allowed = {"implemented_local", "covered_by_http_e2e", "missing", "blocked"}
    for cap in _load_matrix()["capabilities"]:
        assert cap["status"] in allowed
