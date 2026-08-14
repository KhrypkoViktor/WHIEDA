"""Service-intent fuzz matrix for Telegram first-turn phrases (local regression lab)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.gap import GAP_TEXTS
from app.advisor.sql.engine import SERVICE_FALLBACKS, run_structured_query
from app.advisor.sql.text import detect_service_intent, is_unsupported_topic


@asynccontextmanager
async def _fake_conn(_tenant_id: str):
    yield object()


INTENT_CASES: list[tuple[str, str | None]] = [
    ("привет", "greeting"),
    ("здарова", "greeting"),
    ("здрасьте", "greeting"),
    ("здрасьte", "greeting"),
    ("хай", "greeting"),
    ("добрый день", "greeting"),
    ("что можешь", "capabilities"),
    ("че ты можеь", "capabilities"),
    ("а что моешь", "capabilities"),
    ("можешь?", "capabilities"),
    ("помощь", "help"),
    ("что умеешь", "capabilities"),
    ("какие есть товары", None),
    ("какой товар есть", None),
    ("любой товар", None),
    # Telegram navigation opens the catalog before advisor free-text routing.
    ("покажи любой товар", None),
    ("пивка хочешь", None),
    ("ты живой", "smalltalk_status"),
    ("как дела", "smalltalk_status"),
]


@pytest.mark.parametrize("phrase,expected_intent", INTENT_CASES)
def test_service_intent_detection_matrix(phrase: str, expected_intent: str | None) -> None:
    assert detect_service_intent(phrase) == expected_intent


@pytest.mark.parametrize(
    "phrase,must_contain,must_not_contain",
    [
        ("привет", ["Здравств"], ["Traceback"]),
        ("здарова", ["Здравств"], ["Traceback"]),
        ("здрасьте", ["Здравств"], ["Traceback"]),
        ("здрасьte", ["Здравств"], ["Traceback"]),
        ("хай", ["Здравств"], ["Traceback"]),
        ("добрый день", ["Здравств"], ["Traceback"]),
        ("что можешь", ["Могу"], ["Traceback"]),
        ("че ты можеь", ["Могу"], ["Traceback"]),
        ("а что моешь", ["Могу"], ["Traceback"]),
        ("можешь?", ["Могу"], ["Traceback"]),
        ("помощь", ["товар"], ["Traceback"]),
        ("что умеешь", ["Могу"], ["Traceback"]),
        ("ты живой", ["на связи"], ["Traceback"]),
        ("как дела", ["на связи"], ["Traceback"]),
    ],
)
@pytest.mark.asyncio
async def test_service_intent_routing_matrix(
    whieda_tenant,
    phrase: str,
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value=None),
        ):
            result = await run_structured_query(
                whieda_tenant,
                {"question": phrase, "session": f"tg-fuzz-{phrase[:8]}", "surface": "telegram"},
                "tg-fuzz",
            )
    assert result["answer_mode"] == "structured_business"
    blob = result["answer_text"]
    for needle in must_contain:
        assert needle.casefold() in blob.casefold()
    for forbidden in must_not_contain:
        assert forbidden.casefold() not in blob.casefold()


@pytest.mark.parametrize(
    "phrase",
    ["пивка хочешь", "пивка хочешь?"],
)
@pytest.mark.asyncio
async def test_casual_oos_beer_not_capabilities(whieda_tenant, phrase: str) -> None:
    assert detect_service_intent(phrase) is None
    assert is_unsupported_topic(phrase)
    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.gap.emit_gap_response", AsyncMock()) as gap:
            gap.side_effect = lambda *args, **kwargs: {
                "ok": True,
                "answer_mode": "clarification",
                "gap_kind": kwargs.get("gap_kind"),
                "answer_text": kwargs.get("text") or GAP_TEXTS["unsupported_topic"],
            }
            result = await run_structured_query(
                whieda_tenant,
                {"question": phrase, "session": "tg-oos-beer", "surface": "telegram"},
                "tg-oos",
            )
    assert result["gap_kind"] == "unsupported_topic"
    assert "Выберите направление" in result["answer_text"]
    assert SERVICE_FALLBACKS["capabilities"].casefold() not in result["answer_text"].casefold()


@pytest.mark.asyncio
async def test_activator_clarification_pro_choice(whieda_tenant):
    stored = {
        "pending_product_clarification": "activator_variant",
        "pending_base_sku": "M015-00",
        "pending_pro_sku": "EU-N000031-25",
    }
    pro_product = {
        "sku": "EU-N000031-25",
        "canonical_name": "Активатор клеток PRO",
        "retail_price_byn": 2100,
        "partner_price_byn": 1260,
        "partner_w": 360,
    }
    pro_card = {"primary_image_url": "https://example.test/activator-pro.jpg", "what_it_is": "PRO"}

    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
            with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.resolve_product_by_sku", AsyncMock(return_value=pro_product)):
                        with patch("app.advisor.sql.engine.repo.load_product_card", AsyncMock(return_value=pro_card)):
                            with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()):
                                result = await run_structured_query(
                            whieda_tenant,
                            {"question": "pro", "session": "tg-act-pro", "surface": "telegram"},
                            "tg-pro",
                        )
    assert result["answer_mode"] == "structured_card"
    assert result["product"]["sku"] == "EU-N000031-25"


@pytest.mark.asyncio
async def test_activator_clarification_base_price_followup(whieda_tenant):
    stored = {
        "pending_product_clarification": "activator_variant",
        "pending_base_sku": "M015-00",
        "pending_pro_sku": "EU-N000031-25",
        "last_product_sku": "M015-00",
        "last_product_name": "Активатор клеток",
    }
    base_product = {
        "sku": "M015-00",
        "canonical_name": "Активатор клеток",
        "retail_price_byn": 1750,
        "partner_price_byn": 1050,
        "partner_w": 300,
    }

    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
            with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.resolve_product_by_sku", AsyncMock(return_value=base_product)):
                        with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()):
                            result = await run_structured_query(
                                whieda_tenant,
                                {"question": "обычный цена", "session": "tg-act-price", "surface": "telegram"},
                                "tg-price",
                            )
    assert result["answer_mode"] in {"structured_price", "structured_card"}
    assert "BYN" in result["answer_text"] or "1750" in result["answer_text"]


@pytest.mark.asyncio
async def test_activator_clarification_base_photo_followup(whieda_tenant):
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
    base_card = {"primary_image_url": "https://example.test/activator.jpg", "what_it_is": "base"}

    with patch("app.advisor.sql.engine.tenant_connection", _fake_conn):
        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value=stored)):
            with patch("app.advisor.sql.engine.repo.find_business_objection", AsyncMock(return_value=None)):
                with patch("app.advisor.sql.engine.repo.find_business_faq", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.resolve_product_by_sku", AsyncMock(return_value=base_product)):
                        with patch("app.advisor.sql.engine.repo.load_product_card", AsyncMock(return_value=base_card)):
                            with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()):
                                result = await run_structured_query(
                            whieda_tenant,
                            {"question": "обычный фото", "session": "tg-act-photo", "surface": "telegram"},
                            "tg-photo",
                        )
    assert result["answer_mode"] in {"structured_photo", "structured_card"}
    assert "фото" in result["answer_text"].casefold() or result.get("media", {}).get("photo")
