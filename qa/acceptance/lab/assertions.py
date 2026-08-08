"""Response assertion engine for acceptance cases."""

from __future__ import annotations

from typing import Any

ARTIFACTS = ("This needs human review", "Traceback", "Nordman")
PRIORITY_SLA_MS = {"P0": 2000, "P1": 4000, "P2": 8000}

MEDIA_MODES = {
    "structured_photo": "photo",
    "structured_video": "video",
    "structured_certificate": "pdf",
}


def has_assertions(case: dict[str, Any]) -> bool:
    if case.get("must_contain") or case.get("must_not_contain"):
        return True
    if case.get("expected_product"):
        return True
    if case.get("expected_mode"):
        return True
    return False


def evaluate_result(
    *,
    case: dict[str, Any],
    http_status: int | None,
    latency_ms: float | None,
    extracted: dict[str, Any] | None = None,
    error: str | None = None,
    timeout: bool = False,
) -> tuple[str, str]:
    case_id = case.get("case_id", "?")
    priority = case.get("priority", "P2")

    if timeout:
        return "FAIL", "request timeout"
    if error:
        return "FAIL", error
    if http_status is None:
        return "FAIL", "no HTTP response"
    if http_status >= 400:
        return "FAIL", f"HTTP {http_status}"
    if not has_assertions(case):
        return "UNASSERTED", "case has no strict expectations configured"

    answer_text = str((extracted or {}).get("answer_text") or "")
    answer_mode = str((extracted or {}).get("answer_mode") or "")

    if not answer_text.strip():
        return "FAIL", "empty answer_text"

    for artifact in ARTIFACTS:
        if artifact.lower() in answer_text.lower():
            return "FAIL", f"artifact {artifact!r} in answer_text"

    for token in case.get("must_contain") or []:
        if str(token).lower() not in answer_text.lower():
            return "FAIL", f"missing must_contain {token!r}"

    for token in case.get("must_not_contain") or []:
        if str(token).lower() in answer_text.lower():
            return "FAIL", f"forbidden must_not_contain {token!r}"

    expected_mode = case.get("expected_mode")
    if expected_mode and answer_mode != expected_mode:
        return "FAIL", f"expected_mode {expected_mode!r} got {answer_mode!r}"

    expected_product = case.get("expected_product")
    if expected_product:
        product_name = str((extracted or {}).get("product_name") or "")
        if expected_product.lower() not in product_name.lower() and expected_product.lower() not in answer_text.lower():
            return "FAIL", f"expected_product {expected_product!r} not found"

    expected_mode_str = str(expected_mode or "")
    if expected_mode_str == "clarification":
        clarifications = (extracted or {}).get("clarifications") or []
        if not clarifications and "уточн" not in answer_text.lower():
            return "FAIL", "clarification expected but none found"

    media_kind = MEDIA_MODES.get(expected_mode_str)
    if media_kind == "photo":
        if not (extracted or {}).get("photo"):
            return "FAIL", "expected photo but media.photo_url empty"
    elif media_kind == "video":
        videos = (extracted or {}).get("videos") or []
        if not videos:
            return "FAIL", "expected video but media.videos empty"
    elif media_kind == "pdf":
        docs = (extracted or {}).get("pdf_documents") or []
        if not docs:
            return "FAIL", "expected PDF/certificate but media.documents empty"

    sla = PRIORITY_SLA_MS.get(priority, 8000)
    if latency_ms is not None and latency_ms > sla:
        return "FAIL", f"latency {latency_ms:.0f}ms exceeds {priority} SLA {sla}ms"

    return "PASS", "ok"
