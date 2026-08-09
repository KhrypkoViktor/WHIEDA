"""Regression: product alias vs business FAQ and comparison session context."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql.engine import run_structured_query
from app.advisor.sql.repository import find_business_faq
from app.advisor.sql.text import is_product_definition_question


PV_FAQ_ROW = {
    "faq_id": "L-FAQ-PV",
    "title": "Что такое PV",
    "answer_text": "PV — локальный тест.",
    "aliases": "pv|баллы|личный объём",
    "priority": 100,
}

BEM_PRODUCT = {
    "sku": "LOCAL-BEM",
    "canonical_name": "Magic Foherb",
    "retail_price_byn": 2275,
    "partner_w": 650,
}
BEM_CARD = {
    "what_it_is": "Локальный тест БЭМ.",
    "who_asks_about_it": "Core parity lab.",
    "primary_image_url": "https://example.invalid/local/bem.jpg",
}

BASE_ACTIVATOR = {
    "sku": "LOCAL-ACT",
    "canonical_name": "Активатор клеток",
    "retail_price_byn": 1750,
    "partner_w": 300,
}
PRO_ACTIVATOR = {
    "sku": "LOCAL-PRO",
    "canonical_name": "Активатор клеток PRO",
    "retail_price_byn": 2100,
    "partner_w": 350,
}
COMPARISON_LAYER = {
    "answer_text": "Активатор клеток vs PRO: базовая версия для дома, PRO — усиленный.",
    "title": "Активатор vs PRO",
}


@pytest.mark.asyncio
async def test_find_business_faq_pv_matches_pv_question():
    conn = object()

    async def fake_fetch_all(_conn, _sql, _params):
        return [PV_FAQ_ROW]

    with patch("app.advisor.sql.repository.fetch_all", fake_fetch_all):
        row = await find_business_faq(conn, "whieda", "что такое pv")

    assert row is not None
    assert "PV" in row["title"]


@pytest.mark.asyncio
async def test_find_business_faq_does_not_match_bem_on_generic_tokens():
    conn = object()

    async def fake_fetch_all(_conn, _sql, _params):
        return [PV_FAQ_ROW]

    with patch("app.advisor.sql.repository.fetch_all", fake_fetch_all):
        row = await find_business_faq(conn, "whieda", "что такое бэм")

    assert row is None


def test_is_product_definition_question_for_bem():
    assert is_product_definition_question("что такое бэм")
    assert not is_product_definition_question("что такое pv")


@pytest.mark.asyncio
async def test_bem_question_returns_product_card_not_faq(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch(
                        "app.advisor.sql.engine.repo.find_business_faq",
                        AsyncMock(return_value=PV_FAQ_ROW),
                    ):
                        with patch(
                            "app.advisor.sql.engine._resolve_product",
                            AsyncMock(return_value=BEM_PRODUCT),
                        ):
                            with patch(
                                "app.advisor.sql.engine.repo.load_product_card",
                                AsyncMock(return_value=BEM_CARD),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.session_ctx.merge_session_context",
                                    AsyncMock(),
                                ):
                                    result = await run_structured_query(
                                        whieda_tenant,
                                        {"question": "что такое бэм", "session": "bem-1"},
                                        "trace-bem",
                                    )

    assert result["answer_mode"] == "structured_card"
    assert result["product"]["canonical_name"] == "Magic Foherb"
    assert "PV" not in result["answer_text"]


@pytest.mark.asyncio
async def test_pv_question_returns_business_faq(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch(
                        "app.advisor.sql.engine.repo.find_business_faq",
                        AsyncMock(return_value=PV_FAQ_ROW),
                    ):
                        result = await run_structured_query(
                            whieda_tenant,
                            {"question": "что такое pv", "session": "pv-1"},
                            "trace-pv",
                        )

    assert result["answer_mode"] == "structured_business_faq"
    assert "PV" in result["answer_text"]


@pytest.mark.asyncio
async def test_comparison_layer_sets_left_product_context(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    merge = AsyncMock()

    async def fake_resolve(conn, tenant_id, name, sku, slug, *, repo):
        normalized = str(name).lower()
        if "pro" in normalized:
            return PRO_ACTIVATOR
        if "активатор" in normalized:
            return BASE_ACTIVATOR
        return None

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch(
                            "app.advisor.sql.engine.product_resolver.resolve_product",
                            fake_resolve,
                        ):
                            with patch(
                                "app.advisor.sql.engine.repo.find_product_comparison",
                                AsyncMock(return_value=COMPARISON_LAYER),
                            ):
                                with patch("app.advisor.sql.engine.session_ctx.merge_session_context", merge):
                                    result = await run_structured_query(
                                        whieda_tenant,
                                        {
                                            "question": "сравни активатор клеток и активатор pro",
                                            "session": "cmp-1",
                                        },
                                        "trace-cmp",
                                    )

    assert result["answer_mode"] == "structured_comparison_layer"
    assert result["product"]["sku"] == "LOCAL-ACT"
    assert result["product"]["canonical_name"] == "Активатор клеток"
    assert result["context"]["last_product_sku"] == "LOCAL-ACT"
    merge.assert_awaited()


@pytest.mark.asyncio
async def test_comparison_then_price_uses_base_activator(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    stored = {"last_product_sku": "LOCAL-ACT", "last_product_name": "Активатор клеток"}

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.repo.resolve_product_by_sku",
                                AsyncMock(return_value=BASE_ACTIVATOR),
                            ):
                                with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()):
                                    result = await run_structured_query(
                                        whieda_tenant,
                                        {"question": "цена", "session": "cmp-1"},
                                        "trace-price",
                                    )

    assert result["answer_mode"] == "structured_price"
    assert result["product"]["sku"] == "LOCAL-ACT"
    assert "PRO" not in result["product"]["canonical_name"]


@pytest.mark.asyncio
async def test_comparison_then_photo_uses_base_activator(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    stored = {"last_product_sku": "LOCAL-ACT", "last_product_name": "Активатор клеток"}
    card = {"primary_image_url": "https://example.invalid/local/act.jpg"}

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                            with patch(
                                "app.advisor.sql.engine.repo.resolve_product_by_sku",
                                AsyncMock(return_value=BASE_ACTIVATOR),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_product_card",
                                    AsyncMock(return_value=card),
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
                                                {"question": "фото", "session": "cmp-1"},
                                                "trace-photo",
                                            )

    assert result["answer_mode"] == "structured_photo"
    assert result["product"]["sku"] == "LOCAL-ACT"


@pytest.mark.asyncio
async def test_details_about_activator_returns_product_card(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch(
                            "app.advisor.sql.engine._resolve_product",
                            AsyncMock(return_value=BASE_ACTIVATOR),
                        ):
                            with patch(
                                "app.advisor.sql.engine.try_ambiguity_clarification",
                                AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.repo.load_product_detail",
                                    AsyncMock(return_value=None),
                                ):
                                    with patch(
                                        "app.advisor.sql.engine.repo.load_product_card",
                                        AsyncMock(return_value={"what_it_is": "Тестовый активатор."}),
                                    ):
                                        with patch(
                                            "app.advisor.sql.engine.session_ctx.merge_session_context",
                                            AsyncMock(),
                                        ):
                                            result = await run_structured_query(
                                                whieda_tenant,
                                                {
                                                    "question": "расскажи подробнее про активатор",
                                                    "session": "details-1",
                                                },
                                                "trace-details",
                                            )

    assert result["answer_mode"] == "structured_card"
    assert result["product"]["canonical_name"] == "Активатор клеток"


@pytest.mark.asyncio
async def test_diff_from_compare_activator_vs_pro(whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    async def fake_resolve(conn, tenant_id, name, sku, slug, *, repo):
        normalized = str(name).lower()
        if normalized.strip() == "pro" or " pro" in normalized or normalized.endswith(" pro"):
            return PRO_ACTIVATOR
        if "активатор" in normalized:
            return BASE_ACTIVATOR
        return None

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                        with patch(
                            "app.advisor.sql.engine.product_resolver.resolve_product",
                            fake_resolve,
                        ):
                            with patch(
                                "app.advisor.sql.engine.repo.find_product_comparison",
                                AsyncMock(return_value=COMPARISON_LAYER),
                            ):
                                with patch(
                                    "app.advisor.sql.engine.session_ctx.merge_session_context",
                                    AsyncMock(),
                                ):
                                    result = await run_structured_query(
                                        whieda_tenant,
                                        {
                                            "question": "чем отличается активатор от pro",
                                            "session": "diff-1",
                                        },
                                        "trace-diff",
                                    )

    assert result["answer_mode"] == "structured_comparison_layer"
    assert "PRO" in result["answer_text"]
    assert result["product"]["sku"] == "LOCAL-ACT"
