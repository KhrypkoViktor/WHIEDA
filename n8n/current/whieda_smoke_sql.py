"""Lightweight SQL helpers for Telegram smoke (no n8n REST)."""

from __future__ import annotations

import time
from typing import Any

from whieda_runtime_pg_bootstrap import ensure_pgpassword
from whieda_runtime_read import query_rows

SMOKE_CHAT_ID = 900001


def idempotency_key(message_id: int) -> str:
    return f"whieda-tg-{SMOKE_CHAT_ID}-{message_id}"


def wait_for_advisor_event(
    message_id: int,
    *,
    poll_sec: float = 3.0,
    max_wait_sec: float = 180.0,
) -> dict[str, Any] | None:
    ensure_pgpassword()
    key = idempotency_key(message_id)
    deadline = time.perf_counter() + max_wait_sec
    while time.perf_counter() < deadline:
        rows = query_rows(
            f"""
            select surface_message_id, event_type, status,
                   left(coalesce(answer_text,''), 400) as answer_preview,
                   normalized_input->>'message_text' as question,
                   created_at
            from advisor_events
            where surface = 'telegram'
              and surface_message_id = '{key}'
            order by created_at desc
            limit 1
            """
        )
        if rows:
            return rows[0]
        time.sleep(poll_sec)
    return None


def measure_runtime_snapshot_ms() -> float | None:
    """Time the same jsonb_agg snapshot query used by advisor workflow."""
    ensure_pgpassword()
    started = time.perf_counter()
    query_rows(
        """
        select
          (select count(*) from advisor_structured_products where client_id='whieda') as products,
          (select count(*) from advisor_structured_aliases where client_id='whieda') as aliases
        """
    )
    # Full snapshot is heavy; use a representative subset timing marker.
    query_rows(
        """
        select
          coalesce((select jsonb_agg(to_jsonb(t)) from advisor_structured_aliases t where t.client_id='whieda'), '[]'::jsonb) as aliases
        """
    )
    return round((time.perf_counter() - started) * 1000, 1)
