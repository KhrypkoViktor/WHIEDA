"""Server-owned cart sessions: quantity, totals, snapshot, checkout via existing leads."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException

from app.advisor.sql.repository import client_id
from app.cart.pricing import build_public_cart, format_checkout_comment
from app.cart.web_links import snapshot_share_path
from app.cart.store import get_store
from app.db import fetch_all, tenant_connection
from app.leads.service import parse_lead_body, save_lead
from app.markets.constants import normalize_market_id, normalize_ref

FORBIDDEN_FIELDS = {
    "owner_id",
    "assigned_owner_id",
    "attributed_owner_id",
    "telegram_user_id",
    "amount",
    "totals",
    "prices",
    "unit_amount",
    "line_amount",
}
SKU_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
PRICE_MODES = {"primary", "repeat"}
MAX_QTY = 99


def _reject_forbidden(body: dict[str, Any]) -> None:
    for field in FORBIDDEN_FIELDS:
        if field in body and body.get(field) not in (None, ""):
            raise HTTPException(status_code=400, detail={"error": "forbidden_field"})


def _new_id() -> str:
    return secrets.token_urlsafe(18)


def _clean_sku(raw: Any) -> str:
    import re

    value = str(raw or "").strip()
    if not re.fullmatch(SKU_RE, value):
        raise HTTPException(status_code=400, detail={"error": "invalid_sku"})
    return value


def _qty(raw: Any) -> int:
    try:
        qty = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_qty"}) from exc
    if qty < 0 or qty > MAX_QTY:
        raise HTTPException(status_code=400, detail={"error": "invalid_qty"})
    return qty


def _price_mode(raw: Any) -> str:
    mode = str(raw or "primary").strip().lower()
    if mode not in PRICE_MODES:
        raise HTTPException(status_code=400, detail={"error": "invalid_price_mode"})
    return mode


async def fetch_products_for_skus(tenant_id: str, skus: list[str]) -> dict[str, dict[str, Any]]:
    if not skus:
        return {}
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select sku, canonical_name, retail_price_byn, retail_price_rub,
                   partner_price_byn, partner_price_rub, partner_w, partner_points
            from advisor_structured_products
            where client_id = %s and sku = any(%s)
            """,
            (client_id(tenant_id), skus),
        )
    return {str(row["sku"]): dict(row) for row in rows}


async def _public(tenant_id: str, record: dict[str, Any]) -> dict[str, Any]:
    skus = [str(item.get("sku") or "") for item in record.get("items") or []]
    products = await fetch_products_for_skus(tenant_id, [sku for sku in skus if sku])
    return build_public_cart(record, products)


async def create_session(tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _reject_forbidden(body)
    first_ref = normalize_ref(body.get("first_ref") or body.get("ref"))
    record = {
        "tenant_id": tenant_id,
        "cart_session_id": _new_id(),
        "market_id": normalize_market_id(body.get("market_id")),
        "price_mode": _price_mode(body.get("price_mode")),
        "first_ref": first_ref,
        "ref": normalize_ref(body.get("ref")) or first_ref,
        "visitor_session_id": str(body.get("visitor_session_id") or "").strip(),
        "items": [],
        "item_idempotency": {},
    }
    await get_store().save(record)
    return await _public(tenant_id, record)


async def get_session(tenant_id: str, cart_session_id: str) -> dict[str, Any]:
    record = await get_store().get(tenant_id, cart_session_id)
    if not record:
        raise HTTPException(status_code=404, detail={"error": "cart_not_found"})
    return await _public(tenant_id, record)


async def patch_session(tenant_id: str, cart_session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _reject_forbidden(body)
    store = get_store()
    record = await store.get(tenant_id, cart_session_id)
    if not record:
        raise HTTPException(status_code=404, detail={"error": "cart_not_found"})
    if "market_id" in body:
        record["market_id"] = normalize_market_id(body.get("market_id"))
    if "price_mode" in body:
        record["price_mode"] = _price_mode(body.get("price_mode"))
    if "ref" in body:
        record["ref"] = normalize_ref(body.get("ref"))
    await store.save(record)
    return await _public(tenant_id, record)


async def upsert_item(tenant_id: str, cart_session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _reject_forbidden(body)
    store = get_store()
    record = await store.get(tenant_id, cart_session_id)
    if not record:
        raise HTTPException(status_code=404, detail={"error": "cart_not_found"})
    sku = _clean_sku(body.get("sku"))
    qty = _qty(body.get("qty"))
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if idempotency_key:
        seen = (record.get("item_idempotency") or {}).get(idempotency_key)
        if seen is not None:
            return await _public(tenant_id, record)
    products = await fetch_products_for_skus(tenant_id, [sku])
    if sku not in products:
        raise HTTPException(status_code=400, detail={"error": "unknown_sku"})
    items = [item for item in record.get("items") or [] if str(item.get("sku")) != sku]
    if qty > 0:
        items.append({"sku": sku, "qty": qty})
    record["items"] = items
    if idempotency_key:
        record.setdefault("item_idempotency", {})[idempotency_key] = {"sku": sku, "qty": qty}
    await store.save(record)
    return await _public(tenant_id, record)


async def clear_session(tenant_id: str, cart_session_id: str) -> dict[str, Any]:
    store = get_store()
    record = await store.get(tenant_id, cart_session_id)
    if not record:
        raise HTTPException(status_code=404, detail={"error": "cart_not_found"})
    record["items"] = []
    record["item_idempotency"] = {}
    await store.save(record)
    return await _public(tenant_id, record)


async def create_snapshot(tenant_id: str, cart_session_id: str) -> dict[str, Any]:
    public = await get_session(tenant_id, cart_session_id)
    token = _new_id()
    await get_store().save_snapshot(tenant_id, token, public)
    return {
        "ok": True,
        "snapshot_token": token,
        "share_path": snapshot_share_path(token),
        "cart_session_id": cart_session_id,
    }


async def get_snapshot(tenant_id: str, token: str) -> dict[str, Any]:
    payload = await get_store().get_snapshot(tenant_id, token)
    if not payload:
        raise HTTPException(status_code=404, detail={"error": "snapshot_not_found"})
    payload["ok"] = True
    return payload


async def checkout(tenant_id: str, cart_session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _reject_forbidden(body)
    public = await get_session(tenant_id, cart_session_id)
    if not public["items"]:
        raise HTTPException(status_code=400, detail={"error": "empty_cart"})
    if public["totals"]["unavailable_count"]:
        raise HTTPException(status_code=400, detail={"error": "unavailable_price"})
    lead_body = {
        "name": body.get("name"),
        "contact": body.get("contact"),
        "product": "cart-order",
        "product_sku": "cart-order",
        "comment": format_checkout_comment(public),
        "idempotency_key": body.get("idempotency_key"),
        "page_url": body.get("page_url") or "",
        "ref": public["ref"] or public["first_ref"],
        "initial_ref": public["first_ref"],
        "active_ref": public["ref"] or public["first_ref"],
        "visitor_session_id": body.get("visitor_session_id") or "",
        "market_id": public["market_id"],
        "country_iso": "BY" if public["market_id"] == "by" else "RU",
    }
    lead = parse_lead_body(lead_body, tenant_id)
    saved = await save_lead(lead)
    return {
        "ok": True,
        "lead_public_id": saved.get("public_id"),
        "cart_session_id": cart_session_id,
        "totals": public["totals"],
    }
