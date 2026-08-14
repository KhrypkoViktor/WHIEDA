"""Tests for human language rails HTTP acceptance lab."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "human_language_rails"
LAB = PKG / "lab"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(LAB))
sys.path.insert(0, str(ROOT / "qa" / "acceptance"))

import http_runner  # noqa: E402
import http_assertions  # noqa: E402
import target as hlr_target  # noqa: E402
import baseline as hlr_baseline  # noqa: E402
import report as hlr_report  # noqa: E402
import corpus  # noqa: E402


@pytest.fixture(scope="module")
def corpus_cases():
    return corpus.load_cases(PKG / "whieda_human_language_rails_v1.jsonl")


def test_localhost_target_rejects_remote():
    bad = dict(json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8")))
    bad["base_url"] = "http://185.252.232.93:8080"
    with pytest.raises(hlr_target.HlrTargetError):
        hlr_target.validate_hlr_target_url(bad["base_url"])


def test_dry_run_zero_http(corpus_cases):
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))
    payload = http_runner.run_hlr_http(
        target=target,
        cases=corpus_cases,
        client=None,
        dry_run=True,
    )
    assert payload["dry_run"] is True
    assert payload["selected_accepted"] == 183
    assert payload["pending"]["count"] == 40


def test_only_accepted_selected(corpus_cases):
    flow_ids = http_runner.select_flow_ids(corpus_cases, accepted_only=True)
    accepted = {
        c["flow_id"]
        for c in corpus_cases
        if c.get("turn_role") == "assertion" and c.get("acceptance_status") == "accepted"
    }
    assert flow_ids == accepted


def test_pending_not_counted_as_pass(corpus_cases):
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))
    payload = http_runner.run_hlr_http(target=target, cases=corpus_cases, client=MagicMock(), dry_run=True)
    assert payload["pending"]["count"] == 40


def test_pending_assertion_never_makes_http_call():
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))
    client = MagicMock()
    payload = http_runner.run_hlr_http(
        target=target,
        cases=[
            {
                "case_id": "PENDING-1",
                "flow_id": "F-PENDING",
                "turn_index": 1,
                "turn_role": "assertion",
                "acceptance_status": "pending_surface",
                "user_text": "привет",
                "expected_rail": "universal_menu",
            }
        ],
        client=client,
        dry_run=False,
    )
    assert client.request.call_count == 0
    assert payload["results"][0]["status"] == "NOT_RUN_PENDING"


def test_context_does_not_leak_between_flows():
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))
    calls = []

    class Client:
        def request(self, *args, **kwargs):
            calls.append(kwargs["body"])
            if len(calls) == 1:
                return 200, json.dumps({"ok": True, "answer_text": "A", "answer_mode": "structured_card", "context": {"last_product_sku": "M015-00"}}), 10.0
            return 200, json.dumps({"ok": True, "answer_text": "B", "answer_mode": "structured_price", "context": {}}), 10.0

    cases = [
        {"case_id": "A", "flow_id": "F1", "turn_index": 1, "turn_role": "assertion", "acceptance_status": "accepted", "user_text": "активатор", "expected_rail": "direct_answer", "expected_mode": "structured_card", "expected_context_transition": {"sets": ["last_product_sku"]}},
        {"case_id": "B", "flow_id": "F2", "turn_index": 1, "turn_role": "assertion", "acceptance_status": "accepted", "user_text": "цена", "expected_rail": "direct_answer", "expected_mode": "structured_price", "expected_context_transition": {"requires": ["last_product_context"]}},
    ]
    payload = http_runner.run_hlr_http(target=target, cases=cases, client=Client(), dry_run=False)
    by_case = {row.get("case_id"): row for row in payload["results"]}
    assert by_case["A"]["status"] == "PASS"
    assert by_case["B"]["status"] == "FAIL"


def test_isolated_context_before_is_materialized_as_setup():
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))

    class Client:
        def __init__(self):
            self.calls = []

        def request(self, *args, **kwargs):
            self.calls.append(kwargs["body"]["question"])
            answer_mode = "structured_card" if len(self.calls) == 1 else "structured_price"
            context = {"last_product_sku": "M015-00"} if len(self.calls) == 1 else {}
            return 200, json.dumps({"ok": True, "answer_text": "BYN", "answer_mode": answer_mode, "context": context}), 10.0

    client = Client()
    payload = http_runner.run_hlr_http(
        target=target,
        client=client,
        dry_run=False,
        cases=[
            {
                "case_id": "CTX-HTTP",
                "flow_id": "F-CTX",
                "turn_index": 1,
                "turn_role": "assertion",
                "acceptance_status": "accepted",
                "user_text": "цена",
                "context_before": [{"role": "user", "text": "активатор"}],
                "expected_rail": "direct_answer",
                "expected_mode": "structured_price",
                "must_contain_any": ["BYN"],
            }
        ],
    )
    assert client.calls == ["активатор", "цена"]
    assert next(row for row in payload["results"] if row.get("case_id") == "CTX-HTTP")["status"] == "PASS"


def test_list_context_requirement_checks_last_product_context():
    case = {
        "case_id": "CTX-LIST",
        "expected_rail": "direct_answer",
        "expected_context_transition": {"requires": ["last_product_context"]},
    }
    passed = http_assertions.evaluate_hlr_assertion(
        case=case,
        http_status=200,
        payload={"ok": True, "answer_text": "готово", "answer_mode": "structured_price"},
        extracted={},
        latency_ms=10,
        flow_context={"last_product_sku": "M015-00"},
    )
    failed = http_assertions.evaluate_hlr_assertion(
        case=case,
        http_status=200,
        payload={"ok": True, "answer_text": "готово", "answer_mode": "structured_price"},
        extracted={},
        latency_ms=10,
        flow_context={},
    )
    assert passed["status"] == "PASS"
    assert failed["status"] == "FAIL"


def test_allowed_modes_accept_visible_universal_menu():
    verdict = http_assertions.evaluate_hlr_assertion(
        case={
            "case_id": "MODE-SET",
            "expected_rail": "universal_menu",
            "expected_mode": "knowledge_gap",
            "allowed_modes": ["clarification", "knowledge_gap"],
        },
        http_status=200,
        payload={"ok": True, "answer_text": "Выберите направление", "answer_mode": "clarification"},
        extracted={},
        latency_ms=10,
    )
    assert verdict["status"] == "PASS"


def test_must_contain_all_vs_any():
    markers = corpus.UNIVERSAL_MENU_MUST_CONTAIN_ALL
    ok = http_assertions.evaluate_hlr_assertion(
        case={
            "case_id": "T1",
            "expected_rail": "universal_menu",
            "must_contain_all": list(markers),
            "must_contain_any": ["каталог"],
            "must_not_contain": ["не знаю"],
        },
        http_status=200,
        payload={
            "ok": True,
            "answer_text": f"{markers[0]}. {markers[1]}. каталог.",
            "answer_mode": "knowledge_gap",
        },
        extracted={},
        latency_ms=120.0,
    )
    assert ok["status"] == "PASS"

    bad = http_assertions.evaluate_hlr_assertion(
        case={
            "case_id": "T2",
            "expected_rail": "universal_menu",
            "must_contain_all": list(markers),
            "must_contain_any": ["каталог"],
            "must_not_contain": [],
        },
        http_status=200,
        payload={"ok": True, "answer_text": "WHIEDA", "answer_mode": "knowledge_gap"},
        extracted={},
        latency_ms=120.0,
    )
    assert bad["status"] == "FAIL"


def test_failed_setup_marks_not_run_setup():
    target = json.loads((PKG / "hlr_target.example.json").read_text(encoding="utf-8"))
    cases = [
        {
            "case_id": "S1",
            "flow_id": "F1",
            "turn_index": 1,
            "turn_role": "setup",
            "user_text": "setup",
            "context_before": [],
        },
        {
            "case_id": "A1",
            "flow_id": "F1",
            "turn_index": 2,
            "turn_role": "assertion",
            "acceptance_status": "accepted",
            "user_text": "цена",
            "expected_rail": "direct_answer",
            "expected_mode": "structured_price",
            "must_contain_any": ["BYN"],
            "must_not_contain": ["не знаю"],
            "priority": "P0",
            "context_before": [{"role": "user", "text": "setup"}],
        },
    ]

    client = MagicMock()
    client.request.side_effect = [
        (500, "{}", 100.0),
    ]
    payload = http_runner.run_hlr_http(target=target, cases=cases, client=client, dry_run=False)
    statuses = [r.get("status") for r in payload["results"]]
    assert "NOT_RUN_SETUP" in statuses
    result = next(row for row in payload["results"] if row.get("case_id") == "A1")
    assert result["acceptance_status"] == "accepted"


def test_baseline_guard_blocks_failures():
    payload = {
        "results": [
            {
                "kind": "assertion",
                "acceptance_status": "accepted",
                "case_id": "X",
                "status": "FAIL",
                "priority": "P0",
            }
        ],
        "summary": {"fail": 1},
    }
    ok, errors = hlr_baseline.can_accept_baseline(payload)
    assert not ok
    assert errors


def test_baseline_guard_blocks_dry_run():
    ok, errors = hlr_baseline.can_accept_baseline({"dry_run": True, "results": []})
    assert not ok


def test_report_paths_distinct(tmp_path: Path):
    payload = {
        "run_id": "20260814T120000Z-deadbeef",
        "status": "PASS",
        "summary": hlr_report.summarize_results([], {"count": 0}),
        "pending": {"count": 0},
    }
    j1, m1 = hlr_report.write_reports(payload, tmp_path)
    payload["run_id"] = "20260814T120001Z-cafebabe"
    j2, m2 = hlr_report.write_reports(payload, tmp_path)
    assert j1 != j2 and m1 != m2
