"""Evaluate local Telegram canary evidence. Missing capture or wrong order is FAIL."""

from __future__ import annotations

from typing import Any, Iterable

FORBIDDEN_LEAKS = ("WHIEDA", "NSP", "активатор клеток", "wwc.best")


def capture_methods(events: Iterable[dict[str, Any]], *, token: str | None = None) -> list[str]:
    methods: list[str] = []
    for item in events:
        if token is not None and item.get("token") != token:
            continue
        method = str(item.get("bot_method") or "")
        if method not in {"sendPhoto", "sendMessage"}:
            continue
        status = item.get("status")
        if status not in (None, 200):
            continue
        methods.append(method)
    return methods


def photo_then_text_ok(events: Iterable[dict[str, Any]], *, token: str) -> bool:
    methods = capture_methods(events, token=token)
    if len(methods) < 2:
        return False
    if methods[0] != "sendPhoto" or methods[1] != "sendMessage":
        return False
    for item in events:
        if item.get("token") != token or item.get("bot_method") != "sendPhoto":
            continue
        payload = item.get("payload") or {}
        if payload.get("caption"):
            return False
        return True
    return False


def evaluate_case_evidence(case: dict[str, Any]) -> str:
    if case.get("http_only"):
        return "FAIL"
    events = case.get("capture") or []
    if case.get("require_capture") and not events:
        return "FAIL"
    token = case.get("token")
    if case.get("require_photo_then_text"):
        if not token or not photo_then_text_ok(events, token=token):
            return "FAIL"
    leaks = case.get("must_not_contain") or []
    texts = " ".join(str(item) for item in case.get("texts") or [])
    for fragment in list(leaks) + list(FORBIDDEN_LEAKS):
        if fragment and fragment in texts:
            return "FAIL"
    for fragment in case.get("must_contain") or []:
        if fragment not in texts:
            return "FAIL"
    expected_inbox = case.get("inbox_count")
    if expected_inbox is not None:
        actual_inbox = case.get("inbox_actual")
        if actual_inbox is None or int(actual_inbox) != int(expected_inbox):
            return "FAIL"
    expected_outbox = case.get("outbox_count")
    if expected_outbox is not None:
        actual_outbox = case.get("outbox_actual")
        if actual_outbox is None or int(actual_outbox) != int(expected_outbox):
            return "FAIL"
    expected_status = case.get("outbox_status")
    if expected_status and expected_status not in (case.get("outbox_statuses") or []):
        return "FAIL"
    if case.get("status") == "FAIL":
        return "FAIL"
    return "PASS" if case.get("status") == "PASS" else "FAIL"
