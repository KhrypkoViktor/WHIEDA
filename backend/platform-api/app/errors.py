from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


def advisor_error_response(
    request: Request,
    message: str = "Сейчас не удалось обработать запрос. Попробуйте ещё раз.",
) -> dict[str, Any]:
    trace_id = getattr(request.state, "trace_id", None) or str(uuid.uuid4())
    return {
        "ok": False,
        "answer_text": message,
        "answer_mode": "error",
        "route": "error",
        "product": None,
        "media": {"photo_url": None, "videos": [], "documents": []},
        "clarifications": [],
        "sources": [],
        "context": {},
        "error_id": trace_id,
    }


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        payload = {"ok": False, **detail}
    else:
        payload = {"ok": False, "error": str(detail)}
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        payload["trace_id"] = trace_id
    return JSONResponse(status_code=exc.status_code, content=payload)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None) or str(uuid.uuid4())
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "internal_error",
            "trace_id": trace_id,
        },
    )
