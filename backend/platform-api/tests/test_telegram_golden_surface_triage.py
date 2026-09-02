"""Tests for golden surface contract and failure triage."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TG = ROOT / "qa" / "telegram_golden"
LAB = TG / "lab"
ACCEPTANCE = ROOT / "qa" / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("corpus_surface", LAB / "corpus.py")
surface_mod = _load("surface_contract", LAB / "surface.py")
text_mod = _load("text_equiv", LAB / "text_equiv.py")
http_runner_mod = _load("http_runner_surface", LAB / "http_runner.py")
assertions_mod = _load("http_assert_surface", LAB / "http_assertions.py")
triage_mod = _load("triage_surface", LAB / "triage.py")
triage_baseline_mod = _load("triage_baseline_surface", LAB / "triage_baseline.py")
processors_mod = _load("surface_processors", LAB / "surface_processors_mock.py")
report_mod = _load("report_surface", LAB / "report.py")

CLASSIFICATIONS = TG / "telegram_golden_triage_classifications_v1.json"
POLICY = TG / "telegram_golden_policy_decisions_v1.json"
HTTP_REPORT = TG / "reports" / "GOLDEN_HTTP_REPORT_20260813T143036Z-b706fbfa.json"


@pytest.fixture()
def golden_target():
    return json.loads((TG / "golden_target.example.json").read_text(encoding="utf-8"))


def test_unknown_execution_surface_rejected_by_corpus():
    errors = corpus_mod.validate_cases(
        [
            {
                "case_id": "BAD-SURFACE",
                "class": "greeting",
                "execution_surface": "telegram_api",
                "input": {"user_text": "hi", "surface": "telegram"},
                "expected": {"mode": "structured_business", "must_contain": ["x"], "must_not_contain": []},
                "source": {"kind": "manual"},
                "expected_context_transition": {"sets": {}, "clears": [], "requires": {}},
            }
        ]
    )
    assert any("invalid execution_surface" in err for err in errors)


def test_advisor_http_skips_telegram_callback_case(golden_target):
    case = {
        "case_id": "CB-001",
        "class": "catalog",
        "priority": "P0",
        "execution_surface": "telegram_callback",
        "input": {"user_text": "cat:open", "surface": "telegram"},
        "expected": {"mode": "navigation_catalog", "must_contain": ["Каталог"], "must_not_contain": []},
        "expected_context_transition": {"sets": {}, "clears": [], "requires": {}},
    }

    class NoCallTransport:
        def request(self, *args, **kwargs):
            raise AssertionError("HTTP must not run for telegram_callback")

    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[case],
        flows=[],
        client=NoCallTransport(),  # type: ignore[arg-type]
    )
    assert payload["results"][0]["status"] == "SKIP_SURFACE"


def test_every_triage_input_case_classified():
    errors = triage_mod.validate_triage_registry(CLASSIFICATIONS)
    assert errors == []


def test_surface_mismatch_visible_in_triage_report():
    assert HTTP_REPORT.is_file()
    http_payload = json.loads(HTTP_REPORT.read_text(encoding="utf-8"))
    triage_payload = triage_mod.build_triage_payload(
        http_report=http_payload,
        classifications_path=CLASSIFICATIONS,
        policy_path=POLICY,
    )
    md = triage_mod.render_triage_markdown(triage_payload)
    assert "GOLD-BUS-EXTRA-01" in md
    assert "surface_mismatch" in md


def test_yo_e_assertion_equivalence():
    assert text_mod.contains_normalized("Для партнёра", "партнера")
    missing = text_mod.missing_needles("Для партнёра: 100 BYN", ["партнера"])
    assert missing == []


def test_triage_baseline_refuses_p0_core_bug():
    triage_payload = {
        "core_bug_backlog": [{"id": "X", "priority": "P0"}],
        "summary": {"negative_safety_gate": {"fail": 0}},
        "rows": [],
    }
    ok, errors = triage_baseline_mod.can_accept_triage_baseline(triage_payload)
    assert ok is False
    assert errors


def test_triage_baseline_refuses_failed_negative():
    triage_payload = {
        "core_bug_backlog": [],
        "summary": {"negative_safety_gate": {"fail": 1}},
        "rows": [],
    }
    ok, _ = triage_baseline_mod.can_accept_triage_baseline(triage_payload)
    assert ok is False


def test_triage_report_redacts_sensitive_fields():
    http_payload = json.loads(HTTP_REPORT.read_text(encoding="utf-8"))
    triage_payload = triage_mod.build_triage_payload(
        http_report=http_payload,
        classifications_path=CLASSIFICATIONS,
        policy_path=POLICY,
    )
    blob = json.dumps(triage_payload, ensure_ascii=False)
    assert "answer_text" not in blob
    assert "Authorization" not in blob


def test_deterministic_triage_json():
    http_payload = json.loads(HTTP_REPORT.read_text(encoding="utf-8"))
    first = triage_mod.build_triage_payload(
        http_report=http_payload,
        classifications_path=CLASSIFICATIONS,
        policy_path=POLICY,
    )
    second = triage_mod.build_triage_payload(
        http_report=http_payload,
        classifications_path=CLASSIFICATIONS,
        policy_path=POLICY,
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_pending_owner_policy_never_auto_pass():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert all(item.get("status") == "pending_owner" for item in policy.get("decisions") or [])
    triage_payload = {
        "core_bug_backlog": [],
        "summary": {"negative_safety_gate": {"fail": 0}},
        "rows": [
            {
                "id": "GOLD-BUS-EXTRA-04",
                "status": "PASS",
                "classification": "policy_decision_required",
                "execution_surface": "advisor_http",
            }
        ],
    }
    ok, errors = triage_baseline_mod.can_accept_triage_baseline(triage_payload)
    assert ok is False
    assert any("pending policy" in err for err in errors)


def test_mock_telegram_text_processor_routes_label():
    result = processors_mod.process_telegram_text_label("📈 Бизнес")
    assert result["action"] == "open_business_menu"


def test_mock_telegram_callback_processor_routes_prefix():
    result = processors_mod.process_telegram_callback("cat:open")
    assert result["ack"] is True
