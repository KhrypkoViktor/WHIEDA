"""Journey session bridge unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.journey.bridge import load_session_context_for_advisor


@pytest.mark.asyncio
async def test_bridge_empty_when_no_session():
    with patch("app.journey.bridge.tenant_connection") as tc:
        conn = AsyncMock()
        tc.return_value.__aenter__ = AsyncMock(return_value=conn)
        tc.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch("app.journey.bridge.fetch_one", AsyncMock(return_value=None)):
            hints = await load_session_context_for_advisor("whieda", visitor_session_id=None)
    assert hints == {}


@pytest.mark.asyncio
async def test_bridge_extracts_product_sku():
    row = {
        "context": {"last_product_sku": "wentong", "last_product_name": "Wentong"},
        "first_ref": "ladnaya",
        "journey_type": "product",
        "last_product_sku": None,
    }
    with patch("app.journey.bridge.tenant_connection") as tc:
        conn = AsyncMock()
        tc.return_value.__aenter__ = AsyncMock(return_value=conn)
        tc.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch("app.journey.bridge.fetch_one", AsyncMock(return_value=row)):
            hints = await load_session_context_for_advisor("whieda", visitor_session_id="sess-1")
    assert hints["product_sku"] == "wentong"
    assert hints["first_ref"] == "ladnaya"
