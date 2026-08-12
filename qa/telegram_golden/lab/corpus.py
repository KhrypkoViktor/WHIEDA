"""Golden corpus load/validate helpers (offline, no runtime)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GOLDEN_CLASSES = (
    "greeting",
    "capabilities",
    "company",
    "catalog",
    "product_card",
    "price",
    "media",
    "clarification",
    "basket",
    "business",
    "safe_boundary",
)

CLASS_MINIMUMS: dict[str, int] = {
    "greeting": 6,
    "capabilities": 8,
    "company": 4,
    "catalog": 6,
    "product_card": 20,
    "price": 18,
    "media": 15,
    "clarification": 12,
    "basket": 6,
    "business": 10,
    "safe_boundary": 10,
}

MIN_TOTAL_CASES = 115
MIN_TOTAL_FLOWS = 15

SOURCE_KIND_FILES: dict[str, str] = {
    "smoke_cases_raw": "n8n/current/source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv",
    "telegram_experience": "qa/telegram_experience/whieda_telegram_experience_flows_v1.jsonl",
    "conversation_reliability": "qa/conversation_reliability/whieda_conversation_flows_v1.jsonl",
    "no_blind_zone": "qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl",
    "service_intent_fuzz": "backend/platform-api/tests/test_telegram_service_intent_fuzz.py",
    "rag_observed_dialogue": "RAG/1 компиляция. диалоги с врачами/2_SQL_корпус_из_RAW/18_DIALOGUE_FLOWS.tsv",
}

SOURCE_KINDS_WITHOUT_EXTERNAL_FILE = frozenset({"contract", "telegram_navigation"})

REQUIRED_CASE_KEYS = ("case_id", "class", "input", "expected", "source")
REQUIRED_INPUT_KEYS = ("user_text", "surface")
REQUIRED_EXPECTED_KEYS = ("mode", "must_contain", "must_not_contain")
REQUIRED_FLOW_KEYS = ("flow_id", "turns", "session")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def validate_cases(
    cases: list[dict[str, Any]],
    *,
    min_cases: int = MIN_TOTAL_CASES,
    class_minimums: dict[str, int] | None = None,
) -> list[str]:
    errors: list[str] = []
    floors = class_minimums or CLASS_MINIMUMS
    if len(cases) < min_cases:
        errors.append(f"expected at least {min_cases} cases, got {len(cases)}")
    seen: set[str] = set()
    by_class: dict[str, int] = {c: 0 for c in GOLDEN_CLASSES}
    for row in cases:
        case_id = str(row.get("case_id") or "")
        if not case_id:
            errors.append("case missing case_id")
            continue
        if case_id in seen:
            errors.append(f"duplicate case_id {case_id}")
        seen.add(case_id)
        for key in REQUIRED_CASE_KEYS:
            if key not in row:
                errors.append(f"{case_id}: missing {key}")
        klass = str(row.get("class") or "")
        if klass not in GOLDEN_CLASSES:
            errors.append(f"{case_id}: invalid class {klass!r}")
        else:
            by_class[klass] += 1
        inp = row.get("input") or {}
        for key in REQUIRED_INPUT_KEYS:
            if key not in inp:
                errors.append(f"{case_id}: input missing {key}")
        user_text = str(inp.get("user_text") or "").strip()
        if not user_text:
            errors.append(f"{case_id}: empty user_text")
        expected = row.get("expected") or {}
        for key in REQUIRED_EXPECTED_KEYS:
            if key not in expected:
                errors.append(f"{case_id}: expected missing {key}")
        mode = str(expected.get("mode") or "").strip()
        if not mode:
            errors.append(f"{case_id}: expected.mode is empty")
        must_contain = expected.get("must_contain") or []
        must_not = expected.get("must_not_contain") or []
        gap_kind = expected.get("gap_kind")
        if not must_contain and klass not in {"clarification", "safe_boundary"}:
            errors.append(f"{case_id}: expected.must_contain is empty")
        if klass == "safe_boundary" and not gap_kind and not must_not:
            errors.append(f"{case_id}: safe_boundary needs gap_kind or must_not_contain")
        if "expected_context_transition" not in row:
            errors.append(f"{case_id}: missing expected_context_transition")
        errors.extend(_validate_case_source(case_id, row.get("source") or {}))
    for klass, floor in floors.items():
        count = by_class.get(klass, 0)
        if count < floor:
            errors.append(f"class {klass}: expected >={floor}, got {count}")
    return errors


def _validate_case_source(case_id: str, source: dict[str, Any], *, root: Path | None = None) -> list[str]:
    errors: list[str] = []
    kind = str(source.get("kind") or "").strip()
    ref = str(source.get("ref") or source.get("flow_id") or "").strip()
    if not kind:
        errors.append(f"{case_id}: source.kind is required")
        return errors
    if not ref and kind not in {"rag_observed_dialogue"}:
        errors.append(f"{case_id}: source.ref is required for kind {kind!r}")
    if kind in SOURCE_KIND_FILES:
        root = root or Path(__file__).resolve().parents[3]
        rel = SOURCE_KIND_FILES[kind]
        if not (root / rel).is_file():
            errors.append(f"{case_id}: source kind {kind!r} claims missing file {rel}")
    elif kind not in SOURCE_KINDS_WITHOUT_EXTERNAL_FILE:
        errors.append(f"{case_id}: unknown source.kind {kind!r}")
    if kind == "service_intent_fuzz":
        provenance = str(source.get("provenance") or "")
        if "test_telegram_service_intent_fuzz.py::INTENT_CASES" not in provenance:
            errors.append(f"{case_id}: service_intent_fuzz missing provenance to INTENT_CASES")
    if kind == "contract" and not str(source.get("provenance") or source.get("ref") or "").strip():
        errors.append(f"{case_id}: contract source needs ref or provenance")
    return errors


def validate_negative_fixtures(
    fixtures: list[dict[str, Any]],
    *,
    expected_count: int = 5,
    root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if len(fixtures) != expected_count:
        errors.append(f"expected {expected_count} negative fixtures, got {len(fixtures)}")
    seen: set[str] = set()
    for row in fixtures:
        fixture_id = str(row.get("fixture_id") or "")
        if fixture_id in seen:
            errors.append(f"duplicate fixture_id {fixture_id}")
        seen.add(fixture_id)
        if row.get("review_status") != "blocked_raw_internal_only":
            errors.append(f"{fixture_id}: review_status must be blocked_raw_internal_only")
        if row.get("class") != "safe_boundary":
            errors.append(f"{fixture_id}: class must be safe_boundary")
        expected = row.get("expected") or {}
        if expected.get("gap_kind") != "medical_or_safety_boundary":
            errors.append(f"{fixture_id}: expected gap_kind medical_or_safety_boundary required")
        must_not_modes = expected.get("must_not_modes") or []
        if "structured_card" not in must_not_modes or "structured_price" not in must_not_modes:
            errors.append(f"{fixture_id}: must_not_modes must block card and price")
        source = row.get("source") or {}
        if not source.get("provenance_verified"):
            errors.append(f"{fixture_id}: provenance_verified required")
        errors.extend(_validate_case_source(fixture_id, source, root=root))
    return errors


def validate_flows(flows: list[dict[str, Any]], *, min_flows: int = MIN_TOTAL_FLOWS) -> list[str]:
    errors: list[str] = []
    if len(flows) < min_flows:
        errors.append(f"expected at least {min_flows} flows, got {len(flows)}")
    seen: set[str] = set()
    for flow in flows:
        flow_id = str(flow.get("flow_id") or "")
        if not flow_id:
            errors.append("flow missing flow_id")
            continue
        if flow_id in seen:
            errors.append(f"duplicate flow_id {flow_id}")
        seen.add(flow_id)
        for key in REQUIRED_FLOW_KEYS:
            if key not in flow:
                errors.append(f"{flow_id}: missing {key}")
        turns = flow.get("turns") or []
        if len(turns) < 2:
            errors.append(f"{flow_id}: expected >=2 turns, got {len(turns)}")
        for turn in turns:
            turn_no = turn.get("turn")
            if turn_no is None:
                errors.append(f"{flow_id}: turn missing turn index")
            if "expected_context_transition" not in turn:
                errors.append(f"{flow_id} turn {turn_no}: missing expected_context_transition")
    return errors


def flow_stats(flows: list[dict[str, Any]]) -> dict[str, Any]:
    turn_counts = [len(f.get("turns") or []) for f in flows]
    return {
        "flows": len(flows),
        "turns_total": sum(turn_counts),
        "turns_avg": round(sum(turn_counts) / len(turn_counts), 2) if turn_counts else 0,
    }


def case_stats(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_class = {c: 0 for c in GOLDEN_CLASSES}
    for row in cases:
        klass = str(row.get("class") or "")
        if klass in by_class:
            by_class[klass] += 1
    return {"cases": len(cases), "by_class": by_class}
