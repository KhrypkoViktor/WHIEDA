"""Resolve one tenant-scoped Telegram bot binding before webhook ACK."""

from __future__ import annotations

import contextlib
import contextvars
import hmac
import os
from dataclasses import dataclass, field
from typing import Iterator, Literal, cast

from fastapi import HTTPException

from app.db import fetch_one, get_pool
from app.settings import get_settings
from app.tenancy import TenantContext, fetch_all_entitlements

ProcessingMode = Literal["core", "shadow", "legacy"]


@dataclass(frozen=True)
class BotBindingContext:
    binding_id: str
    tenant: TenantContext
    bot_token_ref: str
    webhook_secret_ref: str
    bot_username: str
    status: str
    processing_mode: ProcessingMode
    bot_token: str = field(repr=False)
    webhook_secret: str = field(repr=False)


_current_binding: contextvars.ContextVar[BotBindingContext | None] = contextvars.ContextVar(
    "telegram_bot_binding",
    default=None,
)


def resolve_secret_ref(ref: str) -> str:
    prefix = "env:"
    if not ref.startswith(prefix):
        raise HTTPException(
            status_code=503,
            detail={"error": "bot_binding_secret_unavailable"},
        )
    variable = ref[len(prefix) :].strip()
    value = os.environ.get(variable, "").strip()
    if not variable or not value:
        raise HTTPException(
            status_code=503,
            detail={"error": "bot_binding_secret_unavailable"},
        )
    return value


def validate_processing_mode(tenant: TenantContext, raw_mode: str | None) -> ProcessingMode:
    mode = str(raw_mode or "core").strip().lower()
    if mode not in {"core", "shadow", "legacy"}:
        raise HTTPException(status_code=503, detail={"error": "bot_binding_misconfigured"})
    if tenant.tenant_id != "whieda" and mode != "core":
        raise HTTPException(status_code=503, detail={"error": "bot_binding_misconfigured"})
    return cast(ProcessingMode, mode)


async def resolve_bot_binding_context(binding_id: str) -> BotBindingContext | None:
    settings = get_settings()
    try:
        pool = get_pool()
        async with pool.connection(timeout=settings.database_timeout_sec) as conn:
            row = await fetch_one(
                conn,
                """
                select t.tenant_id,
                       t.status as tenant_status,
                       t.display_name,
                       b.status as binding_status,
                       b.webhook_secret_ref,
                       to_jsonb(b)->>'bot_token_ref' as bot_token_ref,
                       to_jsonb(b)->>'bot_username' as bot_username,
                       to_jsonb(b)->>'processing_mode' as processing_mode
                from tenant_bot_bindings b
                join tenants t on t.tenant_id = b.tenant_id
                where b.binding_id = %s
                limit 1
                """,
                (binding_id,),
            )
            if not row or row["binding_status"] != "active":
                return None
            if row["tenant_status"] == "suspended":
                raise HTTPException(status_code=503, detail={"error": "tenant_suspended"})
            if row["tenant_status"] != "active":
                return None
            entitlement_rows = await fetch_all_entitlements(conn, row["tenant_id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "bot_binding_store_unavailable"},
        ) from exc

    tenant = TenantContext(
        tenant_id=row["tenant_id"],
        status=row["tenant_status"],
        display_name=row["display_name"],
        entitlements={
            item["feature_key"]: bool(item["enabled"])
            for item in entitlement_rows
        },
    )
    bot_token_ref = str(row.get("bot_token_ref") or "").strip()
    webhook_secret_ref = str(row.get("webhook_secret_ref") or "").strip()
    bot_username = str(row.get("bot_username") or "").lstrip("@").strip()
    if not bot_token_ref or not webhook_secret_ref or not bot_username:
        raise HTTPException(status_code=503, detail={"error": "bot_binding_misconfigured"})

    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref=bot_token_ref,
        webhook_secret_ref=webhook_secret_ref,
        bot_username=bot_username,
        status="active",
        processing_mode=validate_processing_mode(tenant, row.get("processing_mode")),
        bot_token=resolve_secret_ref(bot_token_ref),
        webhook_secret=resolve_secret_ref(webhook_secret_ref),
    )


def verify_webhook_secret(context: BotBindingContext, supplied: str) -> None:
    expected_bytes = context.webhook_secret.encode("utf-8")
    supplied_bytes = supplied.encode("utf-8")
    if not hmac.compare_digest(expected_bytes, supplied_bytes):
        raise HTTPException(status_code=403, detail={"error": "invalid_webhook_secret"})


@contextlib.contextmanager
def binding_context_scope(context: BotBindingContext) -> Iterator[BotBindingContext]:
    token = _current_binding.set(context)
    try:
        yield context
    finally:
        _current_binding.reset(token)


def current_bot_binding() -> BotBindingContext:
    context = _current_binding.get()
    if context is None:
        raise RuntimeError("Telegram bot binding context is not set")
    return context


def tenant_from_binding(fallback: TenantContext) -> TenantContext:
    """Telegram advisor tenant comes from the active binding when one is set."""
    context = _current_binding.get()
    if context is None:
        return fallback
    return context.tenant
