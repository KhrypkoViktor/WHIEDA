from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.settings import get_settings


async def post_legacy_json(
    request: Request,
    path: str,
    body: dict[str, Any],
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = f"{settings.legacy_n8n_base_url.rstrip('/')}{path}"
    headers = {"content-type": "application/json"}
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        headers["x-trace-id"] = trace_id
    timeout = timeout_sec if timeout_sec is not None else settings.legacy_request_timeout_sec
    app_client: httpx.AsyncClient = request.app.state.http_client
    use_app_client = timeout_sec is None
    if use_app_client:
        client = app_client
        close_client = False
    else:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        close_client = True
    try:
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail={"ok": False, "error": "legacy_upstream_unavailable", "reason": str(exc)},
            ) from exc

        if response.status_code >= 500:
            raise HTTPException(
                status_code=503,
                detail={"ok": False, "error": "legacy_upstream_error"},
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {"ok": False, "raw": response.text}

        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=payload)
        return payload
    finally:
        if close_client:
            await client.aclose()


async def get_legacy_json(request: Request, path: str, params: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    client: httpx.AsyncClient = request.app.state.http_client
    url = f"{settings.legacy_n8n_base_url.rstrip('/')}{path}"
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": "legacy_upstream_unavailable"},
        ) from exc
    return response.json()
