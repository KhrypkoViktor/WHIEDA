"""Media assertion helpers for parity smoke."""

from __future__ import annotations

import pytest

from app.advisor.parity.media_assertions import (
    evaluate_media_for_case,
    media_is_empty,
    media_urls,
    product_sku,
)


def test_media_is_empty():
    assert media_is_empty({"photo_url": None, "videos": [], "documents": []})
    assert not media_is_empty({"photo_url": "http://x", "videos": [], "documents": []})
    assert not media_is_empty({"photo_url": None, "videos": [{"url": "v"}], "documents": []})


def test_service_greeting_no_media():
    errors = evaluate_media_for_case(
        expected_intent="greeting",
        answer_mode="structured_business",
        media={"photo_url": None, "videos": [{"url": "bad"}], "documents": []},
        product=None,
        context=None,
    )
    assert "service_media_pollution" in errors


def test_product_context_match():
    errors = evaluate_media_for_case(
        expected_intent="product_photo",
        answer_mode="structured_photo",
        media={"photo_url": "http://x", "videos": [], "documents": []},
        product={"sku": "SKU-A"},
        context={"last_product_sku": "SKU-B"},
    )
    assert "context_product_mismatch" in errors


def test_media_urls_and_sku():
    urls = media_urls({"videos": [{"url": "https://v"}], "documents": []})
    assert urls == ["https://v"]
    assert product_sku({"sku": " ABC "}) == "abc"
