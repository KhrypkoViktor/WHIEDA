"""Tests for conversation reliability lab infrastructure."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONV = ROOT / "qa" / "conversation_reliability"
LAB = CONV / "lab"
sys.path.insert(0, str(LAB.parent))
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))

from app.advisor.gap import PROHIBITED_USER_FRAGMENTS, assert_no_prohibited_fragments  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus = _load("conv_corpus_test", LAB / "corpus.py")
assertions = _load("conv_assertions_test", LAB / "assertions.py")
guard = _load("conv_guard_test", LAB / "target_guard.py")


@pytest.fixture()
def flows():
    return corpus.load_flows(CONV / "whieda_conversation_flows_v1.jsonl")


def test_corpus_meets_flow_and_turn_minimums(flows):
    stats = corpus.flow_stats(flows)
    assert stats["flows"] >= 24
    assert stats["turns"] >= 80
    assert not corpus.validate_flows(flows)


def test_corpus_lint_rejects_incomplete_flow(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"flow_id": "BAD", "priority": "P0", "session": "s", "turns": []}) + "\n",
        encoding="utf-8",
    )
    flows = corpus.load_flows(bad)
    errors = corpus.validate_flows(flows, min_flows=1, min_turns=1)
    assert any("no turns" in err for err in errors)


def test_target_guard_rejects_public_url():
    with pytest.raises(ValueError):
        guard.validate_local_target("https://sysarchn8n.duckdns.org")
    guard.validate_local_target("http://127.0.0.1:8080")


def test_prohibited_fragments_guard():
    for frag in PROHIBITED_USER_FRAGMENTS:
        with pytest.raises(ValueError):
            assert_no_prohibited_fragments(f"answer {frag} here")


def test_turn_assertion_checks_context_and_gap():
    flow = {"flow_id": "T", "priority": "P0", "session": "s1", "country": "BY"}
    turn = {
        "turn": 1,
        "input": "цена",
        "expected_mode": "clarification",
        "must_contain": ["уточн"],
        "must_not_contain": ["Traceback"],
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "expected_context": {"last_product_name": None},
        "expected_gap_kind": "unknown_followup",
        "max_latency_ms": 3000,
    }
    ok = assertions.evaluate_turn(
        flow=flow,
        turn=turn,
        http_status=200,
        extracted={
            "answer_text": "Уточните название товара.",
            "answer_mode": "clarification",
            "gap_kind": "unknown_followup",
            "context": {"last_product_name": None},
            "photo": None,
            "videos": [],
            "pdf_documents": [],
        },
        latency_ms=10.0,
    )
    assert ok["status"] == "PASS"


def test_empty_answer_text_fails_turn():
    flow = {"flow_id": "T", "priority": "P0", "session": "s1", "country": "BY"}
    turn = {
        "turn": 1,
        "input": "цена активатора",
        "expected_mode": "structured_price",
        "must_contain": ["BYN"],
        "must_not_contain": ["Traceback"],
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "expected_context": {},
        "max_latency_ms": 3000,
    }
    verdict = assertions.evaluate_turn(
        flow=flow,
        turn=turn,
        http_status=200,
        extracted={"answer_text": "  ", "answer_mode": "structured_price", "photo": None, "videos": [], "pdf_documents": []},
        latency_ms=1.0,
    )
    assert verdict["status"] == "FAIL"


def test_session_isolation_flows_use_distinct_sessions(flows):
    iso = [f for f in flows if "session-isolation" in f.get("flow_id", "")]
    assert len(iso) >= 2
    sessions = {f["session"] for f in iso}
    assert len(sessions) >= 2


def test_telegram_photo_first_contract_importable():
    from app.telegram.delivery import deliver_structured_advisor_response

    assert callable(deliver_structured_advisor_response)
