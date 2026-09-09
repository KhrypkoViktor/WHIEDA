from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.settings import get_settings
from app.subscriptions.service import export_active_partner_hosts
from app.tenancy import get_request_tenant

router = APIRouter(tags=["internal-edge"])


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _require_edge_secret(request: Request) -> bytes:
    configured = get_settings().platform_edge_snapshot_secret
    supplied = request.headers.get("x-wwc-edge-secret", "")
    if not configured or not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return configured.encode("utf-8")


@router.get("/v1/internal/edge/partner-hosts")
async def partner_host_snapshot(request: Request) -> Response:
    secret = _require_edge_secret(request)
    tenant = get_request_tenant(request)
    payload = await export_active_partner_hosts(tenant.tenant_id)
    body = _canonical_json(payload)
    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Cache-Control": "private, no-store",
            "X-WWC-Snapshot-Signature": f"sha256={signature}",
        },
    )
