"""Вход на сайт через Max (мини-приложение /max-app/, 11.10.2026): подпись initData, привязка по коду,
подтверждение заявки сайта. База — подделка в памяти: проверяем решения модуля, не SQL."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote, urlencode

import pytest
from fastapi import HTTPException

from app.content_access.service import build_max_login_link, create_content_challenge
from app.max import webapp
from app.telegram.referral_bonus import is_referral_command

TOKEN = "max-test-token"
SETTINGS = SimpleNamespace(max_bot_token=TOKEN, max_bot_username="se_test_bot")


def _signed(user_id: int = 5001, start_param: str = "", token: str = TOKEN, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "q-1",
        "user": json.dumps({"id": user_id, "first_name": "Айгуль", "username": "aygul_max"}, ensure_ascii=False, separators=(",", ":")),
    }
    if start_param:
        fields["start_param"] = start_param
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


class FakeDb:
    """lead_actor_channels(max), lead_actors.telegram_user_id, channel_link_codes."""

    def __init__(self) -> None:
        self.channels: dict[str, str] = {}
        self.actors: dict[str, int | None] = {"telegram:whieda:700": 700}
        self.codes: dict[str, dict] = {}

    def add_code(self, raw: str, *, actor_id: str = "telegram:whieda:700", expired: bool = False, used_by: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.codes[hashlib.sha256(raw.encode()).hexdigest()] = {
            "actor_id": actor_id,
            "expires_at": now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
            "used_at": now if used_by else None,
            "used_channel_user_id": used_by,
        }

    async def fetch_one(self, conn, query: str, params: tuple = ()):  # noqa: ARG002
        q = " ".join(query.split())
        if q.startswith("select la.telegram_user_id"):
            actor = self.channels.get(params[1])
            return {"telegram_user_id": self.actors.get(actor)} if actor else None
        if q.startswith("select actor_id, expires_at"):
            return self.codes.get(params[0])
        if q.startswith("insert into lead_actor_channels"):
            self.channels[params[2]] = params[1]
            return {"actor_id": params[1]}
        if q.startswith("update channel_link_codes"):
            self.codes[params[1]].update(used_at=datetime.now(timezone.utc), used_channel_user_id=params[0])
            return {"code_hash": params[1]}
        if q.startswith("select actor_id from lead_actors"):
            actor = next((a for a, tg in self.actors.items() if tg == params[1]), None)
            return {"actor_id": actor} if actor else None
        if q.startswith("insert into channel_link_codes"):
            self.codes[params[0]] = {"actor_id": params[2], "expires_at": params[3], "used_at": None, "used_channel_user_id": None}
            return {"code_hash": params[0]}
        raise AssertionError(f"unexpected query: {q[:80]}")


@pytest.fixture
def db():
    fake = FakeDb()

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    confirm = AsyncMock(return_value={"ok": True})
    with patch.object(webapp, "get_settings", return_value=SETTINGS), \
         patch.object(webapp, "tenant_connection", connection), \
         patch.object(webapp, "fetch_one", fake.fetch_one), \
         patch.object(webapp, "warm_hosts", AsyncMock()), \
         patch.object(webapp, "confirm_content_from_telegram", confirm), \
         patch.object(webapp, "create_bot_login", AsyncMock(return_value="cid.nonce")):
        fake.confirm = confirm
        yield fake


RETURN = "https://wwc.best/max-app/"


@pytest.mark.asyncio
async def test_linked_person_confirms_the_browser_login(db):
    db.channels["5001"] = "telegram:whieda:700"
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_AbC-123_x"), return_to="https://wwc.best/me/")
    assert out["status"] == "login_confirmed"
    db.confirm.assert_awaited_once_with(tenant_id="whieda", challenge_token="AbC-123_x", telegram_user_id=700)
    assert out["url"] == "https://wwc.best/me/#wwc-login=cid.nonce", "can also continue right inside Max"


@pytest.mark.asyncio
async def test_unknown_max_user_is_asked_to_link_first(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to=RETURN)
    assert out == {"ok": True, "status": "not_linked", "url": ""}
    db.confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_code_binds_max_to_the_telegram_person(db):
    db.channels["5001"] = "max:whieda:5001"  # уже писал боту Max — отдельная запись без Telegram
    db.actors["max:whieda:5001"] = None
    db.add_code("code-1")
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="link_code-1"), return_to=RETURN)
    assert out["status"] == "linked" and out["url"]
    assert db.channels["5001"] == "telegram:whieda:700"
    again = await webapp.max_webapp("whieda", init_data=_signed(start_param="link_code-1"), return_to=RETURN)
    assert again["status"] == "linked", "reopening one's own link is not an error"


@pytest.mark.asyncio
@pytest.mark.parametrize(("setup", "status"), [
    (lambda db: db.add_code("code-1", expired=True), "link_expired"),
    (lambda db: db.add_code("code-1", used_by="9999"), "link_used"),
    (lambda db: None, "link_not_found"),
])
async def test_bad_link_codes_do_not_bind(db, setup, status):
    setup(db)
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="link_code-1"), return_to=RETURN)
    assert out["status"] == status and out["url"] == ""
    assert "5001" not in db.channels


@pytest.mark.asyncio
async def test_expired_login_challenge_is_reported_not_raised(db):
    db.channels["5001"] = "telegram:whieda:700"
    db.confirm.side_effect = HTTPException(status_code=410, detail={"error": "challenge_expired"})
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_old"), return_to=RETURN)
    assert out["status"] == "login_expired" and out["url"]


@pytest.mark.asyncio
async def test_without_start_param_signs_in_inside_max(db):
    db.channels["5001"] = "telegram:whieda:700"
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    assert out["status"] == "signed_in" and out["url"].endswith("#wwc-login=cid.nonce")


@pytest.mark.asyncio
async def test_foreign_signature_and_stale_data_are_rejected(db):
    with pytest.raises(HTTPException) as foreign:
        await webapp.max_webapp("whieda", init_data=_signed(token="other-bot"), return_to=RETURN)
    assert foreign.value.status_code == 401
    with pytest.raises(HTTPException) as stale:
        await webapp.max_webapp("whieda", init_data=_signed(auth_date=int(time.time()) - 2 * 3600), return_to=RETURN)
    assert stale.value.detail == {"error": "init_data_expired"}


def test_whole_string_encoded_once_more_is_accepted():
    with patch.object(webapp, "get_settings", return_value=SETTINGS):
        user, start = webapp.verify_max_init_data(quote(_signed(start_param="login_x"), safe=""))
    assert user["id"] == 5001 and start == "login_x"


@pytest.mark.asyncio
async def test_telegram_person_gets_a_max_link(db):
    link = await webapp.create_max_link("whieda", telegram_user_id=700)
    assert link.startswith("https://max.ru/se_test_bot?startapp=link_")
    code = link.split("link_", 1)[1]
    assert len(code) <= 500 and all(c.isalnum() or c in "-_" for c in code)
    with pytest.raises(HTTPException):
        await webapp.create_max_link("whieda", telegram_user_id=123)


def test_site_login_link_opens_the_max_mini_app():
    assert build_max_login_link("@se_test_bot", "Ab-_9") == "https://max.ru/se_test_bot?startapp=login_Ab-_9"


@pytest.mark.asyncio
async def test_unknown_login_channel_is_rejected_before_any_write():
    with pytest.raises(HTTPException) as info:
        await create_content_challenge("whieda", browser_nonce="n" * 20, return_to="/", channel="vk")
    assert info.value.detail == {"error": "invalid_login_channel"}


def test_max_command_reaches_the_cabinet_handler():
    assert is_referral_command("/max") and is_referral_command("/max@wwc_bot")
