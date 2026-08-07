"""Shared media/product assertions for Core advisor parity smoke."""

from __future__ import annotations

from typing import Any

SERVICE_INTENTS = frozenset(
    {
        "greeting",
        "smalltalk_status",
        "capabilities",
        "help",
    }
)


def media_is_empty(media: dict[str, Any] | None) -> bool:
    if not media:
        return True
    if media.get("photo_url"):
        return False
    videos = media.get("videos") or []
    documents = media.get("documents") or []
    return len(videos) == 0 and len(documents) == 0


def media_urls(media: dict[str, Any] | None) -> list[str]:
    if not media:
        return []
    urls: list[str] = []
    if media.get("photo_url"):
        urls.append(str(media["photo_url"]))
    for item in media.get("videos") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    for item in media.get("documents") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


def product_sku(product: dict[str, Any] | None) -> str | None:
    if not product:
        return None
    sku = product.get("sku") or product.get("product_sku")
    return str(sku).strip().lower() if sku else None


def evaluate_media_for_case(
    *,
    expected_intent: str,
    answer_mode: str | None,
    media: dict[str, Any] | None,
    product: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    mode = str(answer_mode or "")

    if expected_intent in {"greeting", "service_greeting"} or mode == "structured_business":
        if expected_intent.startswith("service") or expected_intent == "greeting":
            if not media_is_empty(media):
                errors.append("service_media_pollution")

    if expected_intent in SERVICE_INTENTS and not media_is_empty(media):
        errors.append("service_media_pollution")

    ctx_sku = (context or {}).get("last_product_sku") or (context or {}).get("product_sku")
    if ctx_sku and product_sku(product):
        if str(ctx_sku).lower() != product_sku(product):
            errors.append("context_product_mismatch")

    if expected_intent.startswith("product_") and product_sku(product) is None:
        if mode.startswith("structured_") and mode not in {"structured_business"}:
            errors.append("missing_product_on_product_intent")

    return errors
