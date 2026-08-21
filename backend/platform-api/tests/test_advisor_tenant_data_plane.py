"""Tenant advisor data plane: no WHIEDA fallback or cross-tenant catalog leak."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from advisor_isolation_fixture import (
    SHARED_ALIAS,
    SHARED_SKU,
    WHIEDA_ONLY_ALIAS,
    IsolationCatalog,
    empty_tenant,
    nsp_tenant,
)
from app.advisor.service import handle_structured_query
from app.advisor.sql.engine import run_structured_query
from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.processor import handle_advisor_query
from app.telegram.update_parser import TelegramMessage


REPO = "app.advisor.sql.repository"


def _patch_catalog(catalog: IsolationCatalog):
    methods = (
        "resolve_product_by_exact_alias",
        "fetch_alias_candidates",
        "resolve_product_by_sku",
        "resolve_product_by_partial_alias",
        "resolve_activator_pro_product",
        "resolve_product_by_slug",
        "load_product_card",
        "load_product_resources",
        "find_business_faq",
        "find_canonical_question",
        "find_business_objection",
        "load_capability_response",
        "load_clarification_prompt",
        "count_catalog_products",
        "list_catalog_products",
        "load_active_promotions",
        "load_upcoming_events",
        "load_community_resources",
        "load_starter_basket_templates",
        "load_recommendation_catalog",
        "load_coach_objections",
        "find_product_comparison",
        "load_product_detail",
        "resolve_catalog_product_by_sku",
    )
    patches = [patch(f"{REPO}.{name}", getattr(catalog, name)) for name in methods]
    patches.extend(
        [
            patch("app.advisor.sql.context.load_session_context", catalog.load_session_context),
            patch("app.advisor.sql.context.merge_session_context", catalog.merge_session_context),
            patch("app.advisor.sql.engine.session_ctx.load_session_context", catalog.load_session_context),
            patch("app.advisor.sql.engine.session_ctx.merge_session_context", catalog.merge_session_context),
            patch("app.advisor.gap.record_advisor_gap", AsyncMock(return_value=None)),
        ]
    )
    return patches


@asynccontextmanager
async def _fake_conn(_tenant_id: str):
    yield object()


@contextmanager
def isolation_env(catalog: IsolationCatalog):
    cms = [patch("app.advisor.sql.engine.tenant_connection", _fake_conn)]
    cms.extend(_patch_catalog(catalog))
    entered = [cm.__enter__() for cm in cms]
    try:
        yield catalog
    finally:
        for cm in reversed(cms):
            cm.__exit__(None, None, None)
        _ = entered


@pytest.fixture
def catalog():
    return IsolationCatalog()


@pytest.mark.asyncio
async def test_nsp_does_not_see_whieda_only_alias(catalog, whieda_tenant):
    with isolation_env(catalog):
        nsp = await run_structured_query(
            nsp_tenant(),
            {"question": f"цена {WHIEDA_ONLY_ALIAS}", "session": "nsp-1"},
            "t-nsp-only",
        )
        whieda = await run_structured_query(
            whieda_tenant,
            {"question": f"цена {WHIEDA_ONLY_ALIAS}", "session": "wh-1"},
            "t-wh-only",
        )
    assert "WHIEDA" not in (nsp.get("answer_text") or "")
    assert nsp.get("product") is None or nsp["product"].get("sku") != "M015-00"
    assert whieda["product"]["sku"] == "M015-00"
    assert "Активатор" in (whieda.get("answer_text") or "")


@pytest.mark.asyncio
async def test_shared_alias_stays_tenant_local(catalog, whieda_tenant):
    with isolation_env(catalog):
        nsp = await run_structured_query(
            nsp_tenant(),
            {"question": f"цена {SHARED_ALIAS}", "session": "nsp-2"},
            "t-shared-nsp",
        )
        whieda = await run_structured_query(
            whieda_tenant,
            {"question": f"цена {SHARED_ALIAS}", "session": "wh-2"},
            "t-shared-wh",
        )
    assert nsp["product"]["sku"] == SHARED_SKU
    assert whieda["product"]["sku"] == SHARED_SKU
    assert "Спирулина NSP" in nsp["answer_text"]
    assert "99" in nsp["answer_text"]
    assert "WHIEDA" not in nsp["answer_text"]
    assert "Спирулина WHIEDA" in whieda["answer_text"]
    assert "45" in whieda["answer_text"]


@pytest.mark.asyncio
async def test_photo_pdf_certificate_and_followup_keep_tenant(catalog, whieda_tenant):
    with isolation_env(catalog):
        nsp_photo = await run_structured_query(
            nsp_tenant(),
            {"question": f"фото {SHARED_ALIAS}", "session": "nsp-fu"},
            "t-photo-nsp",
        )
        nsp_follow = await run_structured_query(
            nsp_tenant(),
            {"question": "сертификат", "session": "nsp-fu"},
            "t-cert-nsp",
        )
        whieda_pdf = await run_structured_query(
            whieda_tenant,
            {"question": f"pdf {SHARED_ALIAS}", "session": "wh-fu"},
            "t-pdf-wh",
        )
    assert nsp_photo["media"]["photo_url"] == "https://cdn.nsp.example/spirulina.jpg"
    assert "whieda" not in str(nsp_photo["media"]).lower()
    assert nsp_follow["product"]["sku"] == SHARED_SKU
    assert any("nsp.example/cert" in str(item) for item in nsp_follow["media"].get("documents") or [])
    assert any("whieda.example" in str(item) for item in whieda_pdf["media"].get("documents") or [])


@pytest.mark.asyncio
async def test_empty_catalog_does_not_fall_back_to_whieda(catalog):
    with isolation_env(catalog):
        result = await run_structured_query(
            empty_tenant(),
            {"question": f"цена {SHARED_ALIAS}", "session": "empty-1"},
            "t-empty",
        )
        greeting = await run_structured_query(
            empty_tenant(),
            {"question": "привет", "session": "empty-2"},
            "t-empty-hi",
        )
    assert "WHIEDA" not in (result.get("answer_text") or "")
    assert "WHIEDA" not in (greeting.get("answer_text") or "")
    assert greeting["media"]["photo_url"] is None


@pytest.mark.asyncio
async def test_http_payload_tenant_does_not_override_binding_tenant(monkeypatch):
    seen: list[str] = []

    async def capture(tenant, body, trace_id):
        seen.append(tenant.tenant_id)
        assert "tenant" not in body
        return {"ok": True, "answer_text": "ok", "answer_mode": "structured_price", "product": None}

    monkeypatch.setattr("app.advisor.service.run_structured_query", capture)
    monkeypatch.setattr("app.advisor.service._upsert_session_context", AsyncMock())
    await handle_structured_query(
        nsp_tenant(),
        {"session": "s", "question": "цена спирулина", "tenant": "whieda"},
        "t-http",
    )
    assert seen == ["nsp-maxim"]


@pytest.mark.asyncio
async def test_telegram_uses_binding_tenant_not_argument(whieda_tenant, monkeypatch):
    seen: list[str] = []

    async def capture(tenant, body, trace_id):
        seen.append(tenant.tenant_id)
        return {
            "ok": True,
            "answer_text": "nsp",
            "answer_mode": "structured_price",
            "product": {"sku": SHARED_SKU},
            "media": {"photo_url": None, "videos": [], "documents": []},
        }

    monkeypatch.setattr("app.telegram.processor.handle_structured_query", capture)
    monkeypatch.setattr("app.telegram.processor.deliver_advisor_response", AsyncMock())
    binding = BotBindingContext(
        binding_id="nsp-leader-bot",
        tenant=nsp_tenant(),
        bot_token_ref="env:NSP_BOT_TOKEN",
        webhook_secret_ref="env:NSP_WEBHOOK_SECRET",
        bot_username="NSP_Leader_bot",
        status="active",
        processing_mode="core",
        bot_token="nsp-token",
        webhook_secret="nsp-secret",
    )
    msg = TelegramMessage(
        chat_id=100,
        user_id=200,
        text=f"цена {SHARED_ALIAS}",
        chat_type="private",
        raw={},
    )
    with binding_context_scope(binding):
        await handle_advisor_query(whieda_tenant, msg, "t-bind")
    assert seen == ["nsp-maxim"]


@pytest.mark.asyncio
async def test_foreign_session_context_is_not_reused(catalog):
    catalog.sessions[("whieda", "shared-chat")] = {
        "last_product_sku": SHARED_SKU,
        "last_product_name": "Спирулина WHIEDA",
    }
    with isolation_env(catalog):
        nsp = await run_structured_query(
            nsp_tenant(),
            {"question": "фото", "session": "shared-chat"},
            "t-session",
        )
    media = str(nsp.get("media") or "")
    assert "whieda.example" not in media.lower()
    assert "WHIEDA" not in (nsp.get("answer_text") or "")
