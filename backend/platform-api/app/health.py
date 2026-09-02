"""Runtime health extras for TZ scale gates: DB pool and outbox lag."""

from __future__ import annotations

from typing import Any

from app.db import fetch_one, get_pool
from app.settings import get_settings


async def runtime_health() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ready",
        "db_pool": None,
        "outbox_pending": None,
        "outbox_lag_sec": None,
    }
    payload["db_pool"] = _pool_snapshot()
    pending, lag_sec = await _outbox_lag()
    payload["outbox_pending"] = pending
    payload["outbox_lag_sec"] = lag_sec
    return payload


def _pool_snapshot() -> dict[str, int | None] | None:
    try:
        stats = get_pool().get_stats()
    except Exception:
        return None
    return {
        "min": _as_int(stats.get("pool_min")),
        "max": _as_int(stats.get("pool_max")),
        "size": _as_int(stats.get("pool_size")),
        "available": _as_int(stats.get("pool_available")),
        "waiting": _as_int(stats.get("requests_waiting")),
    }


async def _outbox_lag() -> tuple[int | None, int | None]:
    try:
        pool = get_pool()
        async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
            row = await fetch_one(
                conn,
                """
                select count(*)::int as pending,
                       extract(epoch from (now() - min(created_at)))::int as lag_sec
                from platform_outbox
                where status in ('pending', 'failed', 'processing')
                """,
            )
    except Exception:
        return None, None
    if not row:
        return 0, 0
    pending = int(row.get("pending") or 0)
    if pending <= 0:
        return 0, 0
    lag = row.get("lag_sec")
    return pending, int(lag) if lag is not None else 0


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
