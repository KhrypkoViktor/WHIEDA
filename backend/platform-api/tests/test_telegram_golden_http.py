"""Deterministic mocked tests for golden HTTP acceptance lab."""

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
    spec.loader.exec_module(mod)
    return mod


target_mod = _load("tgt", LAB / "target.py")
assertions_mod = _load("assert", LAB / "http_assertions.py")
http_runner_mod = _load("runner", LAB / "http_runner.py")
baseline_mod = _load("baseline", LAB / "baseline.py")
report_mod = _load("report", LAB / "report.py")
corpus_mod = _load("corpus", LAB / "corpus.py")


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=30.0):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.handler(body or {})


@pytest.fixture()
def sample_case():
    return {
        "case_id": "GOLD-TEST-001",
        "class": "greeting",
        "priority": "P0",
        "input": {"user_text": "привет", "country": "BY", "language": "ru", "surface": "telegram"},
        "context_before": {},
        "expected": {
            "mode": "structured_business",
            "gap_kind": None,
            "must_contain": ["Здравств"],
            "must_not_contain": ["Traceback"],
            "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        },
        "expected_context_transition": {"sets": {}, "clears": [], "requires": {}},
    }


@pytest.fixture()
def golden_target():
    return json.loads((TG / "golden_target.example.json").read_text(encoding="utf-8"))


def test_public_target_rejected_before_http():
    with pytest.raises(target_mod.GoldenTargetError, match="HTTPS"):
        target_mod.validate_golden_target_url("https://127.0.0.1:8080")
    with pytest.raises(target_mod.GoldenTargetError, match="not allowed"):
        target_mod.validate_golden_target_url("http://185.252.232.93:8080")


def test_dry_run_zero_http(golden_target, sample_case):
    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[sample_case],
        flows=[],
        client=FakeTransport(lambda _b: (200, "{}", 1.0)),  # type: ignore[arg-type]
        dry_run=True,
    )
    assert payload["dry_run"] is True
    assert payload["planned_cases"] == 1
    assert payload["results"] == []


def test_request_body_and_session_isolation(golden_target, sample_case):
    sessions: list[str] = []

    def handler(body):
        sessions.append(body["session"])
        payload = {
            "ok": True,
            "answer_text": "Здравствуйте!",
            "answer_mode": "structured_business",
            "media": {},
        }
        return 200, json.dumps(payload), 50.0

    transport = FakeTransport(handler)
    http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[sample_case, {**sample_case, "case_id": "GOLD-TEST-002"}],
        flows=[],
        client=transport,  # type: ignore[arg-type]
    )
    assert len(transport.calls) == 2
    assert transport.calls[0]["body"]["question"] == "привет"
    assert transport.calls[0]["body"]["surface"] == "telegram"
    assert len(set(sessions)) == 2


def test_mode_and_must_contain_assertions(sample_case):
    verdict = assertions_mod.evaluate_positive_case(
        case=sample_case,
        http_status=200,
        payload={"ok": True, "answer_text": "Здравствуйте!", "answer_mode": "structured_business", "media": {}},
        extracted={},
        latency_ms=100,
    )
    assert verdict["status"] == "PASS"
    bad = assertions_mod.evaluate_positive_case(
        case=sample_case,
        http_status=200,
        payload={"ok": True, "answer_text": "hello", "answer_mode": "clarification", "media": {}},
        extracted={},
        latency_ms=100,
    )
    assert bad["status"] == "FAIL"


