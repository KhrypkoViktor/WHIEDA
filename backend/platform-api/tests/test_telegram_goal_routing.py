"""Regression tests for Telegram first-turn and goal routing (Block C)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.gap import GAP_TEXTS
from app.advisor.sql.engine import (
    CALCULATOR_INSTRUCTION,
    DISCOMFORT_BOUNDARY_TEXT,
    PRODUCT_SELECTION_FALLBACK,
    INCOME_QUESTION_FALLBACK,
    run_structured_query,
)
from app.advisor.sql.text import detect_service_intent, is_unsupported_topic


ACTIVATOR = {
    "sku": "LOCAL-ACT",
    "canonical_name": "Активатор клеток",
    "retail_price_byn": 1750,
    "partner_price_byn": 1050,
    "partner_w": 300,
}
BEM = {
    "sku": "LOCAL-BEM",
    "canonical_name": "Magic Foherb",
    "retail_price_byn": 2275,
    "partner_price_byn": 1400,
    "partner_w": 650,
}


@asynccontextmanager
async def _fake_conn(_tenant_id: str):
    yield object()


@pytest.mark.asyncio
async def test_typo_greeting_prive(whieda_tenant):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value=None),
        ):
            result = await run_structured_query(
                whieda_tenant, {"question": "приве", "session": "tg-greet", "surface": "telegram"}, "tg-1"
            )
    assert result["answer_mode"] == "structured_business"
    assert "Здравств" in result["answer_text"]


@pytest.mark.parametrize(
    "question",
    [
        "че ты можешь?",
        "че ты можеь?",
        "что можешь",
        "можешь?",
    ],
)
@pytest.mark.asyncio
async def test_slang_capabilities_menu(whieda_tenant, question):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value="Меню возможностей из базы."),
        ):
            result = await run_structured_query(
                whieda_tenant, {"question": question, "session": "tg-cap", "surface": "telegram"}, "tg-2"
            )
    assert result["answer_mode"] == "structured_business"
    assert "Меню возможностей" in result["answer_text"]


@pytest.mark.asyncio
async def test_activator_clarification_accepts_base_choice_followup(whieda_tenant):
    stored = {
        "pending_product_clarification": "activator_variant",
        "pending_base_sku": "M015-00",
        "pending_pro_sku": "EU-N000031-25",
    }
    base_product = {
        "sku": "M015-00",
        "canonical_name": "Активатор клеток",
        "retail_price_byn": 1750,
        "partner_price_byn": 1050,
        "partner_w": 300,
    }
    base_card = {"primary_image_url": "https://example.test/activator.jpg", "what_it_is": "Тестовый активатор"}

    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
            with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.resolve_product_by_sku", AsyncMock(return_value=base_product)):
                        with patch("app.advisor.sql.engine.repo.load_product_card", AsyncMock(return_value=base_card)):
                            with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()) as merge:
                                result = await run_structured_query(
                                    whieda_tenant,
                                    {"question": "обычный", "session": "tg-act-choice", "surface": "telegram"},
                                    "tg-2b",
                                )
    assert result["answer_mode"] == "structured_card"
    assert result["product"]["sku"] == "M015-00"
    assert "Активатор клеток" in result["answer_text"]
    merge.assert_awaited()


@pytest.mark.asyncio
async def test_beer_request_is_out_of_scope_not_capabilities(whieda_tenant):
    assert detect_service_intent("пивка хочешь?") is None
    assert is_unsupported_topic("пивка хочешь?")
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.gap.emit_gap_response", AsyncMock()) as gap:
            gap.side_effect = lambda *args, **kwargs: {
                "ok": True,
                "answer_mode": "clarification",
                "gap_kind": kwargs.get("gap_kind"),
                "answer_text": GAP_TEXTS["unsupported_topic"],
            }
            result = await run_structured_query(
                whieda_tenant,
                {"question": "пивка хочешь?", "session": "tg-beer", "surface": "telegram"},
                "tg-3",
            )
    assert result["gap_kind"] == "unsupported_topic"
    assert "Выберите направление" in result["answer_text"]


@pytest.mark.asyncio
async def test_calculator_instruction(whieda_tenant):
    result = await run_structured_query(
        whieda_tenant, {"question": "калькулятор", "session": "tg-calc", "surface": "telegram"}, "tg-4"
    )
    assert result["answer_mode"] == "structured_business"
    assert "Посчитай:" in result["answer_text"]
    assert result["answer_text"] == CALCULATOR_INSTRUCTION


@pytest.mark.asyncio
async def test_start_options_route(whieda_tenant):
    templates = [{"title": "Старт 500 PV", "goal": "pv_target"}]
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
            with patch(
                "app.advisor.sql.engine.repo.load_starter_basket_templates",
                AsyncMock(return_value=templates),
            ):
                with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()):
                    result = await run_structured_query(
                        whieda_tenant,
                        {"question": "какие виды входа?", "session": "tg-start", "surface": "telegram"},
                        "tg-5",
                    )
    assert result["answer_mode"] == "structured_starter_basket"
    assert "стартов" in result["answer_text"].casefold()


@pytest.mark.asyncio
async def test_company_intro_not_unknown_product(whieda_tenant):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
            with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                result = await run_structured_query(
                    whieda_tenant,
                    {"question": "расскажи о компании", "session": "tg-co", "surface": "telegram"},
                    "tg-6",
                )
    assert result["answer_mode"] == "structured_business"
    assert result.get("gap_kind") is None
    assert "WHIEDA" in result["answer_text"]
    assert "каталог" not in result["answer_text"].casefold()


@pytest.mark.asyncio
async def test_income_question_compliant_route(whieda_tenant):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
            result = await run_structured_query(
                whieda_tenant,
                {"question": "как заработать?", "session": "tg-income", "surface": "telegram"},
                "tg-7",
            )
    assert result["answer_mode"] == "structured_business"
    assert result["answer_text"] == INCOME_QUESTION_FALLBACK
    assert "не обещает" in result["answer_text"].casefold()


@pytest.mark.parametrize("question", ["болят колени", "болит спина", "хочу совет"])
@pytest.mark.asyncio
async def test_discomfort_boundary_not_catalogue_miss(whieda_tenant, question):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.gap.emit_gap_response", AsyncMock()) as gap:
            gap.side_effect = lambda *args, **kwargs: {
                "ok": True,
                "answer_mode": kwargs.get("answer_mode", "clarification"),
                "gap_kind": kwargs.get("gap_kind"),
                "answer_text": kwargs.get("text") or DISCOMFORT_BOUNDARY_TEXT,
            }
            result = await run_structured_query(
                whieda_tenant,
                {"question": question, "session": f"tg-dis-{question[:6]}", "surface": "telegram"},
                "tg-8",
            )
    assert result["gap_kind"] == "medical_or_safety_boundary"
    assert "выберите направление" in result["answer_text"].casefold()
    assert "каталог" not in result["answer_text"].casefold()


@pytest.mark.asyncio
async def test_product_selection_is_a_safe_goal_prompt_not_catalogue_miss(whieda_tenant):
    result = await run_structured_query(
        whieda_tenant,
        {"question": "подобрать товар", "session": "tg-select", "surface": "telegram"},
        "tg-select",
    )
    assert result["answer_mode"] == "clarification"
    assert result["answer_text"] == PRODUCT_SELECTION_FALLBACK
    assert "выберите направление" in result["answer_text"].casefold()


@pytest.mark.asyncio
async def test_cart_remove_recalculates_active_cart(whieda_tenant):
    stored = {
        "active_cart": [
            {
                "sku": "LOCAL-ACT",
                "canonical_name": "Активатор клеток",
                "retail_price_byn": 1750,
                "partner_price_byn": 1050,
                "partner_w": 300,
            },
            {
                "sku": "LOCAL-BEM",
                "canonical_name": "Magic Foherb",
                "retail_price_byn": 2275,
                "partner_price_byn": 1400,
                "partner_w": 650,
            },
        ]
    }

    async def resolve(_conn, _tenant, question, *_args, **_kwargs):
        if "активатор" in question.casefold():
            return ACTIVATOR
        if "бэм" in question.casefold() or "bem" in question.casefold():
            return BEM
        return None

    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
            with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()) as merge:
                with patch("app.advisor.sql.engine.product_resolver.resolve_product", side_effect=resolve):
                    result = await run_structured_query(
                        whieda_tenant,
                        {"question": "убери активатор", "session": "tg-cart", "surface": "telegram"},
                        "tg-9",
                    )
    assert result["answer_mode"] == "structured_cart"
    assert "Magic Foherb" in result["answer_text"]
    assert "1. Magic Foherb" in result["answer_text"]
    merge.assert_awaited()
    assert len(merge.await_args.args[3]["active_cart"]) == 1


@pytest.mark.asyncio
async def test_cart_remove_without_active_cart(whieda_tenant):
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
            result = await run_structured_query(
                whieda_tenant,
                {"question": "убери активатор", "session": "tg-cart-empty", "surface": "telegram"},
                "tg-10",
            )
    assert result["answer_mode"] == "clarification"
    assert "нет активной корзины" in result["answer_text"].casefold()
