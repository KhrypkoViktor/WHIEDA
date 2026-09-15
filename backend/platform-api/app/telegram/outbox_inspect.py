"""Redacted read-only inspection of Telegram delivery outbox snapshots."""

from __future__ import annotations

from typing import Any

from app.telegram.log_safe import chat_ref, redact_telegram_secrets

STATUSES = (
    "pending",
    "leased",
    "sent",
    "retryable_failed",
    "unknown_delivery",
    "dead",
)


def redact_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, Any] = {}
    if "chat_id" in payload:
        cleaned["chat_ref"] = chat_ref(payload.get("chat_id"))
    if payload.get("text"):
        cleaned["text_len"] = len(str(payload.get("text")))
    if payload.get("photo_url"):
        cleaned["photo_url"] = redact_telegram_secrets(str(payload.get("photo_url")))
    if payload.get("reply_markup"):
        cleaned["has_reply_markup"] = True
    return cleaned


def redact_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or row.get("payload_json") or {}
    if isinstance(payload, str):
        payload = {}
    return {
        "delivery_id": row.get("delivery_id") or row.get("outbox_id"),
        "binding_id": row.get("binding_id"),
        "telegram_update_id": row.get("telegram_update_id"),
        "sequence_no": row.get("sequence_no"),
        "kind": row.get("kind"),
        "status": row.get("status") or row.get("state"),
        "attempt_count": row.get("attempt_count") or 0,
        "last_error_code": redact_telegram_secrets(str(row.get("last_error_code") or "")),
        "payload": redact_payload(payload),
        "created_at": row.get("created_at"),
    }


def inspect_snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    redacted = [redact_row(row) for row in rows]
    counts = {name: 0 for name in STATUSES}
    for row in redacted:
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    pending = [row for row in redacted if row.get("status") == "pending"]
    oldest = pending[0] if pending else None
    if pending:
        oldest = min(
            pending,
            key=lambda row: (str(row.get("created_at") or ""), int(row.get("sequence_no") or 0)),
        )
    return {
        "ok": True,
        "mode": "offline-snapshot",
        "counts": counts,
        "oldest_pending": oldest,
        "unknown_delivery": [row for row in redacted if row.get("status") == "unknown_delivery"],
        "dead": [row for row in redacted if row.get("status") == "dead"],
        "row_count": len(redacted),
    }
