"""Response builders for structured advisor answers."""

from __future__ import annotations

from typing import Any

MISSING_PRICE_TEXT = "Цена для этого товара пока не опубликована"
MISSING_CERTIFICATE_TEXT = "Сертификат для этого товара пока не добавлен"
MISSING_PHOTO_TEXT = "Фото для этого товара пока не прикреплено"


def empty_media() -> dict[str, Any]:
    return {"photo_url": None, "videos": [], "documents": []}


def _positive_amount(value: Any) -> bool:
    if value is None:
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


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
            if _positive_amount(partner):
                parts.append(f"Для партнёра: {partner} BYN")
            if _positive_amount(w):
                parts.append(f"PV {w}")
            return ", ".join(parts) if parts else MISSING_PRICE_TEXT
        if retail_only and not partner_only:
            return f"Розничная цена: {byn} BYN" if _positive_amount(byn) else MISSING_PRICE_TEXT
        parts = []
        if _positive_amount(byn):
            parts.append(f"Розничная цена: {byn} BYN")
        if _positive_amount(partner):
            parts.append(f"Для партнёра: {partner} BYN")
        if _positive_amount(w):
            parts.append(f"PV {w}")
        return ", ".join(parts) if parts else MISSING_PRICE_TEXT
    rub = product.get("retail_price_rub")
    pv = product.get("partner_w")
    parts = []
    if _positive_amount(rub):
        parts.append(f"Розничная цена: {rub} RUB")
    if _positive_amount(pv):
        parts.append(f"PV {pv}")
    return ", ".join(parts) if parts else MISSING_PRICE_TEXT


def format_product_card(
    card: dict[str, Any] | None,
    product: dict[str, Any],
    *,
    compact: bool = False,
) -> str:
    from app.advisor.telegram_card import render_telegram_product_card

    return render_telegram_product_card(card, product, compact=compact)


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
    gap_kind: str | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    if gap_kind:
        payload["gap_kind"] = gap_kind
    if next_steps:
        payload["next_steps"] = next_steps[:3]
    return payload
