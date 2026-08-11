"""Turn-level assertions for Telegram experience lab."""

from __future__ import annotations

from typing import Any


def _contains_all(text: str, needles: list[str]) -> list[str]:
    missing = []
    lowered = (text or "").casefold()
    for needle in needles or []:
        if str(needle).casefold() not in lowered:
            missing.append(needle)
    return missing


def _contains_none(text: str, needles: list[str]) -> list[str]:
    hits = []
    lowered = (text or "").casefold()
    for needle in needles or []:
        if str(needle).casefold() in lowered:
            hits.append(needle)
    return hits


def evaluate_turn(
    *,
    flow: dict[str, Any],
    turn: dict[str, Any],
    http_status: int,
    extracted: dict[str, Any],
    latency_ms: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if http_status >= 400:
        reasons.append(f"http_{http_status}")
    answer_text = str(extracted.get("answer_text") or "")
    answer_mode = str(extracted.get("answer_mode") or "")
    gap_kind = extracted.get("gap_kind")
    expected_mode = turn.get("expected_mode")
    if expected_mode and answer_mode != expected_mode:
        reasons.append(f"mode expected {expected_mode}, got {answer_mode}")
    missing = _contains_all(answer_text, turn.get("must_contain") or [])
    if missing:
        reasons.append(f"missing {missing}")
    forbidden = _contains_none(answer_text, turn.get("must_not_contain") or [])
    if forbidden:
        reasons.append(f"forbidden {forbidden}")
    expected_gap = turn.get("expected_gap_kind")
    if expected_gap and gap_kind != expected_gap:
        reasons.append(f"gap_kind expected {expected_gap}, got {gap_kind}")
    max_latency = turn.get("max_latency_ms")
    if max_latency and latency_ms > float(max_latency):
        reasons.append(f"latency {latency_ms:.0f}ms > {max_latency}ms")
    media = extracted.get("media") or {}
    expected_media = turn.get("expected_media") or {}
    if expected_media.get("photo") == "required" and not media.get("photo_url"):
        reasons.append("photo required")
    if expected_media.get("photo") == "none" and media.get("photo_url"):
        reasons.append("unexpected photo")
    video_min = int(expected_media.get("video_count_min") or 0)
    if video_min and len(media.get("videos") or []) < video_min:
        reasons.append(f"videos min {video_min}")
    doc_min = int(expected_media.get("document_count_min") or 0)
    if doc_min and len(media.get("documents") or []) < doc_min:
        reasons.append(f"documents min {doc_min}")
    return {
        "flow_id": flow.get("flow_id"),
        "category": flow.get("category"),
        "turn": turn.get("turn"),
        "input": turn.get("input"),
        "status": "PASS" if not reasons else "FAIL",
        "reason": "; ".join(reasons) if reasons else None,
        "answer_mode": answer_mode,
        "gap_kind": gap_kind,
        "latency_ms": latency_ms,
    }