def test_context_set_clear_require(golden_target):
    flow = {
        "flow_id": "GOLD-FLOW-TEST",
        "session": "golden-flow-test",
        "priority": "P0",
        "turns": [
            {
                "turn": 1,
                "case_id": "T1",
                "class": "product_card",
                "priority": "P0",
                "input": {"user_text": "активатор", "country": "BY"},
                "expected": {
                    "mode": "structured_card",
                    "must_contain": ["Активатор"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "allow"},
                },
                "expected_context_transition": {"sets": {"last_product_sku": "M015-00"}, "clears": [], "requires": {}},
            },
            {
                "turn": 2,
                "case_id": "T2",
                "class": "price",
                "priority": "P0",
                "input": {"user_text": "цена", "country": "BY"},
                "expected": {
                    "mode": "structured_price",
                    "must_contain": ["BYN"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "none"},
                },
                "expected_context_transition": {
                    "sets": {},
                    "clears": [],
                    "requires": {"last_product_sku": "M015-00"},
                },
            },
        ],
    }

    def handler(body):
        if body["question"] == "активатор":
            return 200, json.dumps(
                {
                    "ok": True,
                    "answer_text": "Активатор клеток",
                    "answer_mode": "structured_card",
                    "context": {"last_product_sku": "M015-00"},
                    "media": {},
                }
            ), 80.0
        return 200, json.dumps({"ok": True, "answer_text": "100 BYN", "answer_mode": "structured_price", "media": {}}), 80.0

    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[],
        flows=[flow],
        client=FakeTransport(handler),  # type: ignore[arg-type]
    )
    assert payload["results"][0]["status"] == "PASS"
    assert payload["results"][1]["status"] == "PASS"


def test_flow_turn_is_not_rerun_as_standalone_case(golden_target, sample_case):
    flow_turn = {
        **sample_case,
        "case_id": "GOLD-FLOW-TURN",
        "input": {**sample_case["input"], "user_text": "цена"},
    }
    flow = {"flow_id": "GOLD-FLOW-ONLY", "session": "flow-only", "priority": "P0", "turns": [flow_turn]}
    transport = FakeTransport(
        lambda _body: (200, json.dumps({"ok": True, "answer_text": "Здравствуйте!", "answer_mode": "structured_business", "media": {}}), 10.0)
    )
    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[flow_turn],
        flows=[flow],
        client=transport,  # type: ignore[arg-type]
    )
    assert len(transport.calls) == 1
    assert payload["results"][0]["kind"] == "flow_turn"


def test_context_requirement_accepts_canonical_name(golden_target):
    flow = {
        "flow_id": "GOLD-CANONICAL-NAME",
        "session": "canonical-name",
        "priority": "P0",
        "turns": [
            {
                "turn": 1,
                "case_id": "T1",
                "class": "product_card",
                "priority": "P0",
                "input": {"user_text": "активатор", "country": "BY"},
                "expected": {"mode": "structured_card", "must_contain": ["Активатор"], "must_not_contain": [], "expected_media": {"photo": "allow"}},
                "expected_context_transition": {"sets": {"last_product_name": "Активатор"}, "clears": [], "requires": {}},
            },
            {
                "turn": 2,
                "case_id": "T2",
                "class": "price",
                "priority": "P0",
                "input": {"user_text": "цена", "country": "BY"},
                "expected": {"mode": "structured_price", "must_contain": ["BYN"], "must_not_contain": [], "expected_media": {"photo": "none"}},
                "expected_context_transition": {"sets": {}, "clears": [], "requires": {"last_product_name": "Активатор"}},
            },
        ],
    }

    def handler(body):
        if body["question"] == "активатор":
            return 200, json.dumps({"ok": True, "answer_text": "Активатор клеток", "answer_mode": "structured_card", "context": {"last_product_name": "Активатор клеток"}, "media": {}}), 10.0
        return 200, json.dumps({"ok": True, "answer_text": "1750 BYN", "answer_mode": "structured_price", "media": {}}), 10.0

    payload = http_runner_mod.run_golden_http(target=golden_target, cases=[], flows=[flow], client=FakeTransport(handler))  # type: ignore[arg-type]
    assert [row["status"] for row in payload["results"]] == ["PASS", "PASS"]


