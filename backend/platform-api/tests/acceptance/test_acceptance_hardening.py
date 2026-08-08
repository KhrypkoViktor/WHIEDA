"""Hardening tests: baseline gate and target gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
ACCEPTANCE = ROOT / "qa" / "acceptance"

sys.path.insert(0, str(ACCEPTANCE))

from lab.baseline import load_baseline  # noqa: E402
from lab.baseline_gate import evaluate_baseline_acceptance  # noqa: E402
from lab.run_flow import execute_run  # noqa: E402
from lab.runner import run_cases  # noqa: E402

from test_acceptance_lab import FakeTransport, ok_advisor_response  # noqa: E402


def _pass_payload() -> dict:
    return {
        "run_id": "test-run",
        "live_status": "PASS",
        "summary": {"total": 1, "pass": 1, "fail": 0, "skip": 0, "unasserted": 0},
        "results": [{"case_id": "T-001", "priority": "P0", "status": "PASS", "reason": "ok"}],
    }


def _fail_payload() -> dict:
    return {
        "run_id": "test-run-fail",
        "live_status": "FAIL",
        "summary": {"total": 1, "pass": 0, "fail": 1, "skip": 0, "unasserted": 0},
        "results": [{"case_id": "T-001", "priority": "P0", "status": "FAIL", "reason": "bad"}],
    }


def _unasserted_payload() -> dict:
    return {
        "run_id": "test-run-unasserted",
        "live_status": "PASS",
        "summary": {"total": 1, "pass": 0, "fail": 0, "skip": 0, "unasserted": 1},
        "results": [{"case_id": "T-002", "priority": "P2", "status": "UNASSERTED", "reason": "no strict expectations"}],
    }


@pytest.fixture()
def run_dirs(tmp_path: Path):
    return {
        "reports": tmp_path / "reports",
        "baselines": tmp_path / "baselines",
        "raw": tmp_path / "raw",
    }


def test_fail_run_does_not_change_baseline(target_path, mini_corpus, run_dirs):
    baseline_path = run_dirs["baselines"] / "latest.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps({"cases": {"KEEP": {"status": "PASS"}}}), encoding="utf-8")

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=False,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=lambda **k: _fail_payload(),
        limit=1,
    )
    assert exit_code == 1
    assert payload["baseline"]["saved"] is False
    assert "KEEP" in load_baseline(baseline_path)["cases"]


def test_pass_without_accept_baseline_does_not_change_baseline(target_path, mini_corpus, run_dirs):
    baseline_path = run_dirs["baselines"] / "latest.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps({"cases": {"KEEP": {"status": "PASS"}}}), encoding="utf-8")

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=False,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=lambda **k: _pass_payload(),
        limit=1,
    )
    assert exit_code == 0
    assert payload["baseline"]["saved"] is False
    assert "not saved" in payload["baseline"]["reason"]
    assert "KEEP" in load_baseline(baseline_path)["cases"]


def test_pass_with_accept_baseline_updates_baseline(target_path, mini_corpus, run_dirs):
    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=True,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=lambda **k: _pass_payload(),
        limit=1,
    )
    assert exit_code == 0
    assert payload["baseline"]["saved"] is True
    saved = load_baseline(run_dirs["baselines"] / "latest.json")
    assert "T-001" in saved["cases"]


def test_dry_run_accept_baseline_rejected(target_path, mini_corpus, run_dirs):
    baseline_path = run_dirs["baselines"] / "latest.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps({"cases": {"KEEP": {"status": "PASS"}}}), encoding="utf-8")

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=True,
        accept_baseline=True,
        run_cases_fn=lambda **k: _pass_payload(),
        limit=1,
    )
    assert exit_code == 1
    assert payload["baseline"]["saved"] is False
    assert "dry-run" in payload["baseline"]["reason"]
    assert "KEEP" in load_baseline(baseline_path)["cases"]


def test_unasserted_accept_baseline_rejected(target_path, mini_corpus, run_dirs):
    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=True,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=lambda **k: _unasserted_payload(),
        limit=1,
    )
    assert exit_code == 1
    assert payload["baseline"]["saved"] is False
    assert "UNASSERTED" in payload["baseline"]["reason"]


def test_target_check_fail_blocks_advisor_transport(target_path, mini_corpus, run_dirs):
    transport = FakeTransport(lambda *a, **k: (_ for _ in ()).throw(AssertionError("advisor called")))

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=False,
        transport=transport,
        check_target_fn=lambda *a, **k: {"status": "FAIL", "errors": ["health unreachable"]},
        run_cases_fn=run_cases,
        limit=1,
    )
    assert exit_code == 1
    assert payload["live_status"] == "NOT_RUN"
    assert transport.calls == 0
    assert payload["target_check"]["errors"]


def test_target_check_pass_runs_advisor(target_path, mini_corpus, run_dirs):
    body = ok_advisor_response()
    transport = FakeTransport(lambda *a, **k: (200, json.dumps(body), 50.0))

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=False,
        transport=transport,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=run_cases,
        case_id="T-001",
    )
    assert transport.calls == 1
    assert payload["results"][0]["status"] == "PASS"
    assert exit_code == 0


def test_dry_run_skips_target_check_and_advisor(target_path, mini_corpus, run_dirs):
    check_calls = {"n": 0}
    transport = FakeTransport(lambda *a, **k: (200, "{}", 1.0))

    def counting_check(*a, **k):
        check_calls["n"] += 1
        return {"status": "PASS"}

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=True,
        accept_baseline=False,
        transport=transport,
        check_target_fn=counting_check,
        run_cases_fn=run_cases,
        limit=1,
    )
    assert check_calls["n"] == 0
    assert transport.calls == 0
    assert payload["results"][0]["status"] == "SKIP"
    assert exit_code == 0


def test_target_check_fail_does_not_change_baseline(target_path, mini_corpus, run_dirs):
    baseline_path = run_dirs["baselines"] / "latest.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps({"cases": {"KEEP": {"status": "PASS"}}}), encoding="utf-8")

    exit_code, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=True,
        check_target_fn=lambda *a, **k: {"status": "FAIL", "errors": ["openapi missing path"]},
        run_cases_fn=run_cases,
        limit=1,
    )
    assert exit_code == 1
    assert payload["baseline"]["saved"] is False
    assert "target check failed" in payload["baseline"]["reason"]
    assert "KEEP" in load_baseline(baseline_path)["cases"]


def test_report_documents_baseline_not_saved(target_path, mini_corpus, run_dirs):
    _, payload = execute_run(
        target_path=target_path,
        corpus_path=mini_corpus,
        reports_dir=run_dirs["reports"],
        baselines_dir=run_dirs["baselines"],
        raw_dir=run_dirs["raw"],
        dry_run=False,
        accept_baseline=False,
        check_target_fn=lambda *a, **k: {"status": "PASS"},
        run_cases_fn=lambda **k: _pass_payload(),
        limit=1,
    )
    md = Path(payload["report_paths"]["md"]).read_text(encoding="utf-8")
    assert "Baseline" in md
    assert "False" in md or "not saved" in md.lower()


def test_evaluate_baseline_acceptance_rules():
    ok, _ = evaluate_baseline_acceptance(_pass_payload(), dry_run=False, accept_baseline=True)
    assert ok is True
    ok, reason = evaluate_baseline_acceptance(_pass_payload(), dry_run=False, accept_baseline=False)
    assert ok is False
    assert "not set" in reason
