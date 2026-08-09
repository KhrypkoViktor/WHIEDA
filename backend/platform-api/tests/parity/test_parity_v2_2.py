"""Parity lab V2.2 harness and assertion guards."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[4]
PARITY = ROOT / "qa" / "parity"
ACCEPTANCE = ROOT / "qa" / "acceptance"


def _load(name: str, rel: str):
    path = PARITY / "lab" / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_zero_price_assertion_allows_1050_byn():
    mod = _load("parity_assertions_v22", "assertions.py")
    case = {
        "case_id": "x",
        "must_contain": ["BYN"],
        "expected_mode": "structured_price",
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "max_latency_ms": 3000,
        "price_assertions": {"forbid_zero_amounts": True},
    }
    st, reason = mod.evaluate_parity_case(
        case=case,
        http_status=200,
        latency_ms=50,
        extracted={"answer_text": "Активатор: Розничная цена: 1050 BYN, PV 300", "answer_mode": "structured_price"},
        raw_payload={"context": {}},
    )
    assert st == "PASS", reason


def test_zero_price_assertion_rejects_standalone_zero():
    mod = _load("parity_assertions_v22", "assertions.py")
    case = {
        "case_id": "x",
        "must_contain": ["BYN"],
        "expected_mode": "structured_price",
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "max_latency_ms": 3000,
        "price_assertions": {"forbid_zero_amounts": True},
    }
    st, _ = mod.evaluate_parity_case(
        case=case,
        http_status=200,
        latency_ms=50,
        extracted={"answer_text": "Товар: 0 BYN", "answer_mode": "structured_price"},
        raw_payload={"context": {}},
    )
    assert st == "FAIL"


def test_media_allow_permits_photo_on_card():
    mod = _load("parity_assertions_v22", "assertions.py")
    case = {
        "case_id": "x",
        "must_contain": ["Активатор"],
        "expected_mode": "structured_card",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
        "max_latency_ms": 3000,
    }
    st, reason = mod.evaluate_parity_case(
        case=case,
        http_status=200,
        latency_ms=50,
        extracted={
            "answer_text": "Активатор клеток — тест",
            "answer_mode": "structured_card",
            "photo": "http://example.invalid/x.jpg",
        },
        raw_payload={"context": {}},
    )
    assert st == "PASS", reason


def test_context_setup_sends_user_turns_only():
    runner = _load("parity_runner_v22", "runner.py")
    calls: list[str] = []

    class RecordingTransport:
        def request(self, method, url, *, headers=None, body=None, timeout_seconds=5.0):
            calls.append(str(body.get("question") or body.get("text") or ""))
            payload = json.dumps(
                {
                    "ok": True,
                    "answer_text": "ok",
                    "answer_mode": "structured_card",
                    "product": {"canonical_name": "Активатор клеток", "sku": "LOCAL-ACT"},
                    "media": {"photo_url": None, "videos": [], "documents": []},
                    "context": {"last_product_sku": "LOCAL-ACT", "last_product_name": "Активатор клеток"},
                }
            )
            return 200, payload, 10.0

    case = {
        "case_id": "PARITY-P0-050",
        "priority": "P0",
        "input": "цена",
        "session": "ctx-test-050",
        "country": "BY",
        "context_before": [
            {"role": "user", "text": "расскажи про активатор клеток"},
            {"role": "assistant", "text": "ignored"},
        ],
        "expected_mode": "structured_price",
        "must_contain": ["BYN"],
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "max_latency_ms": 3000,
    }
    runner.run_parity_cases(
        target_path=ACCEPTANCE / "acceptance_target.example.json",
        corpus_path=PARITY / "core_local_parity_cases_v2.jsonl",
        transport=RecordingTransport(),
        case_id="PARITY-P0-050",
        dry_run=False,
    )
    assert calls == ["расскажи про активатор клеток", "цена"]


def test_context_setup_failure_marks_not_run():
    runner = _load("parity_runner_v22", "runner.py")

    class FailTransport:
        def __init__(self):
            self.n = 0

        def request(self, method, url, *, headers=None, body=None, timeout_seconds=5.0):
            self.n += 1
            if self.n == 1:
                return 500, "err", 5.0
            return 200, "{}", 5.0

    payload = runner.run_parity_cases(
        target_path=ACCEPTANCE / "acceptance_target.example.json",
        corpus_path=PARITY / "core_local_parity_cases_v2.jsonl",
        transport=FailTransport(),
        case_id="PARITY-P0-050",
    )
    row = payload["results"][0]
    assert row["status"] == "NOT_RUN"
    assert "context setup failed" in row["reason"]


def test_corpus_p0_cases_have_rationale_where_patched():
    corpus = PARITY / "core_local_parity_cases_v2.jsonl"
    cases = [json.loads(ln) for ln in corpus.read_text(encoding="utf-8").splitlines() if ln.strip()]
    p0 = [c for c in cases if c.get("priority") == "P0"]
    assert len(p0) == 34
    for case_id in ("PARITY-P0-010", "PARITY-P0-064", "PARITY-P0-080", "PARITY-P0-091"):
        match = next(c for c in p0 if c["case_id"] == case_id)
        assert match.get("rationale"), case_id
