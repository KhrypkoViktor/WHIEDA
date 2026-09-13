from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.exceptions import HTTPException

from app.admin.routes import router as admin_router
from app.advisor.routes import router as advisor_router
from app.cart.routes import router as cart_router
from app.content_access.routes import router as content_access_router
from app.db import check_postgres, close_pool, get_pool, init_pool
from app.health import runtime_health
from app.errors import http_exception_handler, unhandled_exception_handler
from app.identity.routes import router as identity_router
from app.journey.routes import router as journey_router
from app.leads.routes import router as leads_router
from app.markets.routes import router as markets_router
from app.partner_library.routes import router as partner_library_router
from app.subscriptions.edge_routes import router as subscription_edge_router
from app.memory.routes import router as memory_router
from app.onboarding.routes import router as onboarding_router
from app.pilot.routes import router as pilot_router
from app.retention.routes import router as retention_router
from app.reports.routes import router as reports_router
from app.observability import TraceMiddleware, configure_logging
from app.ref.routes import router as ref_router
from app.schema_requirements import find_missing_tables, parse_disabled_features
from app.settings import get_settings
from app.telegram.routes import router as telegram_router
from app.tenancy import TenantMiddleware
from app.theme_access.routes import router as theme_access_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    await init_pool()
    await _log_schema_gaps(settings.disabled_features)
    app.state.http_client = httpx.AsyncClient(
        timeout=settings.legacy_request_timeout_sec,
        follow_redirects=True,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await close_pool()


async def _log_schema_gaps(disabled_features: str) -> None:
    """Startup cannot refuse traffic (compose has already swapped the container),
    so it shouts; the release script runs the same check *before* the swap."""
    try:
        async with get_pool().connection() as conn:
            missing = await find_missing_tables(conn, parse_disabled_features(disabled_features))
    except Exception:
        logging.getLogger(__name__).warning("schema_compatibility_check_skipped", exc_info=True)
        return
    if missing:
        logging.getLogger(__name__).error(
            "schema_compatibility_missing_tables", extra={"missing_tables": missing}
        )


def create_app() -> FastAPI:
    settings = get_settings()
    disabled = parse_disabled_features(settings.disabled_features)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(TraceMiddleware)
    app.add_middleware(TenantMiddleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready() -> dict:
        if not await check_postgres():
            raise HTTPException(status_code=503, detail={"error": "postgres_unavailable"})
        return await runtime_health()

    app.include_router(ref_router)
    app.include_router(leads_router)
    app.include_router(identity_router)
    app.include_router(journey_router)
    app.include_router(onboarding_router)
    app.include_router(reports_router)
    # Optional features: unmounted when listed in PLATFORM_DISABLED_FEATURES, so a
    # feature whose tables are not in this database returns 404, not 500.
    for feature, router in (
        ("memory", memory_router),
        ("pilot", pilot_router),
        ("retention", retention_router),
        ("partner_library", partner_library_router),
    ):
        if feature not in disabled:
            app.include_router(router)
    app.include_router(advisor_router)
    app.include_router(cart_router)
    app.include_router(markets_router)
    app.include_router(content_access_router)
    app.include_router(subscription_edge_router)
    app.include_router(theme_access_router)
    app.include_router(admin_router)
    app.include_router(telegram_router)
    return app


app = create_app()