def test_failed_first_turn_marks_dependency(golden_target):
    flow = {
        "flow_id": "GOLD-FLOW-DEP",
        "session": "golden-flow-dep",
        "priority": "P0",
        "turns": [
            {
                "turn": 1,
                "case_id": "T1",
                "class": "product_card",
                "priority": "P0",
                "input": {"user_text": "bad", "country": "BY"},
                "expected": {
                    "mode": "structured_card",
                    "must_contain": ["MISSING"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "none"},
                },
                "expected_context_transition": {"sets": {}, "clears": [], "requires": {}},
            },
            {
                "turn": 2,
                "case_id": "T2",
                "class": "price",
                "priority": "P0",
                "input": {"user_text": "цена", "country": "BY"},
                "expected": {
                    "mode": "structured_price",
                    "must_contain": ["BYN"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "none"},
                },
                "expected_context_transition": {"sets": {}, "clears": [], "requires": {}},
            },
        ],
    }

    def handler(_body):
        return 200, json.dumps({"ok": True, "answer_text": "x", "answer_mode": "clarification", "media": {}}), 50.0

    transport = FakeTransport(handler)
    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[],
        flows=[flow],
        client=transport,  # type: ignore[arg-type]
    )
    assert payload["results"][0]["status"] == "FAIL"
    assert payload["results"][1]["status"] == "NOT_RUN_DEPENDENCY"
    assert len(transport.calls) == 1


def test_timeout_classification(sample_case):
    verdict = assertions_mod.evaluate_positive_case(
        case=sample_case,
        http_status=200,
        payload={"ok": True, "answer_text": "Здравствуйте!", "answer_mode": "structured_business", "media": {}},
        extracted={},
        latency_ms=2500,
    )
    assert verdict["status"] == "TIMEOUT"


def test_content_markers_treat_russian_yo_and_e_as_equivalent():
    verdict = assertions_mod.evaluate_positive_case(
        case={
            "case_id": "yo-marker",
            "priority": "P0",
            "expected": {"mode": "structured_price", "must_contain": ["Для партнера"]},
        },
        http_status=200,
        payload={
            "ok": True,
            "answer_text": "Для партнёра: 1050 BYN",
            "answer_mode": "structured_price",
            "media": {},
        },
        extracted={},
        latency_ms=10,
    )
    assert verdict["status"] == "PASS"


def test_negative_fixture_not_marketing_positive():
    fixture = {
        "fixture_id": "NEG-TEST",
        "class": "safe_boundary",
        "expected": {
            "mode": "clarification",
            "gap_kind": "medical_or_safety_boundary",
            "must_not_contain": ["BYN"],
            "must_not_modes": ["structured_card", "structured_price"],
        },
    }
    pass_row = assertions_mod.evaluate_negative_fixture(
        fixture=fixture,
        http_status=200,
        payload={"ok": True, "answer_text": "не ставлю диагноз", "answer_mode": "clarification", "gap_kind": "medical_or_safety_boundary", "media": {}},
        extracted={},
        latency_ms=50,
    )
    assert pass_row["status"] == "NEGATIVE_PASS"
    fail_row = assertions_mod.evaluate_negative_fixture(
        fixture=fixture,
        http_status=200,
        payload={"ok": True, "answer_text": "100 BYN card", "answer_mode": "structured_price", "media": {"photo_url": "x"}},
        extracted={},
        latency_ms=50,
    )
    assert fail_row["status"] == "NEGATIVE_FAIL"


def test_baseline_write_gate():
    ok_run = {
        "results": [
            {"case_id": "A", "priority": "P0", "status": "PASS"},
            {"fixture_id": "N1", "status": "NEGATIVE_PASS"},
        ]
    }
    assert baseline_mod.can_accept_baseline(ok_run)[0] is True
    bad_run = {
        "results": [
            {"case_id": "A", "priority": "P0", "status": "FAIL"},
        ]
    }
    assert baseline_mod.can_accept_baseline(bad_run)[0] is False


def test_report_redacts_body_and_secrets(golden_target):
    payload = {
        "run_id": "test-run",
        "status": "FAIL",
        "target": target_mod.target_identity(golden_target),
        "results": [
            {
                "case_id": "X",
                "status": "FAIL",
                "answer_preview": "short",
                "reason": "missing",
                "expected_mode": "structured_card",
                "answer_mode": "clarification",
            }
        ],
        "summary": report_mod.summarize_results(
            [
                {
                    "case_id": "X",
                    "status": "FAIL",
                    "answer_preview": "short",
                    "reason": "missing",
                    "expected_mode": "structured_card",
                    "answer_mode": "clarification",
                }
            ]
        ),
    }
    text = json.dumps(payload, ensure_ascii=False)
    assert "api.telegram.org" not in text
    assert "Authorization" not in text
    assert "answer_text" not in text
