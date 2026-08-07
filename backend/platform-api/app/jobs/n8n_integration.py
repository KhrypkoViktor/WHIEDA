from __future__ import annotations

import logging
from typing import Any

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)


async def trigger_lead_delivery(
    lead_id: str,
    *,
    tenant_id: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue Telegram fan-out via n8n control-plane worker (transitional)."""
    settings = get_settings()
    url = f"{settings.legacy_n8n_base_url.rstrip('/')}{settings.lead_delivery_webhook_path}"
    headers = {"content-type": "application/json"}
    if trace_id:
        headers["x-trace-id"] = trace_id
    payload = {
        "job_type": "lead_delivery_fallback",
        "tenant_id": tenant_id,
        "lead_id": lead_id,
        "idempotency_key": f"lead_delivery:{lead_id}",
    }
    async with httpx.AsyncClient(timeout=settings.legacy_request_timeout_sec) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(
            f"lead_delivery_webhook_status={response.status_code} body={response.text[:200]}"
        )
    logger.info(
        "lead_delivery_triggered",
        extra={"lead_id": lead_id, "tenant_id": tenant_id, "status": response.status_code},
    )
    return {"status_code": response.status_code, "body": response.text[:200]}
