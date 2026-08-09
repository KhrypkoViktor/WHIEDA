"""Load and filter no-blind-zone regression corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_corpus(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def filter_cases(
    cases: list[dict[str, Any]],
    *,
    priority: str | None = None,
    case_id: str | None = None,
    group: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    selected = cases
    if priority:
        selected = [c for c in selected if c.get("priority") == priority]
    if case_id:
        selected = [c for c in selected if c.get("case_id") == case_id]
    if group:
        selected = [c for c in selected if c.get("group") == group]
    if limit is not None:
        selected = selected[:limit]
    return selected


def validate_corpus(cases: list[dict[str, Any]], *, min_cases: int = 40) -> list[str]:
    errors: list[str] = []
    if len(cases) < min_cases:
        errors.append(f"corpus has {len(cases)} cases, need at least {min_cases}")
    required = ("case_id", "priority", "group", "input", "session")
    for case in cases:
        for key in required:
            if not case.get(key):
                errors.append(f"{case.get('case_id', '?')}: missing {key}")
    return errors
