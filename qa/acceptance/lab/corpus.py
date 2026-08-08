"""Regression corpus loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "cases" / "whieda_regression_cases_v1.jsonl"


def load_corpus(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Corpus not found: {path}")
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
        if not item.get("case_id"):
            raise ValueError(f"Missing case_id at line {line_no}")
        cases.append(item)
    return cases


def filter_cases(
    cases: list[dict[str, Any]],
    *,
    priority: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    out = cases
    if priority:
        out = [c for c in out if c.get("priority") == priority]
    if case_id:
        out = [c for c in out if c.get("case_id") == case_id]
    if limit is not None:
        out = out[: max(0, limit)]
    return out
