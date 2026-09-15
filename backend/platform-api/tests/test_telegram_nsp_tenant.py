"""NSP tenant (nsp-maxim) on the shared Core: no WHIEDA brand, own token, strict media."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.catalog_browse import handle_catalog_products, handle_main_menu, handle_open_calculator
from app.telegram.delivery import LEGACY_MEDIA_TENANTS, deliver_structured_advisor_response
from app.tenancy import TenantContext

MEDIA_BASE = "https://media.test.example/media"


@pytest.fixture
def nsp_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={"structure_basic": True},
    )


@pytest.fixture
def nsp_bot_binding(nsp_tenant) -> BotBindingContext:
    return BotBindingContext(
        binding_id="nsp-binding",
        tenant=nsp_tenant,
        bot_token_ref="env:NSP_BOT_TOKEN",
        webhook_secret_ref="env:NSP_WEBHOOK_SECRET",
        bot_username="NSP_Leader_bot",
        status="active",
        processing_mode="core",
        bot_token="nsp-token",
        webhook_secret="nsp-secret",
    )


def _media_settings(monkeypatch, base: str | None = MEDIA_BASE):
    monkeypatch.setattr(
        "app.telegram.tenant_media.get_settings",
        lambda: type("S", (), {"platform_tenant_media_base_url": base})(),
    )


@pytest.mark.asyncio
async def test_main_menu_has_no_whieda_brand_or_calculator(nsp_tenant):
    with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
        result = await handle_main_menu(nsp_tenant, 1, trace_id="nsp-menu")
    assert result["route"] == "main_menu"
    text = deliver.await_args.args[1]
    assert "Главное меню NSP" in text
    assert "WHIEDA" not in text
    assert "Калькулятор" not in text
    assert "wwc.best" not in text


@pytest.mark.asyncio
async def test_calculator_is_unavailable_for_nsp(nsp_tenant, nsp_bot_binding):
    with patch("app.telegram.catalog_browse.send_telegram_text", AsyncMock()) as send:
        with binding_context_scope(nsp_bot_binding):
            result = await handle_open_calculator(nsp_tenant, 5, trace_id="nsp-calc")
    assert result["route"] == "calculator_unavailable"
    assert send.await_args.kwargs["bot_token"] == "nsp-token"
    assert "wwc.best" not in send.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_catalog_uses_nsp_token_only(nsp_tenant, nsp_bot_binding):
    with patch("app.telegram.catalog_browse.render_catalog_page", AsyncMock(return_value=("Каталог NSP", None))):
        with patch("app.telegram.catalog_browse.send_telegram_text", AsyncMock()) as send:
            with binding_context_scope(nsp_bot_binding):
                result = await handle_catalog_products(nsp_tenant, 9, trace_id="nsp-cat")
    assert result["route"] == "catalog_products"
    assert send.await_args.kwargs["bot_token"] == "nsp-token"


def test_nsp_is_not_a_legacy_media_tenant():
    assert "nsp-maxim" not in LEGACY_MEDIA_TENANTS
    assert "whieda" in LEGACY_MEDIA_TENANTS


@pytest.mark.asyncio
async def test_nsp_delivery_publishes_package_media_and_strips_foreign_links(monkeypatch):
    _media_settings(monkeypatch)
    response = {
        "answer_text": "Локло — см. https://drive.google.com/file/d/abc и https://wwc.best/x",
        "answer_mode": "structured_photo",
        "product": {"sku": "1346"},
        "media": {"filename": "main.webp", "sku": "1346", "photo_url": "https://wwc.best/leak.jpg"},
    }
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            7, response, bot_token="nsp-token", tenant_id="nsp-maxim"
        )
    assert photo.await_args.kwargs["photo_url"] == f"{MEDIA_BASE}/nsp-maxim/1346/main.webp"
    sent_text = text.await_args.kwargs["text"]
    assert "drive.google" not in sent_text
    assert "wwc.best" not in sent_text
    assert result["photo_sent"] is True and result["text_sent"] is True


@pytest.mark.asyncio
async def test_whieda_delivery_keeps_absolute_photo_and_links(monkeypatch):
    _media_settings(monkeypatch)
    response = {
        "answer_text": "Видео по Активатору: https://youtu.be/abc",
        "answer_mode": "structured_video",
        "product": {"sku": "M015-00"},
        "media": {"photo_url": "https://mlm.sysarch.pro/whieda-media/aktivator-kletok.jpg", "videos": [], "documents": []},
    }
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        await deliver_structured_advisor_response(8, response, bot_token="whieda-token", tenant_id="whieda")
    assert photo.await_args.kwargs["photo_url"] == "https://mlm.sysarch.pro/whieda-media/aktivator-kletok.jpg"
    assert "https://youtu.be/abc" in text.await_args.kwargs["text"]
