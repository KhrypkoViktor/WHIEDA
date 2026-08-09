"""Parity case assertion engine (extends acceptance lab patterns)."""

from __future__ import annotations

import re
from typing import Any

ARTIFACTS = ("This needs human review", "Traceback", "Nordman", "duckdns.org", "api.telegram.org")

# Standalone zero amounts only — "1050 BYN" must not match "0 BYN".
ZERO_PRICE_AMOUNT_RE = re.compile(
    r"(?:^|[\s,;])0\s+(?:BYN|RUB|W\$|PV)(?:[\s,;.]|$)",
    re.I,
)


def has_required_assertions(case: dict[str, Any]) -> bool:
    if case.get("send_invalid_json") or case.get("expect_http_status"):
        return True
    if not case.get("must_contain") and not case.get("expected_mode"):
        return False
    if case.get("expected_media") is None:
        return False
    if case.get("max_latency_ms") is None:
        return False
    return bool(case.get("must_contain") or case.get("expected_mode"))


def _check_price_assertions(case: dict[str, Any], answer_text: str) -> tuple[str, str] | None:
    spec = case.get("price_assertions") or {}
    forbid_zero = spec.get("forbid_zero_amounts")
    if forbid_zero is None and case.get("forbid_zero_price"):
        forbid_zero = True
    if forbid_zero and ZERO_PRICE_AMOUNT_RE.search(answer_text):
        return "FAIL", "forbidden zero price amount (0 BYN/RUB/W$)"
    return None


def evaluate_parity_case(
    *,
    case: dict[str, Any],
    http_status: int | None,
    latency_ms: float | None,
    extracted: dict[str, Any] | None,
    raw_payload: dict[str, Any] | None = None,
    error: str | None = None,
    timeout: bool = False,
) -> tuple[str, str]:
    if timeout:
        return "FAIL", "request timeout"
    if error:
        return "FAIL", error

    expected_status = case.get("expect_http_status")
    if expected_status is not None:
        if http_status != expected_status:
            return "FAIL", f"expected HTTP {expected_status} got {http_status}"
        if case.get("must_not_contain"):
            body_text = str((raw_payload or extracted or {}))
            for token in case["must_not_contain"]:
                if str(token).lower() in body_text.lower():
                    return "FAIL", f"forbidden {token!r} in error response"
        return "PASS", "ok"

    if http_status is None:
        return "FAIL", "no HTTP response"
    if http_status >= 400:
        return "FAIL", f"HTTP {http_status}"

    if not has_required_assertions(case):
        return "UNASSERTED", "case missing required parity assertions"

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

    price_fail = _check_price_assertions(case, answer_text)
    if price_fail:
        return price_fail

    expected_mode = case.get("expected_mode")
    if expected_mode and answer_mode != expected_mode:
        return "FAIL", f"expected_mode {expected_mode!r} got {answer_mode!r}"

    expected_product = case.get("expected_product")
    if expected_product:
        product_name = str((extracted or {}).get("product_name") or "")
        if (
            str(expected_product).lower() not in product_name.lower()
            and str(expected_product).lower() not in answer_text.lower()
        ):
            return "FAIL", f"expected_product {expected_product!r} not found"

    media_spec = case.get("expected_media") or {}
    photo = (extracted or {}).get("photo")
    videos = (extracted or {}).get("videos") or []
    docs = (extracted or {}).get("pdf_documents") or []

    photo_rule = str(media_spec.get("photo") or "none").lower()
    if photo_rule == "required" and not photo:
        return "FAIL", "expected photo but media.photo_url empty"
    if photo_rule == "none" and photo:
        return "FAIL", "expected no photo but media.photo_url present"
    # "allow" — photo optional, no assertion

    min_v = int(media_spec.get("video_count_min") or 0)
    if len(videos) < min_v:
        return "FAIL", f"expected >= {min_v} videos got {len(videos)}"

    min_d = int(media_spec.get("document_count_min") or 0)
    if len(docs) < min_d:
        return "FAIL", f"expected >= {min_d} documents got {len(docs)}"

    ctx_spec = case.get("expected_context") or {}
    expected_last = ctx_spec.get("last_product_name")
    if expected_last is not None and expected_last != "":
        ctx = (raw_payload or {}).get("context") or {}
        last = str(ctx.get("last_product_name") or ctx.get("product_name") or "")
        product_name = str((extracted or {}).get("product_name") or "")
        found = (
            str(expected_last).lower() in last.lower()
            or str(expected_last).lower() in product_name.lower()
            or str(expected_last).lower() in answer_text.lower()
        )
        if not found:
            return "FAIL", f"expected context product {expected_last!r} not reflected"
    elif expected_last is None and ctx_spec:
        ctx = (raw_payload or {}).get("context") or {}
        last = str(ctx.get("last_product_name") or "").strip()
        if last:
            return "FAIL", f"expected empty context but last_product_name={last!r}"

    max_lat = case.get("max_latency_ms")
    if max_lat is not None and latency_ms is not None and latency_ms > float(max_lat):
        return "FAIL", f"latency {latency_ms:.0f}ms exceeds max {max_lat}ms"

    return "PASS", "ok"
