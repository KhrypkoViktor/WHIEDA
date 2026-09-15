"""Tenant media delivery: our HTTPS URL only, photo-first, no cross-tenant leak."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.delivery import (
    deliver_structured_advisor_response,
    outbound_binding_guard,
    send_telegram_photo,
)
from app.telegram.inbox import InMemoryInboxStore, set_inbox_store_for_tests
from app.telegram.tenant_media import published_media_url, resolve_delivery_photo_url
from app.telegram.worker import handle_claimed_inbox

MEDIA_BASE = "https://media.test.example/media"
SKU = "1346"
FILENAME = "main.webp"
NSP_URL = f"{MEDIA_BASE}/nsp-maxim/{SKU}/{FILENAME}"
WHIEDA_URL = f"{MEDIA_BASE}/whieda/{SKU}/{FILENAME}"


def _settings(monkeypatch, base: str | None = MEDIA_BASE):
    monkeypatch.setattr(
        "app.telegram.tenant_media.get_settings",
        lambda: type("S", (), {"platform_tenant_media_base_url": base})(),
    )


def _response(*, tenant_id: str | None = None, sku: str = SKU, filename: str = FILENAME, extra_media=None, text="карточка"):
    media = {"filename": filename, "sku": sku}
    if tenant_id is not None:
        media["tenant_id"] = tenant_id
    if extra_media:
        media.update(extra_media)
    return {
        "answer_text": text,
        "answer_mode": "structured_photo",
        "product": {"sku": sku},
        "media": media,
    }


def _binding(tenant, *, binding_id: str, token: str, status: str = "active") -> BotBindingContext:
    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref=f"env:{binding_id.upper()}_BOT_TOKEN",
        webhook_secret_ref=f"env:{binding_id.upper()}_WEBHOOK_SECRET",
        bot_username=f"{binding_id}_bot",
        status=status,
        processing_mode="core",
        bot_token=token,
        webhook_secret=f"{binding_id}-secret",
    )


@pytest.fixture
def nsp_tenant(whieda_tenant):
    return type(whieda_tenant)(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={},
    )


def test_nsp_reference_builds_only_nsp_url(monkeypatch):
    _settings(monkeypatch)
    url = resolve_delivery_photo_url(_response(), tenant_id="nsp-maxim")
    assert url == NSP_URL
    assert url.count("nsp-maxim") == 1
    assert "whieda" not in url


def test_same_sku_whieda_does_not_see_nsp(monkeypatch):
    _settings(monkeypatch)
    nsp = resolve_delivery_photo_url(_response(), tenant_id="nsp-maxim")
    whieda = resolve_delivery_photo_url(_response(), tenant_id="whieda")
    assert nsp == NSP_URL
    assert whieda == WHIEDA_URL
    assert nsp != whieda
    assert "nsp-maxim" not in whieda
    assert "whieda" not in nsp


@pytest.mark.parametrize(
    "field,value",
    [
        ("filename", "../main.webp"),
        ("filename", "main/webp"),
        ("filename", "main\\webp"),
        ("filename", "https://evil.example/x.webp"),
        ("filename", "main.webp?x=1"),
        ("filename", "main.webp#x"),
        ("sku", "../1346"),
        ("sku", "1346/extra"),
        ("sku", "1346\\x"),
        ("sku", "https://cdn.example/1346"),
        ("sku", "1346?x=1"),
    ],
)
def test_invalid_path_segment_is_rejected(monkeypatch, field, value):
    _settings(monkeypatch)
    kwargs = {"filename": FILENAME, "sku": SKU}
    kwargs[field] = value
    url = resolve_delivery_photo_url(_response(**kwargs), tenant_id="nsp-maxim")
    assert url is None


def test_package_and_external_urls_are_never_published(monkeypatch):
    _settings(monkeypatch)
    for leaked in (
        "https://wwc.best/photo.jpg",
        "https://drive.google.com/file/d/abc/view",
        "https://cdn.example.test/tenant-alpha/A-001.jpg",
        "https://mlm.sysarch.pro/whieda-media/aktivator-kletok.jpg",
        "http://localhost:8080/media/nsp-maxim/1346/main.webp",
    ):
        url = resolve_delivery_photo_url(
            _response(extra_media={"photo_url": leaked, "url": leaked}),
            tenant_id="nsp-maxim",
        )
        assert url == NSP_URL
        assert leaked not in url


def test_missing_base_url_does_not_invent_photo(monkeypatch):
    _settings(monkeypatch, base=None)
    assert resolve_delivery_photo_url(_response(), tenant_id="nsp-maxim") is None
    assert published_media_url(tenant_id="nsp-maxim", sku=SKU, filename=FILENAME, base_url=None) is None


def test_cross_tenant_media_claim_is_rejected(monkeypatch):
    _settings(monkeypatch)
    url = resolve_delivery_photo_url(_response(tenant_id="nsp-maxim"), tenant_id="whieda")
    assert url is None


def test_package_relative_media_json_url_becomes_our_https(monkeypatch):
    _settings(monkeypatch)
    nsp = resolve_delivery_photo_url(
        {
            "answer_text": "Локло",
            "product": {"sku": "1346"},
            "media": {
                "tenant_id": "nsp-maxim",
                "sku": "1346",
                "resource_type": "image",
                "url": "media/nsp-maxim/1346/1.png.webp",
                "title": "Локло",
            },
        },
        tenant_id="nsp-maxim",
    )
    whieda = resolve_delivery_photo_url(
        {
            "answer_text": "same sku",
            "product": {"sku": "1346"},
            "media": {"url": "media/whieda/1346/1.png.webp", "sku": "1346"},
        },
        tenant_id="whieda",
    )
    assert nsp == f"{MEDIA_BASE}/nsp-maxim/1346/1.png.webp"
    assert whieda == f"{MEDIA_BASE}/whieda/1346/1.png.webp"
    assert nsp != whieda


def test_package_relative_path_does_not_cross_tenant(monkeypatch):
    _settings(monkeypatch)
    assert (
        resolve_delivery_photo_url(
            {
                "product": {"sku": "1346"},
                "media": {"url": "media/nsp-maxim/1346/1.png.webp"},
            },
            tenant_id="whieda",
        )
        is None
    )


def test_https_package_url_without_filename_is_rejected(monkeypatch):
    _settings(monkeypatch)
    assert (
        resolve_delivery_photo_url(
            {
                "product": {"sku": "1346"},
                "media": {"url": "https://drive.google.com/file/d/abc/view", "photo_url": "https://wwc.best/x.jpg"},
            },
            tenant_id="nsp-maxim",
        )
        is None
    )


@pytest.mark.asyncio
async def test_nsp_photo_first_uses_nsp_token(monkeypatch, nsp_tenant):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            10,
            _response(text="фото NSP"),
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    photo.assert_awaited_once()
    assert photo.await_args.kwargs["photo_url"] == NSP_URL
    assert photo.await_args.kwargs["bot_token"] == "nsp-token"
    assert "caption" not in photo.await_args.kwargs
    text.assert_awaited_once()
    assert text.await_args.kwargs["bot_token"] == "nsp-token"
    assert result["photo_sent"] is True
    assert result["text_sent"] is True


@pytest.mark.asyncio
async def test_nsp_media_json_relative_url_is_photo_first(monkeypatch):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            17,
            {
                "answer_text": "Отправляю фото: Локло",
                "answer_mode": "structured_photo",
                "product": {"sku": "1346"},
                "media": {
                    "url": "media/nsp-maxim/1346/1.png.webp",
                    "sku": "1346",
                    "photo_url": "media/nsp-maxim/1346/1.png.webp",
                },
            },
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    photo.assert_awaited_once()
    assert photo.await_args.kwargs["photo_url"] == f"{MEDIA_BASE}/nsp-maxim/1346/1.png.webp"
    assert photo.await_args.kwargs["bot_token"] == "nsp-token"
    assert "caption" not in photo.await_args.kwargs
    text.assert_awaited_once()
    assert result["photo_sent"] is True


@pytest.mark.asyncio
async def test_nsp_media_json_relative_url_is_photo_first(monkeypatch):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            17,
            {
                "answer_text": "Отправляю фото: Локло",
                "answer_mode": "structured_photo",
                "product": {"sku": "1346"},
                "media": {
                    "url": "media/nsp-maxim/1346/1.png.webp",
                    "sku": "1346",
                    "photo_url": "media/nsp-maxim/1346/1.png.webp",
                },
            },
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    photo.assert_awaited_once()
    assert photo.await_args.kwargs["photo_url"] == f"{MEDIA_BASE}/nsp-maxim/1346/1.png.webp"
    assert photo.await_args.kwargs["bot_token"] == "nsp-token"
    assert "caption" not in photo.await_args.kwargs
    text.assert_awaited_once()
    assert result["photo_sent"] is True


@pytest.mark.asyncio
async def test_same_sku_whieda_sends_only_whieda_url(monkeypatch):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ):
        result = await deliver_structured_advisor_response(
            11,
            _response(text="фото WHIEDA"),
            bot_token="whieda-token",
            tenant_id="whieda",
        )
    photo.assert_awaited_once()
    sent = photo.await_args.kwargs["photo_url"]
    assert sent == WHIEDA_URL
    assert "nsp-maxim" not in sent
    assert result["photo_sent"] is True


@pytest.mark.asyncio
async def test_invalid_reference_sends_text_only(monkeypatch):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            12,
            _response(filename="../main.webp", text="текст без фото"),
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    photo.assert_not_awaited()
    text.assert_awaited_once()
    assert result["photo_sent"] is False
    assert result["text_sent"] is True


@pytest.mark.asyncio
async def test_missing_base_url_sends_text_without_invented_link(monkeypatch):
    _settings(monkeypatch, base=None)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            13,
            _response(text="карточка без base URL https://drive.google.com/file/d/abc"),
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    photo.assert_not_awaited()
    sent_text = text.await_args.kwargs["text"]
    assert "drive.google" not in sent_text
    assert "http" not in sent_text.lower()
    assert result["photo_sent"] is False
    assert result["text_sent"] is True


@pytest.mark.asyncio
async def test_external_url_in_package_never_reaches_telegram(monkeypatch):
    _settings(monkeypatch)
    leaked = "https://wwc.best/nsp.jpg"
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        await deliver_structured_advisor_response(
            14,
            _response(extra_media={"photo_url": leaked, "url": leaked}, text=f"см. {leaked}"),
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
        )
    assert photo.await_args.kwargs["photo_url"] == NSP_URL
    assert leaked not in photo.await_args.kwargs["photo_url"]
    assert leaked not in text.await_args.kwargs["text"]
    assert "wwc.best" not in text.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_unknown_or_disabled_binding_does_not_deliver(monkeypatch):
    _settings(monkeypatch)
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock()) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock()
    ) as text:
        skipped_unknown = await deliver_structured_advisor_response(
            15,
            _response(),
            bot_token="nsp-token",
            tenant_id=None,
        )
        skipped_disabled = await deliver_structured_advisor_response(
            16,
            _response(),
            bot_token="nsp-token",
            tenant_id="nsp-maxim",
            binding_status="disabled",
        )
    photo.assert_not_awaited()
    text.assert_not_awaited()
    assert skipped_unknown["skipped"] is True
    assert skipped_disabled["skipped"] is True


@pytest.mark.asyncio
async def test_retry_does_not_send_nsp_media_to_whieda_binding(monkeypatch, nsp_tenant, whieda_tenant):
    _settings(monkeypatch)
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    nsp_binding = _binding(nsp_tenant, binding_id="nsp-leader-bot", token="nsp-token")
    whieda_binding = _binding(whieda_tenant, binding_id="whieda-advisor-bot", token="whieda-token")

    async def resolve(binding_id: str):
        if binding_id == nsp_binding.binding_id:
            return nsp_binding
        return None

    monkeypatch.setattr("app.telegram.worker.resolve_bot_binding_context", resolve)
    sent: list[tuple[str, str]] = []

    async def process_update(binding, payload, trace_id):
        with outbound_binding_guard(binding.bot_token):
            with patch(
                "app.telegram.delivery.send_telegram_photo",
                AsyncMock(return_value={"ok": True}),
            ) as photo, patch(
                "app.telegram.delivery.send_telegram_text",
                AsyncMock(return_value={"ok": True}),
            ):
                await deliver_structured_advisor_response(
                    1,
                    _response(),
                    bot_token=binding.bot_token,
                    tenant_id=binding.tenant.tenant_id,
                    binding_status=binding.status,
                )
                sent.append(
                    (
                        binding.tenant.tenant_id,
                        photo.await_args.kwargs["photo_url"] if photo.await_args else "",
                    )
                )
                assert photo.await_args.kwargs["bot_token"] == "nsp-token"

    try:
        enqueued = await store.enqueue(
            binding_id=nsp_binding.binding_id,
            tenant_id="nsp-maxim",
            telegram_update_id=9001,
            payload={"update_id": 9001, "message": {"text": "фото", "chat": {"id": 1}}},
        )
        claimed = await store.claim_by_id(enqueued.inbox_id, "worker-1")
        await handle_claimed_inbox(
            claimed,
            webhook_binding=whieda_binding,
            trace_id="retry-media",
            store=store,
            process_update=process_update,
        )
        assert sent == [("nsp-maxim", NSP_URL)]
    finally:
        set_inbox_store_for_tests(None)


@pytest.mark.asyncio
async def test_retry_skips_delivery_when_claimed_binding_unavailable(monkeypatch, nsp_tenant, whieda_tenant):
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    nsp_binding = _binding(nsp_tenant, binding_id="nsp-leader-bot", token="nsp-token")
    whieda_binding = _binding(whieda_tenant, binding_id="whieda-advisor-bot", token="whieda-token")
    monkeypatch.setattr("app.telegram.worker.resolve_bot_binding_context", AsyncMock(return_value=None))
    process_update = AsyncMock()
    try:
        enqueued = await store.enqueue(
            binding_id=nsp_binding.binding_id,
            tenant_id="nsp-maxim",
            telegram_update_id=9002,
            payload={"update_id": 9002, "message": {"text": "фото", "chat": {"id": 1}}},
        )
        claimed = await store.claim_by_id(enqueued.inbox_id, "worker-1")
        await handle_claimed_inbox(
            claimed,
            webhook_binding=whieda_binding,
            trace_id="retry-missing-binding",
            store=store,
            process_update=process_update,
        )
        process_update.assert_not_awaited()
    finally:
        set_inbox_store_for_tests(None)


@pytest.mark.asyncio
async def test_foreign_bot_token_cannot_send_photo():
    with outbound_binding_guard("nsp-token"):
        with pytest.raises(RuntimeError, match="foreign_bot_token_forbidden"):
            await send_telegram_photo(
                chat_id="1",
                photo_url=NSP_URL,
                bot_token="whieda-token",
            )
