"""Acceptance Lab tests (mock-only, no network)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
ACCEPTANCE = ROOT / "qa" / "acceptance"
RUNNER = ACCEPTANCE / "run_acceptance.py"
EXAMPLE_TARGET = ACCEPTANCE / "acceptance_target.example.json"

sys.path.insert(0, str(ACCEPTANCE))

from lab.assertions import evaluate_result, has_assertions  # noqa: E402
from lab.baseline import build_baseline, compare_baselines  # noqa: E402
from lab.check_target import check_target_contract  # noqa: E402
from lab.corpus import filter_cases, load_corpus  # noqa: E402
from lab.offline import run_offline_checks  # noqa: E402
from lab.paths import get_by_path  # noqa: E402
from lab.redact import redact_run_payload, redact_text  # noqa: E402
from lab.report import build_report_payload, write_reports  # noqa: E402
from lab.runner import run_cases  # noqa: E402
from lab.target import TargetConfigError, build_advisor_request, extract_response_fields, load_target, validate_target  # noqa: E402


def ok_advisor_response(**overrides):
    base = {
        "ok": True,
        "answer_text": "Активатор клеток — описание продукта.",
        "answer_mode": "structured_card",
        "product": {"canonical_name": "Активатор клеток", "sku": "ACT-001"},
        "media": {"photo_url": None, "videos": [], "documents": []},
        "clarifications": [],
        "error_id": None,
    }
    base.update(overrides)
    return base


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = 0

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=30.0):
        self.calls += 1
        return self.handler(method, url, headers, body, timeout_seconds)


@pytest.mark.parametrize(
    "case,extracted,status,reason_fragment",
    [
        (
            {"case_id": "A", "priority": "P0", "must_contain": ["Активатор"], "must_not_contain": ["Nordman"], "expected_mode": "structured_card", "expected_product": "Активатор"},
            {"answer_text": "Активатор клеток", "answer_mode": "structured_card", "product_name": "Активатор клеток"},
            "PASS",
            "ok",
        ),
        (
            {"case_id": "B", "priority": "P0", "must_contain": ["PV"]},
            {"answer_text": "цена", "answer_mode": "structured_price"},
            "FAIL",
            "must_contain",
        ),
        (
            {"case_id": "C", "priority": "P0", "must_not_contain": ["Nordman"]},
            {"answer_text": "Nordman leak", "answer_mode": "structured_card"},
            "FAIL",
            "artifact",
        ),
        (
            {"case_id": "D", "priority": "P0", "must_contain": ["x"]},
            {"answer_text": "", "answer_mode": "structured_card"},
            "FAIL",
            "empty",
        ),
        (
            {"case_id": "E", "priority": "P0", "must_contain": ["x"]},
            {"answer_text": "Traceback here", "answer_mode": "structured_card"},
            "FAIL",
            "artifact",
        ),
        (
            {"case_id": "F", "priority": "P0", "must_contain": ["x"]},
            {"answer_text": "This needs human review", "answer_mode": "structured_card"},
            "FAIL",
            "artifact",
        ),
        (
            {"case_id": "G", "priority": "P0", "expected_mode": "structured_price"},
            {"answer_text": "ok price", "answer_mode": "structured_card"},
            "FAIL",
            "expected_mode",
        ),
        (
            {"case_id": "H", "priority": "P0", "expected_product": "Вэнтун"},
            {"answer_text": "ok", "answer_mode": "structured_card", "product_name": "Другое"},
            "FAIL",
            "expected_product",
        ),
        (
            {"case_id": "I", "priority": "P0", "expected_mode": "clarification"},
            {"answer_text": "ответ", "answer_mode": "clarification", "clarifications": []},
            "FAIL",
            "clarification",
        ),
        (
            {"case_id": "J", "priority": "P0", "expected_mode": "structured_photo", "must_contain": ["фото"]},
            {"answer_text": "фото", "answer_mode": "structured_photo", "photo": None},
            "FAIL",
            "photo",
        ),
        (
            {"case_id": "K", "priority": "P0", "expected_mode": "structured_video", "must_contain": ["Видео"]},
            {"answer_text": "Видео", "answer_mode": "structured_video", "videos": []},
            "FAIL",
            "video",
        ),
        (
            {"case_id": "L", "priority": "P0", "expected_mode": "structured_certificate", "must_contain": ["сертиф"]},
            {"answer_text": "сертиф", "answer_mode": "structured_certificate", "pdf_documents": []},
            "FAIL",
            "pdf",
        ),
    ],
)
def test_evaluate_result_scenarios(case, extracted, status, reason_fragment):
    st, reason = evaluate_result(case=case, http_status=200, latency_ms=100, extracted=extracted)
    assert st == status
    assert reason_fragment in reason.lower()


@pytest.mark.parametrize("http_status", [400, 404, 500, 502])
def test_http_errors_always_fail(http_status: int):
    case = {"case_id": "X", "priority": "P0", "must_contain": ["a"]}
    st, reason = evaluate_result(case=case, http_status=http_status, latency_ms=50, extracted={"answer_text": "a"})
    assert st == "FAIL"
    assert str(http_status) in reason


def test_timeout_always_fail():
    case = {"case_id": "X", "priority": "P0", "must_contain": ["a"]}
    st, reason = evaluate_result(case=case, http_status=None, latency_ms=None, timeout=True)
    assert st == "FAIL"
    assert "timeout" in reason.lower()


@pytest.mark.parametrize("priority,latency,should_fail", [("P0", 2500, True), ("P0", 500, False), ("P1", 5000, True), ("P2", 7000, False)])
def test_latency_sla(priority, latency, should_fail):
    case = {"case_id": "X", "priority": priority, "must_contain": ["ok"]}
    st, _ = evaluate_result(
        case=case,
        http_status=200,
        latency_ms=latency,
        extracted={"answer_text": "ok", "answer_mode": "structured_card"},
    )
    assert (st == "FAIL") == should_fail


def test_unasserted_without_expectations():
    case = {"case_id": "U", "priority": "P2"}
    assert not has_assertions(case)
    st, reason = evaluate_result(case=case, http_status=200, latency_ms=10, extracted={"answer_text": "anything"})
    assert st == "UNASSERTED"


def test_invalid_json_response_fail(example_target, target_path, mini_corpus):
    def handler(method, url, headers, body, timeout):
        return 200, "not-json", 10.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), limit=1)
    assert payload["results"][0]["status"] == "FAIL"
    assert "json" in payload["results"][0]["reason"].lower()


def test_runner_success_pass(example_target, target_path, mini_corpus):
    body = ok_advisor_response()

    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(body), 120.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-001")
    assert payload["results"][0]["status"] == "PASS"


def test_runner_wrong_product_fail(example_target, target_path, mini_corpus):
    body = ok_advisor_response(
        answer_text="описание без названия",
        product={"canonical_name": "Другое", "sku": "X"},
    )

    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(body), 120.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-001")
    assert payload["results"][0]["status"] == "FAIL"


def test_runner_empty_answer_fail(example_target, target_path, mini_corpus):
    body = ok_advisor_response(answer_text="   ")

    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(body), 120.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-001")
    assert payload["results"][0]["status"] == "FAIL"


def test_runner_http_500_fail(example_target, target_path, mini_corpus):
    def handler(method, url, headers, body_in, timeout):
        return 500, "error", 50.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-001")
    assert payload["results"][0]["status"] == "FAIL"


def test_runner_timeout_fail(example_target, target_path, mini_corpus):
    def handler(method, url, headers, body_in, timeout):
        raise TimeoutError("request timed out")

    payload = run_cases(
        target_path=target_path,
        corpus_path=mini_corpus,
        transport=FakeTransport(handler),
        case_id="T-001",
        timeout_seconds=0.001,
    )
    assert payload["results"][0]["status"] == "FAIL"


def test_runner_dry_run_skip_no_http(example_target, target_path, mini_corpus):
    transport = FakeTransport(lambda *a, **k: (200, "{}", 1.0))
    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=transport, dry_run=True, limit=2)
    assert transport.calls == 0
    assert all(r["status"] == "SKIP" for r in payload["results"])


def test_runner_fail_fast(example_target, target_path, mini_corpus):
    calls = {"n": 0}

    def handler(method, url, headers, body_in, timeout):
        calls["n"] += 1
        return 500, "err", 1.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), fail_fast=True, limit=5)
    assert calls["n"] == 1


def test_photo_first_mode(example_target, target_path, mini_corpus, tmp_path: Path):
    corpus = tmp_path / "one.jsonl"
    row = {
        "case_id": "PHO-1",
        "priority": "P0",
        "input": "дай фото",
        "context_before": [],
        "expected_mode": "structured_photo",
        "must_contain": ["фото"],
        "must_not_contain": ["Nordman"],
        "expected_product": "Активатор клеток",
    }
    corpus.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    body = ok_advisor_response(
        answer_mode="structured_photo",
        answer_text="фото активатора",
        media={"photo_url": "https://cdn.example/a.jpg", "videos": [], "documents": []},
    )

    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(body), 100.0

    payload = run_cases(target_path=target_path, corpus_path=corpus, transport=FakeTransport(handler))
    assert payload["results"][0]["status"] == "PASS"
    assert payload["results"][0]["has_photo"] is True


def test_text_followup_pass(example_target, target_path, tmp_path: Path):
    corpus = tmp_path / "fup.jsonl"
    row = {
        "case_id": "FUP-1",
        "priority": "P0",
        "input": "расскажи подробнее",
        "context_before": ["активатор"],
        "expected_mode": "structured_card",
        "must_contain": ["Активатор"],
        "must_not_contain": ["Nordman"],
        "expected_product": "Активатор клеток",
    }
    corpus.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    body = ok_advisor_response()

    def handler(method, url, headers, body_in, timeout):
        assert body_in.get("context_before") == ["активатор"]
        return 200, json.dumps(body), 100.0

    payload = run_cases(target_path=target_path, corpus_path=corpus, transport=FakeTransport(handler))
    assert payload["results"][0]["status"] == "PASS"


def test_target_loads(example_target, target_path):
    loaded = load_target(target_path)
    assert loaded["base_url"].startswith("http")


def test_target_missing_field_fails(tmp_path: Path, example_target):
    broken = dict(example_target)
    del broken["health_path"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(TargetConfigError):
        load_target(path)


def test_build_advisor_request_uses_config(example_target):
    case = {"input": "hello", "context_before": ["a"], "sku": "SKU1"}
    _, url, headers, body = build_advisor_request(example_target, case)
    assert url.endswith("/v1/advisor/query")
    assert body["question"] == "hello"
    assert body["context_before"] == ["a"]
    assert body["sku"] == "SKU1"
    assert headers["Host"] == "wwc.best"


def test_extract_response_fields_config_paths(example_target):
    payload = ok_advisor_response(media={"photo_url": "https://x/y.jpg", "videos": [{"url": "v"}], "documents": [{"url": "d"}]})
    extracted = extract_response_fields(example_target, payload)
    assert extracted["answer_text"]
    assert extracted["photo"] == "https://x/y.jpg"
    assert extracted["videos"]
    assert extracted["pdf_documents"]


def test_get_by_path_nested():
    data = {"product": {"canonical_name": "X"}, "media": {"videos": [1]}}
    assert get_by_path(data, "product.canonical_name") == "X"
    assert get_by_path(data, "media.videos") == [1]
    assert get_by_path(data, "missing.path") is None


def test_check_target_contract_pass(example_target):
    openapi = {
        "paths": {
            "/health/live": {"get": {}},
            "/health/ready": {"get": {}},
            "/v1/advisor/query": {"post": {}},
        }
    }

    def request_fn(method, url, headers, data):
        if url.endswith("/health/ready"):
            return 200, "ok"
        return 200, json.dumps(openapi)

    result = check_target_contract(example_target, request_fn)
    assert result["status"] == "PASS"


def test_check_target_health_fail(example_target):
    def request_fn(method, url, headers, data):
        return 503, "down"

    result = check_target_contract(example_target, request_fn)
    assert result["status"] == "FAIL"


def test_check_target_openapi_missing_path(example_target):
    openapi = {"paths": {"/health/live": {"get": {}}, "/health/ready": {"get": {}}}}

    def request_fn(method, url, headers, data):
        if "health" in url:
            return 200, "ok"
        return 200, json.dumps(openapi)

    result = check_target_contract(example_target, request_fn)
    assert result["status"] == "FAIL"


def test_offline_pass(target_path, mini_corpus):
    result = run_offline_checks(target_path=target_path, corpus_path=mini_corpus)
    assert result["status"] == "PASS"
    assert result["case_count"] == 2


def test_offline_offline_cli_no_http(target_path, mini_corpus, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("HTTP called during offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--offline", "--target", str(target_path), "--corpus", str(mini_corpus)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "offline mode" in proc.stdout.lower()


def test_corpus_filter_priority():
    cases = [{"case_id": "A", "priority": "P0"}, {"case_id": "B", "priority": "P1"}]
    out = filter_cases(cases, priority="P0")
    assert len(out) == 1


def test_corpus_filter_case_id():
    cases = [{"case_id": "A", "priority": "P0"}, {"case_id": "B", "priority": "P1"}]
    out = filter_cases(cases, case_id="B")
    assert out[0]["case_id"] == "B"


def test_corpus_filter_limit():
    cases = [{"case_id": f"C{i}", "priority": "P0"} for i in range(5)]
    out = filter_cases(cases, limit=3)
    assert len(out) == 3


def test_load_real_corpus_has_250_plus():
    cases = load_corpus(ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl")
    assert len(cases) >= 250


def test_baseline_regression_detection():
    prev = build_baseline({"run_id": "a", "results": [{"case_id": "X", "priority": "P0", "status": "PASS", "reason": "ok", "latency_ms": 100}]})
    curr = build_baseline({"run_id": "b", "results": [{"case_id": "X", "priority": "P0", "status": "FAIL", "reason": "bad", "latency_ms": 100}]})
    diff = compare_baselines(curr, prev)
    assert diff["status"] == "FAIL"
    assert "X" in diff["regressions"]


def test_baseline_improvement_detection():
    prev = build_baseline({"run_id": "a", "results": [{"case_id": "Y", "priority": "P0", "status": "FAIL", "reason": "bad", "latency_ms": 100}]})
    curr = build_baseline({"run_id": "b", "results": [{"case_id": "Y", "priority": "P0", "status": "PASS", "reason": "ok", "latency_ms": 100}]})
    diff = compare_baselines(curr, prev)
    assert "Y" in diff["fixed"]


def test_baseline_slower_detection():
    prev = build_baseline({"run_id": "a", "results": [{"case_id": "Z", "priority": "P0", "status": "PASS", "reason": "ok", "latency_ms": 100}]})
    curr = build_baseline({"run_id": "b", "results": [{"case_id": "Z", "priority": "P0", "status": "PASS", "reason": "ok", "latency_ms": 900}]})
    diff = compare_baselines(curr, prev)
    assert "Z" in diff["slower"]


def test_baseline_no_previous():
    curr = build_baseline({"run_id": "b", "results": []})
    diff = compare_baselines(curr, None)
    assert diff["status"] == "no_baseline"


def test_report_generation(tmp_path: Path):
    payload = {
        "run_id": "r1",
        "live_status": "FAIL",
        "target": "t",
        "corpus": "c",
        "dry_run": False,
        "summary": {"total": 2, "pass": 1, "fail": 1, "skip": 0, "unasserted": 0, "by_priority": {"P0": {"pass": 1, "fail": 1}}, "latency_ms": {"p50": 100, "p95": 200, "max": 200}},
        "results": [
            {"case_id": "P0-1", "priority": "P0", "input": "q", "status": "FAIL", "reason": "missing must_contain 'x'", "answer_text": "bad api.telegram.org leak"},
            {"case_id": "P0-2", "priority": "P0", "input": "q2", "status": "PASS", "reason": "ok", "answer_text": "ok"},
        ],
    }
    md = tmp_path / "r.md"
    js = tmp_path / "r.json"
    report = write_reports(payload, md, js)
    assert report["status"] == "FAIL"
    assert md.is_file() and js.is_file()
    body = js.read_text(encoding="utf-8")
    assert "[REDACTED]" in body
    assert "telegram" not in body.lower()


def test_redact_secrets():
    text = "Bearer abc123 token api.telegram.org duckdns.org"
    out = redact_text(text)
    assert "abc123" not in out
    assert "telegram" not in out.lower() or "[REDACTED]" in out


def test_redact_run_payload_removes_raw_paths():
    payload = {
        "results": [
            {
                "case_id": "A",
                "request": {"headers": {"Authorization": "Bearer secret"}},
                "answer_text": "api.telegram.org leak",
                "raw_response_path": "/tmp/x.json",
            }
        ]
    }
    safe = redact_run_payload(payload)
    assert "raw_response_path" not in safe["results"][0]
    assert "[REDACTED]" in safe["results"][0]["answer_text"]


def test_report_lists_unasserted():
    payload = {
        "run_id": "r2",
        "live_status": "PASS",
        "summary": {"total": 1, "pass": 0, "fail": 0, "skip": 0, "unasserted": 1, "by_priority": {}, "latency_ms": {}},
        "results": [{"case_id": "U-1", "priority": "P2", "status": "UNASSERTED", "reason": "no strict expectations"}],
    }
    report = build_report_payload(payload)
    assert "U-1" in report["unasserted_case_ids"]


def test_validate_target_structure(example_target):
    validate_target(example_target)


def test_runner_unasserted_case(example_target, target_path, mini_corpus):
    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(ok_advisor_response()), 50.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-002")
    assert payload["results"][0]["status"] == "UNASSERTED"


def test_slow_response_fail(example_target, target_path, mini_corpus):
    body = ok_advisor_response()

    def handler(method, url, headers, body_in, timeout):
        return 200, json.dumps(body), 3000.0

    payload = run_cases(target_path=target_path, corpus_path=mini_corpus, transport=FakeTransport(handler), case_id="T-001")
    assert payload["results"][0]["status"] == "FAIL"
    assert "latency" in payload["results"][0]["reason"].lower()
