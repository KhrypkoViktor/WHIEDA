"""Resolve approved canonical questions to structured advisor actions."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql import formatters as fmt
from app.advisor.sql import repository as repo


def _sku_from_answer_key(answer_key: str) -> str | None:
    if ":" not in answer_key:
        return None
    sku = answer_key.split(":", 1)[1].strip()
    return sku or None


async def try_canonical_response(
    conn,
    tenant_id: str,
    row: dict[str, Any],
    *,
    country: str,
    trace_id: str,
    session: str,
) -> dict[str, Any] | None:
    answer_key = str(row.get("answer_key") or "").strip()
    intent_id = str(row.get("intent_id") or "").strip().lower()
    if not answer_key:
        return None

    if answer_key.startswith("service-"):
        service_intent = answer_key.split("-", 1)[1]
        text = await repo.load_capability_response(conn, tenant_id, service_intent)
        if text:
            return fmt.ok_response(text, "structured_business", trace_id)
        return None

    sku = _sku_from_answer_key(answer_key)
    if sku and answer_key.startswith("Products_Prices:"):
        product = await repo.resolve_product_by_sku(conn, tenant_id, sku)
        if product:
            return fmt.ok_response(
                f"{product['canonical_name']}: {fmt.format_price(product, country)}",
                "structured_price",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )

    if sku and answer_key.startswith("Product_Cards:"):
        product = await repo.resolve_product_by_sku(conn, tenant_id, sku)
        if product:
            card = await repo.load_product_card(conn, tenant_id, sku)
            media = fmt.build_media_payload(card, [], "photo")
            return fmt.ok_response(
                fmt.format_product_card(card, product),
                "structured_card",
                trace_id,
                product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
                media=media,
                context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
            )

    if sku and answer_key.startswith("Resource_Links:"):
        product = await repo.resolve_product_by_sku(conn, tenant_id, sku)
        if not product:
            return None
        card = await repo.load_product_card(conn, tenant_id, sku)
        resources = await repo.load_product_resources(conn, tenant_id, sku)
        kind = "video" if "video" in intent_id else "photo"
        media = fmt.build_media_payload(card, resources, kind)
        if kind == "video" and media.get("videos"):
            text = f"Видео по {product['canonical_name']}: {media['videos'][0]['url']}"
            mode = "structured_video"
        elif media.get("photo_url"):
            text = f"Отправляю фото: {product['canonical_name']}"
            mode = "structured_photo"
        else:
            text = f"По {product['canonical_name']} пока нет подходящего материала в базе."
            mode = "clarification"
        return fmt.ok_response(
            text,
            mode,
            trace_id,
            product={"sku": product["sku"], "canonical_name": product["canonical_name"]},
            media=media,
            context={"last_product_sku": product["sku"], "last_product_name": product["canonical_name"]},
        )

    if answer_key == "Product_Comparisons" or intent_id == "product_compare":
        entity = str(row.get("entity_id") or "")
        pair = re.split(r"[:|]", entity) if entity else []
        if len(pair) >= 2:
            left = await repo.resolve_product_by_sku(conn, tenant_id, pair[0].strip())
            right = await repo.resolve_product_by_sku(conn, tenant_id, pair[1].strip())
            if left and right:
                comparison = await repo.find_product_comparison(conn, tenant_id, left["sku"], right["sku"])
                if comparison:
                    return fmt.ok_response(
                        str(comparison.get("answer_text") or comparison.get("title") or "").strip(),
                        "structured_comparison",
                        trace_id,
                    )
    return None
