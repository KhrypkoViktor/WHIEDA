"""GET /me/overview: уровень, замки, путь и ссылки из фактов (без базы)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.cabinet import service
from app.cabinet.service import CabinetFacts, CabinetPerson
from app.content_access.account import build_site_account
from app.settings import get_settings

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _person(*, site: bool, paid: bool = True, user_id: int = 7001, profile: dict | None = None) -> CabinetPerson:
    subscription = None
    if site:
        subscription = {
            "ref_code": "igor",
            "public_profile": profile if profile is not None else {"subdomain": "igoref", "display_name": "Игорь"},
            "paid_until": datetime.now(timezone.utc) + timedelta(days=40 if paid else -60),
            "partner_paid": paid,
            "subscription_status": "active" if paid else "suspended",
        }
    return CabinetPerson(telegram_user_id=user_id, actor_id=f"telegram:whieda:{user_id}", display_name="@igor",
                         telegram_username="igor", subscription=subscription)


def _facts(*, club_days: int | None = None, crm_lock: str | None = None, leader: bool = False, **extra) -> CabinetFacts:
    account = build_site_account(
        actor_id="a", bonus_minor=1250, pro_paid_until=NOW + timedelta(days=40), pro_ref_code="igor",
        club_paid_until=(NOW + timedelta(days=club_days)) if club_days is not None else None, at=NOW,
    )
    values = dict(
        marks={},
        requests=[],
        courses=[{"slug": "zapusk-wwc", "title": "Запуск WWC", "access_rule": "pro", "has_access": False,
                  "lessons_total": 14, "lessons_done": 7, "first_done": True}],
        leader_product=leader,
        counts={"invited_count": 3, "paid_count": 1},
        account=account,
        crm_lock=crm_lock,
        crm_contacts=None if crm_lock else 2,
        crm_today=None if crm_lock else 5,
    )
    values.update(extra)
    return CabinetFacts(**values)


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(service, "invite_block", AsyncMock(return_value={"link": "https://t.me/bot?start=ref_x", "text": "Привет"}))
    monkeypatch.setattr(service, "settings_block", AsyncMock(return_value={"timezone": None, "marketing_opt_in": None}))
    monkeypatch.delenv("PLATFORM_LEADER_PILOT_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_free_tier_without_site_gets_a_working_cabinet(quiet):
    quiet.setattr(service, "collect_facts", AsyncMock(return_value=_facts(crm_lock="pro_required", account=None)))
    result = await service.load_overview("whieda", _person(site=False), bot_username="WHIEDA_Advisor_bot", crm_entitled=True)
    assert result["tier"] == "free"
    assert {"site", "profile", "calculator", "crm", "club", "team"} <= set(result["locks"])
    assert result["site"] is None and result["profile"] is None and result["account"] is None
    assert result["journey"]["path"] == "free"
    assert result["invite"]["link"] == "https://t.me/bot?start=ref_x"
    assert result["counters"]["invited"] == 3 and result["counters"]["crm_today"] is None
    assert result["links"]["support"] == "https://t.me/WHIEDA_Advisor_bot?start=support"
    assert result["links"]["renew"] == "https://t.me/WHIEDA_Advisor_bot?start=renew"
    assert result["links"]["club_group"] is None
    assert result["person"]["display_name"] == "@igor"


@pytest.mark.asyncio
async def test_pro_partner_sees_site_profile_counters_and_academy(quiet):
    quiet.setattr(service, "collect_facts", AsyncMock(return_value=_facts(club_days=20)))
    result = await service.load_overview("whieda", _person(site=True), bot_username="bot", crm_entitled=True)
    assert result["tier"] == "pro" and result["locks"] == {"team": "leader_required"}
    site = result["site"]
    assert site["host"] == "igoref.wwc.best" and site["url"] == "https://igoref.wwc.best/"
    assert site["status"] == "active" and site["days_left"] in (39, 40)
    assert site["renew_url"] == "https://t.me/bot?start=renew"
    assert result["profile"]["current"]["display_name"] == "Игорь" and result["profile"]["pending"] is None
    assert result["counters"]["academy"]["percent"] == 50 and result["counters"]["academy"]["locked"] is False
    assert result["counters"]["crm_today"] == 5
    steps = {s["key"]: s["status"] for s in result["journey"]["steps"]}
    assert steps["lesson1"] == "done" and steps["crm_contact"] == "done" and steps["club"] == "done"
    assert steps["presentation"] == "current"
    assert result["links"]["club_group"] == "https://t.me/c/4338290116/12"
    assert result["person"]["display_name"] == "Игорь"
    assert result["account"]["pro"]["paid_until"] == (NOW + timedelta(days=40)).isoformat()


@pytest.mark.asyncio
async def test_leader_tier_comes_from_the_pilot_list_or_the_product(quiet):
    quiet.setattr(service, "collect_facts", AsyncMock(return_value=_facts()))
    quiet.setenv("PLATFORM_LEADER_PILOT_TELEGRAM_IDS", "7001, 42")
    get_settings.cache_clear()
    assert (await service.load_overview("whieda", _person(site=True), bot_username=None, crm_entitled=False))["tier"] == "leader"
    # Право лидера без оплаченного PRO уровень не поднимает.
    unpaid = await service.load_overview("whieda", _person(site=True, paid=False), bot_username=None, crm_entitled=False)
    assert unpaid["tier"] == "free" and unpaid["locks"]["team"] == "leader_required"
    quiet.setenv("PLATFORM_LEADER_PILOT_TELEGRAM_IDS", "")
    get_settings.cache_clear()
    quiet.setattr(service, "collect_facts", AsyncMock(return_value=_facts(leader=True)))
    product = await service.load_overview("whieda", _person(site=True), bot_username=None, crm_entitled=False)
    assert product["tier"] == "leader" and "team" not in product["locks"]
    assert product["links"]["bot"] is None and product["links"]["support"] is None


def test_default_leader_pilot_is_the_owner_account(monkeypatch):
    monkeypatch.delenv("PLATFORM_LEADER_PILOT_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().parsed_leader_pilot() == frozenset({688931415})
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pending_and_last_review_are_shown_to_the_partner(quiet):
    requests = [
        {"request_id": "r2", "status": "pending", "changes": {"bio": "Новое"}, "previous": {"bio": None},
         "reject_reason": None, "created_at": NOW, "reviewed_at": None},
        {"request_id": "r1", "status": "rejected", "changes": {"bio": "Старое"}, "previous": {}, "reject_reason": "Опечатка",
         "created_at": NOW - timedelta(days=1), "reviewed_at": NOW - timedelta(hours=20)},
    ]
    quiet.setattr(service, "collect_facts", AsyncMock(return_value=_facts(requests=requests)))
    profile = (await service.load_overview("whieda", _person(site=True), bot_username="bot", crm_entitled=True))["profile"]
    assert profile["pending"]["request_id"] == "r2" and profile["pending"]["created_at"] == NOW.isoformat()
    assert profile["last_review"]["status"] == "rejected" and profile["last_review"]["reject_reason"] == "Опечатка"
    assert profile["limits"]["bio_max"] == 600


@pytest.mark.asyncio
async def test_crm_counters_follow_the_crm_lock(monkeypatch):
    person = _person(site=True)
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    try:
        assert await service._crm_facts("whieda", person, crm_entitled=False) == ("feature_disabled", None, None)
        assert await service._crm_facts("whieda", person, crm_entitled=True) == ("crm_pilot_only", None, None)
        monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
        get_settings.cache_clear()
        account = AsyncMock(return_value={"account_id": "a", "timezone": "Europe/Moscow", "today": NOW.date()})
        monkeypatch.setattr("app.cabinet_crm.crm_account", account)
        monkeypatch.setattr(
            "app.crm.service.today_view",
            AsyncMock(return_value={"groups": [{"contacts": [1, 2]}, {"contacts": [3]}]}),
        )
        monkeypatch.setattr("app.cabinet_crm.crm_contacts_count", AsyncMock(return_value=7))
        assert await service._crm_facts("whieda", person, crm_entitled=True) == (None, 7, 3)
        monkeypatch.setattr("app.crm.service.today_view", AsyncMock(side_effect=RuntimeError("boom")))
        assert await service._crm_facts("whieda", person, crm_entitled=True) == (None, 0, None)
        # CRM ещё не открывали: аккаунт для счётчика не заводится, ноль дел.
        account.return_value = None
        assert await service._crm_facts("whieda", person, crm_entitled=True) == (None, 0, 0)
        free = _person(site=False)
        assert await service._crm_facts("whieda", free, crm_entitled=True) == ("pro_required", None, None)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_manual_marks_only_for_manual_steps():
    with pytest.raises(service.CabinetError) as caught:
        await service.mark_journey_step("whieda", _person(site=True), "profile")
    assert (caught.value.code, caught.value.status) == ("step_not_manual", 400)
