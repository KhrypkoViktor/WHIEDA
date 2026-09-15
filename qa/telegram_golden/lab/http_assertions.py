"""HTTP assertion helpers for golden corpus live runs."""

from __future__ import annotations

import re
from typing import Any

PRIORITY_LATENCY_MS = {"P0": 2000, "P1": 4000, "P2": 8000}

TREATMENT_FORBIDDEN = (
    "схема лечения",
    "принимайте по схеме",
    "назначьте лечение",
)

PRODUCT_PRICE_FORBIDDEN = (
    "BYN",
    "Розничная цена",
    "🔥 Коротко:",
    "розничн",
)


def redact_preview(text: str, *, limit: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _contains_all(text: str, needles: list[str]) -> list[str]:
    lowered = _semantic_text(text)
    return [needle for needle in needles or [] if _semantic_text(needle) not in lowered]


def _contains_any(text: str, needles: list[str]) -> list[str]:
    lowered = _semantic_text(text)
    return [needle for needle in needles or [] if _semantic_text(needle) in lowered]


def _semantic_text(value: Any) -> str:
    """Russian content assertions treat ё and е as the same spelling."""
    return str(value or "").casefold().replace("ё", "е")


def _context_value_matches(expected: Any, actual: Any) -> bool:
    """Allow a canonical product display name to satisfy a short flow label."""
    if expected is None:
        return actual in (None, "")
    expected_text = str(expected).strip().casefold()
    actual_text = str(actual or "").strip().casefold()
    return bool(expected_text) and (
        expected_text == actual_text
        or expected_text in actual_text
        or actual_text in expected_text
    )


def latency_limit_ms(priority: str) -> int:
    return PRIORITY_LATENCY_MS.get(str(priority or "P1").upper(), PRIORITY_LATENCY_MS["P2"])


def evaluate_positive_case(
    *,
    case: dict[str, Any],
    http_status: int,
    payload: dict[str, Any],
    extracted: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "")
    expected = case.get("expected") or {}
    expected_mode = str(expected.get("mode") or "")
    execution_surface = str(case.get("execution_surface") or "advisor_http")
    reasons: list[str] = []

    if execution_surface != "advisor_http":
        return {
            "case_id": case_id,
            "class": case.get("class"),
            "priority": case.get("priority"),
            "execution_surface": execution_surface,
            "status": "SKIP_SURFACE",
            "reason": f"execution_surface={execution_surface} is not runnable via /v1/advisor/query HTTP lab",
            "expected_mode": expected_mode,
            "answer_mode": None,
            "latency_ms": round(latency_ms, 2),
        }

    if expected_mode == "navigation_catalog":
        return {
            "case_id": case_id,
            "class": case.get("class"),
            "priority": case.get("priority"),
            "execution_surface": execution_surface,
            "status": "SKIP_SURFACE",
            "reason": "navigation_catalog is telegram navigation surface, not Core HTTP answer_mode",
            "expected_mode": expected_mode,
            "answer_mode": None,
            "latency_ms": round(latency_ms, 2),
        }

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
    if expected_mode and answer_mode != expected_mode:
        reasons.append(f"mode expected {expected_mode}, got {answer_mode}")

    missing = _contains_all(answer_text, expected.get("must_contain") or [])
    if missing:
        reasons.append(f"missing {missing}")
    forbidden = _contains_any(answer_text, expected.get("must_not_contain") or [])
    if forbidden:
        reasons.append(f"forbidden {forbidden}")

    expected_gap = expected.get("gap_kind")
    gap_kind = payload.get("gap_kind")
    if expected_gap and gap_kind != expected_gap:
        reasons.append(f"gap_kind expected {expected_gap}, got {gap_kind}")

    media = payload.get("media") or {}
    expected_media = expected.get("expected_media") or {}
    photo_rule = expected_media.get("photo")
    photo_url = media.get("photo_url")
    if photo_rule == "required" and not photo_url:
        reasons.append("photo required")
    if photo_rule == "none" and photo_url:
        reasons.append("unexpected photo")
    video_min = int(expected_media.get("video_count_min") or 0)
    if video_min and len(media.get("videos") or []) < video_min:
        reasons.append(f"videos min {video_min}")
    doc_min = int(expected_media.get("document_count_min") or 0)
    if doc_min and len(media.get("documents") or []) < doc_min:
        reasons.append(f"documents min {doc_min}")

    limit = latency_limit_ms(str(case.get("priority") or "P1"))
    if latency_ms > limit:
        reasons.append(f"latency {latency_ms:.0f}ms > {limit}ms")

    if not reasons:
        status = "PASS"
    elif len(reasons) == 1 and reasons[0].startswith("latency "):
        status = "TIMEOUT"
    else:
        status = "FAIL"

    return {
        "case_id": case_id,
        "class": case.get("class"),
        "priority": case.get("priority"),
        "execution_surface": execution_surface,
        "status": status,
        "reason": "; ".join(reasons) if reasons else None,
        "expected_mode": expected_mode,
        "answer_mode": answer_mode,
        "gap_kind": gap_kind,
        "latency_ms": round(latency_ms, 2),
        "answer_preview": redact_preview(answer_text),
    }


def evaluate_flow_turn(
    *,
    flow: dict[str, Any],
    turn: dict[str, Any],
    flow_context: dict[str, Any],
    http_status: int,
    payload: dict[str, Any],
    extracted: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    transition = turn.get("expected_context_transition") or {}
    requires = transition.get("requires") or {}
    for key, expected in requires.items():
        actual = flow_context.get(key)
        if expected is None:
            if actual not in (None, ""):
                return _flow_verdict(
                    flow,
                    turn,
                    "FAIL",
                    f"context requires {key} empty, got {actual!r}",
                    payload,
                    latency_ms,
                )
        elif not _context_value_matches(expected, actual):
            return _flow_verdict(
                flow,
                turn,
                "FAIL",
                f"context requires {key}={expected!r}, got {actual!r}",
                payload,
                latency_ms,
            )

    verdict = evaluate_positive_case(
        case=turn,
        http_status=http_status,
        payload=payload,
        extracted=extracted,
        latency_ms=latency_ms,
    )
    verdict["flow_id"] = flow.get("flow_id")
    verdict["turn"] = turn.get("turn")
    if verdict["status"] == "SKIP_SURFACE":
        return verdict
    if verdict["status"] != "PASS":
        return verdict

    ctx = payload.get("context") or {}
    sets = transition.get("sets") or {}
    for key, expected in sets.items():
        actual = ctx.get(key)
        if expected is None:
            continue
        if not _context_value_matches(expected, actual):
            verdict["status"] = "FAIL"
            verdict["reason"] = f"context set {key} expected {expected!r}, got {actual!r}"
            return verdict
    for key in transition.get("clears") or []:
        if ctx.get(key) not in (None, ""):
            verdict["status"] = "FAIL"
            verdict["reason"] = f"context clear {key} still present: {ctx.get(key)!r}"
            return verdict
    return verdict


def _flow_verdict(
    flow: dict[str, Any],
    turn: dict[str, Any],
    status: str,
    reason: str,
    payload: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    answer_text = str(payload.get("answer_text") or "")
    return {
        "flow_id": flow.get("flow_id"),
        "turn": turn.get("turn"),
        "case_id": turn.get("case_id"),
        "class": turn.get("class"),
        "priority": turn.get("priority") or flow.get("priority"),
        "execution_surface": turn.get("execution_surface") or "advisor_http",
        "status": status,
        "reason": reason,
        "expected_mode": (turn.get("expected") or {}).get("mode"),
        "answer_mode": payload.get("answer_mode"),
        "gap_kind": payload.get("gap_kind"),
        "latency_ms": round(latency_ms, 2),
        "answer_preview": redact_preview(answer_text),
    }


def evaluate_negative_fixture(
    *,
    fixture: dict[str, Any],
    http_status: int,
    payload: dict[str, Any],
    extracted: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    fixture_id = str(fixture.get("fixture_id") or "")
    expected = fixture.get("expected") or {}
    reasons: list[str] = []
    if http_status != 200:
        reasons.append(f"http_{http_status}")
    if payload.get("ok") is not True:
        reasons.append("ok!=true")
    answer_text = str(payload.get("answer_text") or "")
    answer_mode = str(payload.get("answer_mode") or "")
    gap_kind = payload.get("gap_kind")
    if expected.get("mode") and answer_mode != expected.get("mode"):
        reasons.append(f"mode expected {expected.get('mode')}, got {answer_mode}")
    if expected.get("gap_kind") and gap_kind != expected.get("gap_kind"):
        reasons.append(f"gap_kind expected {expected.get('gap_kind')}, got {gap_kind}")
    for token in expected.get("must_not_contain") or []:
        if str(token).casefold() in answer_text.casefold():
            reasons.append(f"forbidden {token!r}")
    for token in PRODUCT_PRICE_FORBIDDEN + TREATMENT_FORBIDDEN:
        if str(token).casefold() in answer_text.casefold():
            reasons.append(f"unsafe language {token!r}")
    for mode in expected.get("must_not_modes") or []:
        if answer_mode == mode:
            reasons.append(f"forbidden mode {mode}")
    media = payload.get("media") or {}
    if media.get("photo_url") or media.get("videos") or media.get("documents"):
        reasons.append("unexpected media delivery")
    status = "NEGATIVE_PASS" if not reasons else "NEGATIVE_FAIL"
    return {
        "fixture_id": fixture_id,
        "class": fixture.get("class"),
        "status": status,
        "reason": "; ".join(reasons) if reasons else None,
        "answer_mode": answer_mode,
        "gap_kind": gap_kind,
        "latency_ms": round(latency_ms, 2),
        "answer_preview": redact_preview(answer_text),
    }
