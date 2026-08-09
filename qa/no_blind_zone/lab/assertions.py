"""Evaluate no-blind-zone corpus cases against Core advisor responses."""

from __future__ import annotations

PROHIBITED_USER_FRAGMENTS = (
    "не знаю",
    "не смог обработать",
    "нет в базе",
    "передам на проверку",
    "needs human review",
    "this needs human review",
    "передам вопрос команде",
    "нет подтверждённого ответа в базе",
)


def evaluate_nbz_case(case: dict, response: dict, *, http_status: int) -> dict:
    errors: list[str] = []
    if http_status >= 400:
        errors.append(f"HTTP {http_status}")

    answer = str(response.get("answer_text") or "")
    lowered = answer.lower()
    if not answer.strip():
        errors.append("empty answer_text")

    for fragment in PROHIBITED_USER_FRAGMENTS:
        if fragment in lowered:
            errors.append(f"prohibited fragment: {fragment!r}")

    for needle in case.get("must_contain") or []:
        if str(needle).lower() not in lowered:
            errors.append(f"missing must_contain: {needle!r}")

    for needle in case.get("must_not_contain") or []:
        if str(needle).lower() in lowered:
            errors.append(f"forbidden must_not_contain: {needle!r}")

    expected_mode = case.get("expected_mode")
    if expected_mode and response.get("answer_mode") != expected_mode:
        errors.append(f"answer_mode expected {expected_mode!r}, got {response.get('answer_mode')!r}")

    expected_gap = case.get("expected_gap_kind")
    if expected_gap:
        if response.get("gap_kind") != expected_gap:
            errors.append(f"gap_kind expected {expected_gap!r}, got {response.get('gap_kind')!r}")
    elif case.get("forbid_gap"):
        if response.get("gap_kind"):
            errors.append(f"unexpected gap_kind: {response.get('gap_kind')!r}")

    steps = response.get("next_steps") or []
    if case.get("require_next_steps") and not steps:
        errors.append("missing next_steps")

    return {
        "case_id": case.get("case_id"),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "answer_mode": response.get("answer_mode"),
        "gap_kind": response.get("gap_kind"),
    }
