from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql.engine import run_structured_query
from app.advisor.sql.text import has_pro_marker


@pytest.mark.asyncio
async def test_engine_greeting_uses_capability_db(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        conn = object()
        yield conn

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value="Привет из базы"),
        ):
            result = await run_structured_query(whieda_tenant, {"question": "привет"}, "t1")

    assert result["answer_mode"] == "structured_business"
    assert "Привет из базы" in result["answer_text"]


@pytest.mark.asyncio
async def test_engine_price_without_product_returns_clarification(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        conn = object()
        yield conn

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch(
                "app.advisor.sql.engine.session_ctx.load_session_context",
                AsyncMock(return_value={}),
            ):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch(
                            "app.advisor.sql.engine.repo.load_clarification_prompt",
                            AsyncMock(return_value="Какой товар?"),
                        ):
                            with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                                result = await run_structured_query(
                                    whieda_tenant,
                                    {"question": "сколько стоит", "session": "s-price"},
                                    "t2",
                                )

    assert result["answer_mode"] == "clarification"
    assert result["answer_text"] == "Какой товар?"


@pytest.mark.asyncio
async def test_compare_skips_canonical_product_card(whieda_tenant):
    """Canonical substring match must not steal compare requests."""

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        conn = object()
        yield conn

    canonical_row = {
        "answer_key": "Product_Cards:SKU-ACTIVATOR",
        "intent_id": "product_card",
    }
    left = {"sku": "S1", "canonical_name": "Спирулина", "retail_price_byn": 245, "partner_price_byn": 200, "partner_w": 50}
    right = {
        "sku": "S2",
        "canonical_name": "Активатор клеток",
        "retail_price_byn": 1750,
        "partner_price_byn": 1500,
        "partner_w": 300,
    }

    async def fake_resolve(conn, tenant_id, name, sku, slug, *, repo):
        normalized = str(name).lower()
        if "спирулина" in normalized:
            return left
        if "активатор" in normalized:
            return right
        return None

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.find_canonical_question",
            AsyncMock(return_value=canonical_row),
        ):
            with patch(
                "app.advisor.sql.engine.session_ctx.load_session_context",
                AsyncMock(return_value={}),
            ):
                with patch(
                    "app.advisor.sql.engine.product_resolver.resolve_product",
                    fake_resolve,
                ):
                        with patch(
                            "app.advisor.sql.engine.repo.find_product_comparison",
                            AsyncMock(return_value=None),
                        ):
                            with patch(
                                "app.advisor.sql.engine.repo.load_product_card",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.find_business_objection",
                                    AsyncMock(return_value=None),
                                ):
                                    with patch(
                                        "app.advisor.sql.engine.repo.find_business_faq",
                                        AsyncMock(return_value=None),
                                    ):
                                        result = await run_structured_query(
                                            whieda_tenant,
                                            {"question": "сравни спирулина и активатор клеток"},
                                            "t-compare",
                                        )

    assert result["answer_mode"] == "structured_comparison"
    assert "По цене" in result["answer_text"]


@pytest.mark.asyncio
async def test_cart_list_skips_canonical_product_card(whieda_tenant):
    spirulina = {
        "sku": "S1",
        "canonical_name": "Спирулина",
        "retail_price_byn": 245,
        "partner_price_byn": 200,
        "partner_w": 50,
    }
    aktivator = {
        "sku": "S2",
        "canonical_name": "Активатор клеток",
        "retail_price_byn": 1750,
        "partner_price_byn": 1500,
        "partner_w": 300,
    }

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        conn = object()
        yield conn

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.find_canonical_question",
            AsyncMock(return_value={"answer_key": "Product_Cards:SKU-ACTIVATOR"}),
        ):
            with patch(
                "app.advisor.sql.engine.session_ctx.load_session_context",
                AsyncMock(return_value={}),
            ):
                with patch(
                    "app.advisor.sql.engine.product_resolver.resolve_product",
                    AsyncMock(side_effect=[spirulina, aktivator]),
                ):
                    result = await run_structured_query(
                        whieda_tenant,
                        {"question": "посчитай: спирулина, активатор клеток"},
                        "t-cart",
                    )

    assert result["answer_mode"] == "structured_cart"
    assert "Расчёт списка" in result["answer_text"]
    assert "1995" in result["answer_text"] or "245" in result["answer_text"]


@pytest.mark.asyncio
async def test_paste_returns_clarification_not_card(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.repo.load_clarification_prompt",
                                AsyncMock(return_value="Зубная или Цинфэн?"),
                            ):
                                result = await run_structured_query(
                                    whieda_tenant,
                                    {"question": "паста"},
                                    "t-paste",
                                )

    assert result["answer_mode"] == "clarification"
    assert "Цинфэн" in result["answer_text"] or "Зубная" in result["answer_text"]


@pytest.mark.asyncio
async def test_explicit_pro_photo_not_overwritten_by_comparison_context(whieda_tenant):
    left = {
        "sku": "LOCAL-ACT",
        "canonical_name": "Активатор клеток",
        "retail_price_byn": 1750,
        "partner_w": 300,
    }
    pro = {
        "sku": "LOCAL-PRO",
        "canonical_name": "Активатор клеток PRO",
        "retail_price_byn": 2275,
        "partner_w": 400,
    }

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    async def fake_resolve(_conn, _tenant_id, question, _sku, _slug):
        if has_pro_marker(question):
            return pro
        return None

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch(
                "app.advisor.sql.engine.session_ctx.load_session_context",
                AsyncMock(
                    return_value={
                        "last_product_sku": left["sku"],
                        "last_product_name": left["canonical_name"],
                    }
                ),
            ):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", fake_resolve):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_product_card",
                                    AsyncMock(return_value={"primary_image_url": "https://example.invalid/pro.jpg"}),
                                ):
                                    with patch(
                                        "app.advisor.sql.engine.repo.load_product_resources",
                                        AsyncMock(return_value=[]),
                                    ):
                                        with patch(
                                            "app.advisor.sql.engine.session_ctx.merge_session_context",
                                            AsyncMock(),
                                        ):
                                            result = await run_structured_query(
                                                whieda_tenant,
                                                {"question": "фото pro", "session": "cmp-pro-photo"},
                                                "t-pro-photo",
                                            )

    assert result["answer_mode"] == "structured_photo"
    assert result["product"]["sku"] == "LOCAL-PRO"
