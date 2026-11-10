"""Фоновая уборка брошенных заявок продления (владелец, 09.11.2026).

Раз в час worker переводит заявки в awaiting_* без движения дольше суток в cancelled. Человеку
ничего не пишем: он уже ушёл, а висящая заявка перехватывала чеки за другие услуги. Шаг
идемпотентен, поэтому staging с общей базой ему не мешает.
"""

from __future__ import annotations

import logging

from app.db import fetch_all, get_pool
from app.renewal_requests.service import cancel_stale_renewal_requests
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def renewal_stale_step() -> int:
    pool = get_pool()
    async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
        tenants = await fetch_all(conn, "select tenant_id from tenants where status = 'active'")
    cancelled = 0
    for row in tenants:
        cancelled += await cancel_stale_renewal_requests(str(row["tenant_id"]))
    if cancelled:
        logger.info("renewal_stale_cancelled", extra={"count": cancelled})
    return cancelled
