"""Offline validation of corpus and target config (no HTTP)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lab.corpus import load_corpus
from lab.target import TargetConfigError, load_target, validate_target


def run_offline_checks(*, target_path: Path, corpus_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        target = load_target(target_path)
    except TargetConfigError as exc:
        return {"status": "FAIL", "errors": [str(exc)], "warnings": [], "case_count": 0}

    try:
        cases = load_corpus(corpus_path)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "FAIL", "errors": [str(exc)], "warnings": [], "case_count": 0}

    ids = [c.get("case_id") for c in cases]
    if len(ids) != len(set(ids)):
        errors.append("duplicate case_id values in corpus")

    for case in cases:
        if not case.get("input"):
            errors.append(f"{case.get('case_id')}: missing input")
        if case.get("priority") not in {"P0", "P1", "P2"}:
            warnings.append(f"{case.get('case_id')}: unexpected priority {case.get('priority')}")

    # Re-validate structure explicitly
    try:
        validate_target(target)
    except TargetConfigError as exc:
        errors.append(str(exc))

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "case_count": len(cases),
        "target_name": target.get("name"),
        "target_base_url": target.get("base_url"),
    }
