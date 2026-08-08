"""Shared fixtures for acceptance lab tests."""

from __future__ import annotations

import json
import copy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
ACCEPTANCE = ROOT / "qa" / "acceptance"
EXAMPLE_TARGET = ACCEPTANCE / "acceptance_target.example.json"
CORPUS = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"


@pytest.fixture(scope="module")
def example_target() -> dict:
    return json.loads(EXAMPLE_TARGET.read_text(encoding="utf-8"))


@pytest.fixture()
def target_path(tmp_path: Path, example_target: dict) -> Path:
    path = tmp_path / "target.json"
    path.write_text(json.dumps(example_target), encoding="utf-8")
    return path


@pytest.fixture()
def mini_corpus(tmp_path: Path) -> Path:
    rows = [
        {
            "case_id": "T-001",
            "priority": "P0",
            "input": "расскажи про активатор",
            "context_before": [],
            "expected_mode": "structured_card",
            "must_contain": ["Активатор"],
            "must_not_contain": ["Nordman"],
            "expected_product": "Активатор клеток",
        },
        {
            "case_id": "T-002",
            "priority": "P1",
            "input": "пустой кейс",
            "context_before": [],
        },
    ]
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return path


def ok_advisor_response(**overrides) -> dict:
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
