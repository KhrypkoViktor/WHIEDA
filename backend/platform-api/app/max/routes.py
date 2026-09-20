"""Webhook Max Bot API: POST /v1/max/webhook (секрет — заголовок X-Max-Bot-Api-Secret)."""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.max.processor import process_max_event
from app.max.update_parser import parse_max_update
from app.settings import get_settings
from app.tenancy import _load_tenant, get_trace_id

logger = logging.getLogger("whieda.max")
router = APIRouter()

# Один бот Max на процесс; тенант — из настроек (владелец WWC).
MAX_TENANT_ID = "whieda"


def verify_max_secret(header_value: str) -> None:
    expected = get_settings().max_webhook_secret or ""
    if not expected:
        raise HTTPException(status_code=503, detail={"error": "max_not_configured"})
    if not hmac.compare_digest(header_value or "", expected):
        raise HTTPException(status_code=403, detail={"error": "max_secret_mismatch"})


async def _process(update: dict[str, Any], trace_id: str) -> None:
    event = parse_max_update(update)
    if event is None:
        return
    try:
        tenant = await _load_tenant(MAX_TENANT_ID)
        await process_max_event(tenant, event, trace_id)
    except Exception:  # noqa: BLE001 — webhook уже ответил 200, ошибку только логируем
        logger.exception("max_update_failed", extra={"trace_id": trace_id, "kind": event.kind})


@router.post("/v1/max/webhook")
async def max_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    verify_max_secret(request.headers.get("x-max-bot-api-secret", ""))
    update = await request.json()
    trace_id = get_trace_id(request)
    background_tasks.add_task(_process, update, trace_id)
    return {"ok": True}
