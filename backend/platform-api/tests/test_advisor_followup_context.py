"""Conversation follow-up uses last product from context."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql.engine import run_structured_query


@pytest.mark.asyncio
async def test_followup_photo_uses_stored_product(whieda_tenant):
    product = {
        "sku": "SKU-SPIRULINA",
        "canonical_name": "Спирулина",
        "retail_price_byn": 245,
    }
    card = {"primary_image_url": "https://example/photo.jpg"}
    resources = [{"resource_type": "image", "url": "https://example/photo.jpg"}]
    stored = {"last_product_sku": "SKU-SPIRULINA", "last_product_name": "Спирулина"}

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    patches = [
        patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection),
        patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)),
        patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)),
        patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)),
        patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)),
        patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)),
        patch("app.advisor.sql.engine.repo.resolve_product_by_sku", AsyncMock(return_value=product)),
        patch("app.advisor.sql.engine.repo.load_product_card", AsyncMock(return_value=card)),
        patch("app.advisor.sql.engine.repo.load_product_resources", AsyncMock(return_value=resources)),
        patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()),
    ]

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
        result = await run_structured_query(
            whieda_tenant,
            {"question": "фото", "session": "followup-1"},
            "trace-fu",
        )

    assert result["answer_mode"] == "structured_photo"
    assert result["product"]["sku"] == "SKU-SPIRULINA"
    assert result["media"]["photo_url"] == "https://example/photo.jpg"


@pytest.mark.asyncio
async def test_service_greeting_never_has_media(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value="Здравствуйте"),
        ):
            result = await run_structured_query(
                whieda_tenant,
                {"question": "привет", "session": "greet-1"},
                "trace-g",
            )

    assert result["media"]["videos"] == []
    assert result["media"]["photo_url"] is None
