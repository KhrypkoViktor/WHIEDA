"""Сайт: /api/v1/content-access/me/… — сессия, коды ошибок, no-store, загрузка фото."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.cabinet.profile import ProfileValidationError
from app.cabinet.service import CabinetError, CabinetPerson
from app.referral_bonus.service import encode_page_cursor

HOST = {"host": "wwc.best"}
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _person(*, paid: bool = True, site: bool = True) -> CabinetPerson:
    subscription = None
    if site:
        subscription = {
            "ref_code": "olga-samtsova",
            "public_profile": {"display_name": "Ольга Самцова", "subdomain": "samtsova"},
            "paid_until": NOW + timedelta(days=30) if paid else NOW - timedelta(days=60),
            "partner_paid": paid,
            "subscription_status": "active" if paid else "suspended",
        }
    return CabinetPerson(
        telegram_user_id=525317405,
        actor_id="olga-actor",
        display_name="Ольга",
        telegram_username="olga_samtsova",
        subscription=subscription,
    )


@pytest.fixture
def signed_in(monkeypatch):
    def sign_in(person: CabinetPerson | None = None):
        monkeypatch.setattr(
            "app.cabinet.routes._current_session", AsyncMock(return_value={"telegram_user_id": 525317405})
        )
        loader = AsyncMock(return_value=person or _person())
        monkeypatch.setattr("app.cabinet.routes.load_person", loader)
        return loader

    return sign_in


def _jpeg_bytes(size=(2000, 1500)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(out, format="JPEG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_without_session_every_cabinet_route_is_401_and_not_cached(client):
    for method, path in (
        ("GET", "/api/v1/content-access/me/overview"),
        ("GET", "/api/v1/content-access/me/referrals"),
        ("POST", "/api/v1/content-access/me/profile"),
        ("POST", "/v1/content-access/me/journey/presentation/done"),
    ):
        response = await client.request(method, path, headers=HOST, json={} if method == "POST" else None)
        assert response.status_code == 401, path
        assert response.json()["error"] == "content_session_required"
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_overview_passes_bot_and_crm_entitlement(client, signed_in, monkeypatch):
    signed_in()
    overview = AsyncMock(return_value={"ok": True, "tier": "pro", "locks": {"team": "leader_required"}})
    monkeypatch.setattr("app.cabinet.routes.load_overview", overview)
    for prefix in ("/api/v1", "/v1"):
        response = await client.get(f"{prefix}/content-access/me/overview", headers=HOST)
        assert response.status_code == 200 and response.json()["tier"] == "pro"
        assert response.headers["cache-control"] == "private, no-store"
    kwargs = overview.await_args.kwargs
    assert kwargs["crm_entitled"] is False  # у тестового тенанта нет права crm


@pytest.mark.asyncio
async def test_identity_conflict_is_409(client, monkeypatch):
    monkeypatch.setattr("app.cabinet.routes._current_session", AsyncMock(return_value={"telegram_user_id": 1}))
    monkeypatch.setattr("app.cabinet.routes.load_person", AsyncMock(side_effect=CabinetError("identity_conflict", 409)))
    response = await client.get("/api/v1/content-access/me/overview", headers=HOST)
    assert response.status_code == 409 and response.json()["error"] == "identity_conflict"


@pytest.mark.asyncio
async def test_referrals_page_maps_rows_and_rejects_foreign_cursor(client, signed_in, monkeypatch):
    signed_in()
    page = AsyncMock(
        return_value={
            "items": [
                {"invitee_actor_id": "telegram:whieda:9", "display_name": "Анна", "telegram_username": "@anna",
                 "attributed_at": NOW, "subscription_status": "no_site", "site_request_status": "awaiting_payment"}
            ],
            "next_cursor": "abc",
        }
    )
    monkeypatch.setattr("app.cabinet.routes.list_referrals", page)
    monkeypatch.setattr("app.cabinet.routes.referral_counts", AsyncMock(return_value={"invited_count": 3, "paid_count": 1}))
    body = (await client.get("/api/v1/content-access/me/referrals?limit=5", headers=HOST)).json()
    assert body["counts"] == {"invited": 3, "paid": 1} and body["next_cursor"] == "abc"
    assert body["items"] == [
        {"display_name": "Анна", "telegram_username": "anna", "attributed_at": NOW.isoformat(),
         "site_state": "waiting", "status_label": "ожидает оплаты", "subscription_status": "no_site"}
    ]
    assert "invitee_actor_id" not in body["items"][0]
    assert page.await_args.kwargs == {"limit": 5, "cursor": None}

    from app.referral_bonus import service as referral_service

    # Настоящая выборка: курсор разбирается до базы.
    monkeypatch.setattr("app.cabinet.routes.list_referrals", referral_service.list_referrals)
    bad = await client.get("/api/v1/content-access/me/referrals?cursor=garbage!", headers=HOST)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_cursor"


@pytest.mark.asyncio
async def test_bonus_ledger_has_labels_balance_and_rules(client, signed_in, monkeypatch):
    signed_in()
    monkeypatch.setattr(
        "app.cabinet.routes.list_bonus_ledger",
        AsyncMock(return_value={"items": [{"entry_id": "e1", "created_at": NOW, "amount_minor": 600,
                                           "entry_type": "credit", "description": "Referral bonus: first payment"}],
                                "next_cursor": None}),
    )
    monkeypatch.setattr(
        "app.cabinet.service.bonus_balance_and_rules",
        AsyncMock(return_value={"balance_minor": 1250, "rules": {"first_payment_percent": 20, "renewal_percent": 10,
                                                                 "cash_out": False, "spend_url": "/start/"}}),
    )
    body = (await client.get("/api/v1/content-access/me/bonus-ledger", headers=HOST)).json()
    assert body["balance"] == {"currency": "WWC$", "amount_minor": 1250}
    assert body["rules"]["first_payment_percent"] == 20 and body["rules"]["cash_out"] is False
    assert body["items"][0]["label"] == "Бонус: первая оплата сайта приглашённым"
    assert body["items"][0]["currency"] == "WWC$"


@pytest.mark.asyncio
async def test_profile_submit_errors_and_owner_notice(client, signed_in, monkeypatch):
    signed_in()
    monkeypatch.setattr(
        "app.cabinet.routes.submit_profile_request",
        AsyncMock(side_effect=ProfileValidationError("invalid_phone", "whatsapp")),
    )
    bad = await client.post("/api/v1/content-access/me/profile", headers=HOST, json={"contacts": {"whatsapp": "12"}})
    assert bad.status_code == 400
    assert {key: bad.json()[key] for key in ("ok", "error", "field")} == {"ok": False, "error": "invalid_phone", "field": "whatsapp"}
    assert bad.headers["cache-control"] == "private, no-store"

    monkeypatch.setattr("app.cabinet.routes.submit_profile_request", AsyncMock(side_effect=CabinetError("pro_required", 402)))
    assert (await client.post("/api/v1/content-access/me/profile", headers=HOST, json={"bio": "x"})).status_code == 402

    pending = {"request_id": "11111111-2222-3333-4444-555555555555", "status": "pending", "changes": {"bio": "Новое"},
               "previous": {"bio": None}, "reject_reason": None, "created_at": NOW, "reviewed_at": None}
    monkeypatch.setattr(
        "app.cabinet.routes.submit_profile_request",
        AsyncMock(return_value={"pending": pending, "status": "pending", "replaced": True, "replaced_request_id": "old"}),
    )
    notify = AsyncMock(return_value=True)
    retire = AsyncMock()
    monkeypatch.setattr("app.telegram.cabinet_profile.notify_owner_about_profile_request", notify)
    monkeypatch.setattr("app.telegram.cabinet_profile.retire_owner_card", retire)
    ok = (await client.post("/api/v1/content-access/me/profile", headers=HOST, json={"bio": "Новое"})).json()
    assert ok["status"] == "pending" and "owner_notified" not in ok
    assert ok["pending"]["request_id"] == pending["request_id"] and ok["pending"]["created_at"] == NOW.isoformat()
    # Карточка владельцу — фоном после ответа; старая заявка теряет кнопки там же.
    notify.assert_awaited_once_with("whieda", pending["request_id"], replaced_request_id="old")
    retire.assert_not_awaited()

    monkeypatch.setattr(
        "app.cabinet.routes.submit_profile_request",
        AsyncMock(return_value={"pending": None, "status": "no_changes", "replaced_request_id": "old"}),
    )
    back = (await client.post("/api/v1/content-access/me/profile", headers=HOST, json={"bio": None})).json()
    assert back == {"ok": True, "status": "no_changes", "pending": None}
    retire.assert_awaited_once_with("whieda", "old")
    monkeypatch.setattr("app.cabinet.routes.submit_profile_request", AsyncMock(side_effect=CabinetError("too_many_requests", 429)))
    assert (await client.post("/api/v1/content-access/me/profile", headers=HOST, json={"bio": "x"})).status_code == 429


@pytest.mark.asyncio
async def test_photo_upload_needs_paid_site_before_reading_the_file(client, signed_in, monkeypatch):
    signed_in(_person(paid=False))
    store = AsyncMock()
    monkeypatch.setattr("app.cabinet.routes.store_profile_photo", store)
    response = await client.post(
        "/api/v1/content-access/me/profile/photo", headers=HOST, files={"photo": ("p.jpg", _jpeg_bytes(), "image/jpeg")}
    )
    assert response.status_code == 402 and response.json()["error"] == "pro_required"
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_upload_multipart_and_raw_body(client, signed_in, monkeypatch):
    signed_in()
    allowed = AsyncMock(return_value="olga-samtsova")
    monkeypatch.setattr("app.cabinet.routes.check_upload_allowed", allowed)
    seen = []

    async def store(tenant_id, person, photo):
        seen.append(photo)
        return {"media_id": "m", "photo_url": "https://wwc.best/api/v1/content-access/partner-media/m.jpg",
                "width": photo.width, "height": photo.height, "size_bytes": len(photo.body)}

    monkeypatch.setattr("app.cabinet.routes.store_profile_photo", store)
    multipart = await client.post(
        "/api/v1/content-access/me/profile/photo", headers=HOST, files={"photo": ("p.jpg", _jpeg_bytes(), "image/jpeg")}
    )
    assert multipart.status_code == 200, multipart.text
    assert (multipart.json()["width"], multipart.json()["height"]) == (1200, 900)
    raw = await client.post(
        "/v1/content-access/me/profile/photo",
        headers={**HOST, "content-type": "image/jpeg"},
        content=_jpeg_bytes((800, 600)),
    )
    assert raw.status_code == 200 and (seen[-1].width, seen[-1].height) == (800, 600)

    other = await client.post(
        "/api/v1/content-access/me/profile/photo", headers={**HOST, "content-type": "text/plain"}, content=b"hi"
    )
    assert other.status_code == 415 and other.json()["error"] == "photo_type_unsupported"
    missing = await client.post(
        "/api/v1/content-access/me/profile/photo", headers=HOST, files={"other": ("p.jpg", b"x", "image/jpeg")}
    )
    assert missing.status_code == 400 and missing.json()["error"] == "photo_missing"
    broken = await client.post(
        "/api/v1/content-access/me/profile/photo", headers={**HOST, "content-type": "image/png"}, content=b"not a png"
    )
    assert broken.status_code == 415 and broken.json()["error"] == "photo_type_unsupported"
    cut = await client.post(
        "/api/v1/content-access/me/profile/photo",
        headers={**HOST, "content-type": "image/jpeg"},
        content=_jpeg_bytes()[:2000],
    )
    assert cut.status_code == 422 and cut.json()["error"] == "photo_unreadable"
    huge = await client.post(
        "/api/v1/content-access/me/profile/photo",
        headers={**HOST, "content-type": "image/jpeg", "content-length": str(30 * 1024 * 1024)},
        content=b"x",
    )
    assert huge.status_code == 413 and huge.json()["error"] == "photo_too_large"
    assert len(seen) == 2
    assert allowed.await_count == 7  # лимит проверяется до чтения каждого файла

    monkeypatch.setattr("app.cabinet.routes.check_upload_allowed", AsyncMock(side_effect=CabinetError("too_many_uploads", 429)))
    limited = await client.post(
        "/api/v1/content-access/me/profile/photo", headers=HOST, files={"photo": ("p.jpg", _jpeg_bytes(), "image/jpeg")}
    )
    assert limited.status_code == 429 and len(seen) == 2  # файл даже не разбирали


@pytest.mark.asyncio
async def test_journey_marks_only_manual_steps(client, signed_in, monkeypatch):
    signed_in()
    from app.cabinet import service

    marked = AsyncMock(return_value={"ok": True, "step": "invite_sent", "done": True, "done_at": NOW.isoformat()})
    monkeypatch.setattr("app.cabinet.routes.mark_journey_step", marked)
    shared = await client.post("/api/v1/content-access/me/invite/share", headers=HOST)
    assert shared.json()["step"] == "invite_sent" and marked.await_args.args[2] == "invite_sent"
    monkeypatch.setattr("app.cabinet.routes.mark_journey_step", service.mark_journey_step)
    wrong = await client.post("/api/v1/content-access/me/journey/profile/done", headers=HOST)
    assert wrong.status_code == 400 and wrong.json()["error"] == "step_not_manual"


@pytest.mark.asyncio
async def test_settings_accept_only_timezone_and_consent(client, signed_in, monkeypatch):
    signed_in()
    update = AsyncMock(return_value={"timezone": "Asia/Yekaterinburg", "timezone_default": "Europe/Moscow",
                                     "marketing_opt_in": True})
    monkeypatch.setattr("app.cabinet.routes.update_settings", update)
    response = await client.patch(
        "/api/v1/content-access/me/settings", headers=HOST, json={"timezone": "Asia/Yekaterinburg", "marketing_opt_in": True}
    )
    assert response.status_code == 200 and response.json()["settings"]["timezone"] == "Asia/Yekaterinburg"
    assert update.await_args.kwargs == {"timezone_name": "Asia/Yekaterinburg", "marketing_opt_in": True}
    extra = await client.patch("/api/v1/content-access/me/settings", headers=HOST, json={"leader": True})
    assert extra.status_code == 422
    monkeypatch.setattr("app.cabinet.routes.update_settings", AsyncMock(side_effect=CabinetError("invalid_timezone", 400)))
    bad = await client.patch("/api/v1/content-access/me/settings", headers=HOST, json={"timezone": "Mars/Base"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_timezone"


@pytest.mark.asyncio
async def test_public_photo_is_served_with_long_cache_and_404_for_unknown(client, monkeypatch):
    loader = AsyncMock(return_value=_jpeg_bytes((300, 300)))
    monkeypatch.setattr("app.cabinet.routes.load_public_media", loader)
    media = "3f1c2a9e-0b7d-4c55-9a1e-2d3f4b5c6d7e"
    response = await client.get(f"/api/v1/content-access/partner-media/{media}.jpg", headers=HOST)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"
    loader.assert_awaited_once_with("whieda", media)
    assert (await client.get(f"/v1/content-access/partner-media/{media}.png", headers=HOST)).status_code == 404
    loader.return_value = None
    assert (await client.get(f"/api/v1/content-access/partner-media/{media}.jpg", headers=HOST)).status_code == 404


@pytest.mark.asyncio
async def test_pending_get_and_cancel(client, signed_in, monkeypatch):
    signed_in()
    monkeypatch.setattr(
        "app.cabinet.routes.get_pending_request", AsyncMock(return_value={"pending": None, "last_review": None})
    )
    assert (await client.get("/api/v1/content-access/me/profile/pending", headers=HOST)).json() == {
        "ok": True, "pending": None, "last_review": None}
    retire = AsyncMock()
    monkeypatch.setattr("app.telegram.cabinet_profile.retire_owner_card", retire)
    monkeypatch.setattr("app.cabinet.routes.cancel_pending_request", AsyncMock(return_value="req-1"))
    assert (await client.delete("/api/v1/content-access/me/profile/pending", headers=HOST)).json() == {
        "ok": True, "cancelled": True}
    retire.assert_awaited_once_with("whieda", "req-1")
    monkeypatch.setattr("app.cabinet.routes.cancel_pending_request", AsyncMock(return_value=None))
    assert (await client.delete("/api/v1/content-access/me/profile/pending", headers=HOST)).json()["cancelled"] is False
    assert retire.await_count == 1


def test_cursor_helper_used_by_the_site_is_opaque():
    cursor = encode_page_cursor(NOW, "x")
    assert "2026" not in cursor and "/" not in cursor and "+" not in cursor


@pytest.mark.asyncio
async def test_own_photo_preview_is_private_and_only_for_the_owner(client, signed_in, monkeypatch):
    signed_in()
    media = "3f1c2a9e-0b7d-4c55-9a1e-2d3f4b5c6d7e"
    own = AsyncMock(return_value=_jpeg_bytes((300, 300)))
    monkeypatch.setattr("app.cabinet.routes.load_own_media", own)
    response = await client.get(f"/api/v1/content-access/me/profile/photo/{media}.jpg", headers=HOST)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert own.await_args.args[0] == "whieda" and own.await_args.args[2] == media
    own.return_value = None  # чужое или удалённое
    assert (await client.get(f"/v1/content-access/me/profile/photo/{media}.jpg", headers=HOST)).status_code == 404
