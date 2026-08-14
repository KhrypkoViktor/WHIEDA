"""HTTP assertion helpers for human language rails live runs."""

from __future__ import annotations

import re
from typing import Any

CASE_TIMEOUT_MS = 5000


def redact_preview(text: str, *, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _semantic_text(value: Any) -> str:
    return str(value or "").casefold().replace("ё", "е")


def _contains_all(text: str, needles: list[str]) -> list[str]:
    lowered = _semantic_text(text)
    return [needle for needle in needles or [] if _semantic_text(needle) not in lowered]


def _contains_any(text: str, needles: list[str]) -> bool:
    if not needles:
        return True
    lowered = _semantic_text(text)
    return any(_semantic_text(needle) in lowered for needle in needles)


def _forbidden_hits(text: str, needles: list[str]) -> list[str]:
    lowered = _semantic_text(text)
    return [needle for needle in needles or [] if _semantic_text(needle) in lowered]


def _context_value_matches(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual in (None, "")
    expected_text = str(expected).strip().casefold()
    actual_text = str(actual or "").strip().casefold()
    return bool(expected_text) and (
        expected_text == actual_text or expected_text in actual_text or actual_text in expected_text
    )


def evaluate_setup_turn(*, http_status: int, payload: dict[str, Any], latency_ms: float) -> dict[str, Any]:
    reasons: list[str] = []
    if http_status != 200:
        reasons.append(f"http_{http_status}")
    if not isinstance(payload, dict):
        reasons.append("response not object")
        payload = {}
    if payload.get("ok") is not True:
        reasons.append("ok!=true")
    if not str(payload.get("answer_text") or "").strip():
        reasons.append("empty answer_text")
    if latency_ms > CASE_TIMEOUT_MS:
        reasons.append(f"latency {latency_ms:.0f}ms > {CASE_TIMEOUT_MS}ms")
    return {
        "status": "PASS" if not reasons else ("TIMEOUT" if len(reasons) == 1 and reasons[0].startswith("latency") else "FAIL"),
        "reason": "; ".join(reasons) if reasons else None,
        "answer_mode": payload.get("answer_mode"),
        "latency_ms": round(latency_ms, 2),
    }


def evaluate_hlr_assertion(
    *,
    case: dict[str, Any],
    http_status: int,
    payload: dict[str, Any],
    extracted: dict[str, Any],
    latency_ms: float,
    flow_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    expected_mode = str(case.get("expected_mode") or "")
    expected_rail = str(case.get("expected_rail") or "")
    reasons: list[str] = []

    if http_status != 200:
        reasons.append(f"http_{http_status}")
    if not isinstance(payload, dict):
        reasons.append("response not object")
        payload = {}
    if payload.get("ok") is not True:
        reasons.append("ok!=true")

    answer_text = str(payload.get("answer_text") or extracted.get("answer_text") or "")
    if not answer_text.strip():
        reasons.append("empty answer_text")

    answer_mode = str(payload.get("answer_mode") or extracted.get("answer_mode") or "")
    allowed_modes = [str(mode) for mode in (case.get("allowed_modes") or []) if str(mode)]
    if allowed_modes:
        if answer_mode not in allowed_modes:
            reasons.append(f"mode expected one of {allowed_modes}, got {answer_mode}")
    elif expected_mode and answer_mode != expected_mode:
        reasons.append(f"mode expected {expected_mode}, got {answer_mode}")

    missing_all = _contains_all(answer_text, case.get("must_contain_all") or [])
    if missing_all:
        reasons.append(f"missing_all {missing_all}")

    must_any = case.get("must_contain_any") or []
    if must_any and not _contains_any(answer_text, must_any):
        reasons.append(f"missing_any {must_any}")

    forbidden = _forbidden_hits(answer_text, case.get("must_not_contain") or [])
    if forbidden:
        reasons.append(f"forbidden {forbidden}")

    gap_kind = payload.get("gap_kind")
    if latency_ms > CASE_TIMEOUT_MS:
        reasons.append(f"latency {latency_ms:.0f}ms > {CASE_TIMEOUT_MS}ms")

    transition = case.get("expected_context_transition") or {}
    requires = transition.get("requires") or {}
    flow_context = flow_context or {}
    if isinstance(requires, list):
        for requirement in requires:
            key = str(requirement or "").strip()
            if key == "last_product_context":
                if not (flow_context.get("last_product_sku") or flow_context.get("last_product_name")):
                    reasons.append("context requires last_product_context")
            elif key and not flow_context.get(key):
                reasons.append(f"context requires {key}")
    elif isinstance(requires, dict):
        for key, expected in requires.items():
            actual = flow_context.get(key)
            if expected is None:
                if actual not in (None, ""):
                    reasons.append(f"context requires {key} empty, got {actual!r}")
            elif not _context_value_matches(expected, actual):
                reasons.append(f"context requires {key}={expected!r}, got {actual!r}")
    elif requires:
        reasons.append("context requires has invalid shape")

    if not reasons:
        status = "PASS"
    elif len(reasons) == 1 and reasons[0].startswith("latency "):
        status = "TIMEOUT"
    else:
        status = "FAIL"

    result = {
        "case_id": case_id,
        "flow_id": case.get("flow_id"),
        "turn_index": case.get("turn_index"),
        "priority": case.get("priority"),
        "expected_rail": expected_rail,
        "expected_mode": expected_mode or None,
        "answer_mode": answer_mode or None,
        "gap_kind": gap_kind,
        "status": status,
        "reason": "; ".join(reasons) if reasons else None,
        "latency_ms": round(latency_ms, 2),
        "answer_preview": redact_preview(answer_text),
        "source": case.get("source"),
    }

    if status == "PASS" and transition:
        ctx = payload.get("context") or {}
        sets = transition.get("sets") or {}
        if isinstance(sets, list):
            sets = {str(key): "__present__" for key in sets if str(key).strip()}
        if not isinstance(sets, dict):
            sets = {}
        for key, expected in sets.items():
            actual = ctx.get(key)
            if expected == "__present__" and actual in (None, ""):
                result["status"] = "FAIL"
                result["reason"] = f"context set {key} expected a value"
                break
            if expected != "__present__" and expected is not None and not _context_value_matches(expected, actual):
                result["status"] = "FAIL"
                result["reason"] = f"context set {key} expected {expected!r}, got {actual!r}"
                break

    return result
