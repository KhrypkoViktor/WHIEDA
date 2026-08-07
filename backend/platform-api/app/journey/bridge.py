"""Bridge visitor_sessions context to advisor session hints."""

from __future__ import annotations

import json
from typing import Any

from app.db import fetch_one, tenant_connection


async def load_session_context_for_advisor(
    tenant_id: str,
    *,
    visitor_session_id: str | None,
    telegram_user_id: int | None = None,
) -> dict[str, Any]:
    """Resolve product/topic hints from visitor session or telegram link."""
    async with tenant_connection(tenant_id) as conn:
        row = None
        if visitor_session_id:
            row = await fetch_one(
                conn,
                """
                select context, first_ref, journey_type, last_product_sku
                from visitor_sessions
                where tenant_id = %s and session_id = %s::uuid
                limit 1
                """,
                (tenant_id, visitor_session_id),
            )
        elif telegram_user_id:
            row = await fetch_one(
                conn,
                """
                select v.context, v.first_ref, v.journey_type, v.last_product_sku
                from telegram_identity_links l
                join visitor_sessions v
                  on v.tenant_id = l.tenant_id and v.session_id = l.session_id
                where l.tenant_id = %s and l.telegram_user_id = %s
                limit 1
                """,
                (tenant_id, telegram_user_id),
            )

    if not row:
        return {}

    context = row.get("context") or {}
    if isinstance(context, str):
        try:
            context = json.loads(context)
        except json.JSONDecodeError:
            context = {}

    hints: dict[str, Any] = {}
    sku = context.get("last_product_sku") or row.get("last_product_sku")
    if sku:
        hints["product_sku"] = sku
    if context.get("last_product_name"):
        hints["product_name"] = context["last_product_name"]
    if context.get("topic"):
        hints["topic"] = context["topic"]
    if row.get("first_ref"):
        hints["first_ref"] = row["first_ref"]
    if row.get("journey_type"):
        hints["journey_type"] = row["journey_type"]
    return hints
