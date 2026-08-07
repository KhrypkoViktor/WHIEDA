from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.exceptions import HTTPException

from app.advisor.routes import router as advisor_router
from app.db import check_postgres, close_pool, init_pool
from app.errors import http_exception_handler, unhandled_exception_handler
from app.identity.routes import router as identity_router
from app.journey.routes import router as journey_router
from app.leads.routes import router as leads_router
from app.memory.routes import router as memory_router
from app.onboarding.routes import router as onboarding_router
from app.pilot.routes import router as pilot_router
from app.retention.routes import router as retention_router
from app.reports.routes import router as reports_router
from app.observability import TraceMiddleware, configure_logging
from app.ref.routes import router as ref_router
from app.settings import get_settings
from app.telegram.routes import router as telegram_router
from app.tenancy import TenantMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_pool()
    app.state.http_client = httpx.AsyncClient(
        timeout=settings.legacy_request_timeout_sec,
        follow_redirects=True,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(TenantMiddleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready() -> dict[str, str]:
        if not await check_postgres():
            raise HTTPException(status_code=503, detail={"error": "postgres_unavailable"})
        return {"status": "ready"}

    app.include_router(ref_router)
    app.include_router(leads_router)
    app.include_router(identity_router)
    app.include_router(journey_router)
    app.include_router(onboarding_router)
    app.include_router(reports_router)
    app.include_router(memory_router)
    app.include_router(pilot_router)
    app.include_router(retention_router)
    app.include_router(advisor_router)
    app.include_router(telegram_router)
    return app


app = create_app()
