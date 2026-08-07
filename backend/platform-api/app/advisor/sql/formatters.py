"""Response builders for structured advisor answers."""

from __future__ import annotations

from typing import Any

def empty_media() -> dict[str, Any]:
    return {"photo_url": None, "videos": [], "documents": []}


def format_price(
    product: dict[str, Any],
    country: str,
    *,
    partner_only: bool = False,
    retail_only: bool = False,
) -> str:
    if country == "BY":
        byn = product.get("retail_price_byn")
        partner = product.get("partner_price_byn")
        w = product.get("partner_w") or product.get("partner_price_byn")
        if partner_only and not retail_only:
            parts: list[str] = []
            if partner:
                parts.append(f"Для партнёра: {partner} BYN")
            if w:
                parts.append(f"PV {w}")
            return ", ".join(parts) if parts else "цена уточняется"
        if retail_only and not partner_only:
            return f"Розничная цена: {byn} BYN" if byn else "цена уточняется"
        parts = []
        if byn:
            parts.append(f"Розничная цена: {byn} BYN")
        if partner:
            parts.append(f"Для партнёра: {partner} BYN")
        if w:
            parts.append(f"PV {w}")
        return ", ".join(parts) if parts else "цена уточняется"
    rub = product.get("retail_price_rub")
    return f"розница {rub} RUB" if rub else "цена уточняется"


def format_product_card(card: dict[str, Any] | None, product: dict[str, Any]) -> str:
    if card:
        parts = [
            str(card.get("what_it_is") or "").strip(),
            str(card.get("who_asks_about_it") or "").strip(),
            str(card.get("common_use_cases") or "").strip(),
            str(card.get("how_to_use_short") or "").strip(),
        ]
        text = "\n\n".join(part for part in parts if part)
        if text:
            return text
    return str(product.get("canonical_name") or product.get("sku") or "").strip()


def build_media_payload(
    card: dict[str, Any] | None,
    resources: list[dict[str, Any]],
    resource_kind: str,
) -> dict[str, Any]:
    photo_url = None
    videos: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []

    if resource_kind == "photo":
        if card and card.get("primary_image_url"):
            photo_url = card["primary_image_url"]
        else:
            for row in resources:
                rtype = str(row.get("resource_type") or "").lower()
                if rtype in {"image", "photo", "picture", "img"} and row.get("url"):
                    photo_url = row["url"]
                    break
    elif resource_kind == "video":
        for row in resources:
            rtype = str(row.get("resource_type") or "").lower()
            if rtype in {"video", "youtube"} and row.get("url"):
                videos.append({"url": row["url"], "title": row.get("title")})
    elif resource_kind == "certificate":
        for row in resources:
            rtype = str(row.get("resource_type") or "").lower()
            topic = str(row.get("topic") or "").lower()
            if rtype in {"certificate", "pdf", "document"} or topic == "certificates":
                documents.append({"url": row["url"], "title": row.get("title")})

    return {"photo_url": photo_url, "videos": videos, "documents": documents}


def ok_response(
    answer_text: str,
    answer_mode: str,
    trace_id: str,
    *,
    product: dict[str, Any] | None = None,
    media: dict[str, Any] | None = None,
    clarifications: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "answer_text": answer_text,
        "answer_mode": answer_mode,
        "route": "structured",
        "product": product,
        "media": media if media is not None else empty_media(),
        "clarifications": clarifications or [],
        "sources": [],
        "context": context or {},
        "error_id": None,
        "trace_id": trace_id,
    }
