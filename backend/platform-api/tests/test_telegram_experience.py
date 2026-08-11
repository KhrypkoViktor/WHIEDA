"""Tests for Telegram experience lab infrastructure."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TG = ROOT / "qa" / "telegram_experience"
LAB = TG / "lab"
sys.path.insert(0, str(TG))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus = _load("tg_corpus_test", LAB / "corpus.py")
assertions = _load("tg_assertions_test", LAB / "assertions.py")


@pytest.fixture()
def flows():
    corpus_path = TG / "whieda_telegram_experience_flows_v1.jsonl"
    if not corpus_path.is_file():
        import subprocess

        subprocess.run([sys.executable, str(TG / "build_flows.py")], cwd=str(ROOT), check=True)
    return corpus.load_flows(corpus_path)


def test_corpus_meets_minimums(flows):
    stats = corpus.flow_stats(flows)
    assert stats["flows"] >= 12
    assert stats["turns"] >= 22
    assert not corpus.validate_flows(flows, min_flows=12, min_turns=22)
    for category in corpus.CATEGORIES:
        assert stats["by_category"].get(category, 0) >= 1, f"missing category {category}"


def test_ordering_flow_requires_capabilities_before_oos(flows):
    flow = next(f for f in flows if f["flow_id"] == "TG-ORDER-CAP-OOS")
    assert flow["category"] == "ordering"
    assert flow["turns"][0]["input"] == "что можешь?"
    assert flow["turns"][1]["input"] == "пивка хочешь?"
    assert flow["turns"][1].get("expected_gap_kind") == "unsupported_topic"


def test_assertions_detect_missing_content():
    verdict = assertions.evaluate_turn(
        flow={"flow_id": "X", "category": "presentation"},
        turn={"turn": 1, "input": "x", "expected_mode": "structured_card", "must_contain": ["🔥"], "must_not_contain": [], "max_latency_ms": 1000},
        http_status=200,
        extracted={"answer_text": "plain", "answer_mode": "structured_card", "media": {}},
        latency_ms=10,
    )
    assert verdict["status"] == "FAIL"
