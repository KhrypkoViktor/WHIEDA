"""«Мои рефералы» и «История WWC$»: одни выборки и подписи для бота и кабинета на сайте."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.referral_bonus import service
from app.referral_bonus.service import (
    InvalidPageCursorError,
    bonus_entry_label,
    decode_page_cursor,
    encode_page_cursor,
    referral_site_state,
    referral_status_label,
)
from app.telegram.referral_bonus import _history_text, _referrals_text

AT = datetime(2026, 10, 2, 9, 30, 15, 123456, tzinfo=timezone.utc)


def test_cursor_round_trip_and_foreign_cursors_are_refused():
    cursor = encode_page_cursor(AT, "telegram:whieda:42")
    assert decode_page_cursor(cursor) == (AT, "telegram:whieda:42")
    assert decode_page_cursor(None) is None and decode_page_cursor("") is None
    for bad in ("not-base64!", encode_page_cursor(AT, "x")[:-3], "W10", "WyIyMDI2LTEwLTAyIiwgIngiXQ"):
        with pytest.raises(InvalidPageCursorError):
            decode_page_cursor(bad)


def test_referral_states_for_the_site_list():
    assert referral_site_state({"subscription_status": "active"}) == "paid"
    assert referral_site_state({"subscription_status": "grace"}) == "paid"
    assert referral_site_state({"subscription_status": "suspended"}) == "waiting"
    assert referral_site_state({"subscription_status": "no_site", "site_request_status": "awaiting_payment"}) == "waiting"
    assert referral_site_state({"subscription_status": "no_site"}) == "no_site"


def test_bot_and_site_share_the_status_words():
    assert referral_status_label({"subscription_status": "active"}) == "сайт активен"
    assert referral_status_label({"subscription_status": "no_site", "site_request_status": "pending_provisioning"}) == "сайт создаётся"
    assert referral_status_label({"subscription_status": "weird"}) == "статус уточняется"


def test_ledger_rows_get_human_labels():
    assert bonus_entry_label({"description": "Referral bonus: first payment"}) == "Бонус: первая оплата сайта приглашённым"
    assert bonus_entry_label({"description": "Referral bonus: renewal"}) == "Бонус: продление сайта приглашённым"
    assert bonus_entry_label({"description": "Automatic points redemption: 1 x platform_3m"}).startswith("Продление сайта за WWC$")
    assert bonus_entry_label({"description": "Bonus offset: 5 WWC$ towards payment 1a2b3c4d"}) == "Оплата WWC$"
    assert bonus_entry_label({"description": "Gemini: доля партнёра за продажу, заявка 9f…"}) == "Gemini: доля партнёра за продажу"
    assert bonus_entry_label({"description": "переплата за пакет"}) == "переплата за пакет"
    assert bonus_entry_label({"entry_type": "reversal", "description": None}) == "Отмена начисления"


def test_bot_texts_show_dates_and_the_same_labels():
    history = _history_text([{"created_at": AT, "amount_minor": 600, "description": "Referral bonus: renewal"}])
    assert "02.10.2026  +6 WWC$ — Бонус: продление сайта приглашённым" in history
    referrals = _referrals_text(
        [{"display_name": "Анна", "telegram_username": "anna", "attributed_at": AT, "subscription_status": "active"}]
    )
    assert "• Анна @anna — сайт активен · 02.10.2026" in referrals


@pytest.mark.asyncio
async def test_pages_fetch_one_extra_row_to_build_the_next_cursor(monkeypatch):
    seen: list[dict] = []

    async def fake_fetch_all(conn, sql, params=None):
        seen.append(params)
        return [
            {"entry_id": "00000000-0000-0000-0000-00000000000%d" % i, "entry_type": "credit", "amount_minor": 600,
             "product_code": "platform_subscription", "description": None, "created_at": AT}
            for i in range(3)
        ]

    @asynccontextmanager
    async def fake_conn(tenant_id):
        yield object()

    monkeypatch.setattr(service, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(service, "tenant_connection", fake_conn)
    page = await service.list_bonus_ledger("whieda", "actor", limit=2)
    assert len(page["items"]) == 2 and seen[0]["limit"] == 3
    after_at, after_id = decode_page_cursor(page["next_cursor"])
    assert after_at == AT and after_id == "00000000-0000-0000-0000-000000000001"
    await service.list_bonus_ledger("whieda", "actor", limit=2, cursor=page["next_cursor"])
    assert seen[1]["after_id"] == "00000000-0000-0000-0000-000000000001" and seen[1]["after_at"] == AT
    with pytest.raises(InvalidPageCursorError):
        await service.list_bonus_ledger("whieda", "actor", cursor=encode_page_cursor(AT, "not-a-uuid"))
    referrals = await service.list_referrals("whieda", "actor", limit=500)
    assert seen[-1]["limit"] == service.REFERRALS_PAGE_MAX + 1  # не больше 50 за раз
    assert len(referrals["items"]) == 3 and referrals["next_cursor"] is None
