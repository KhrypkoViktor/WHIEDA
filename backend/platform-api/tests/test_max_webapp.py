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
SETTINGS = SimpleNamespace(max_bot_token=TOKEN, max_bot_username="se_test_bot", telegram_bot_username="WWC_test_bot")


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
    """lead_actor_channels(max), lead_actors.telegram_user_id, channel_link_codes, account_identities (identities)."""

    def __init__(self) -> None:
        self.channels: dict[str, str] = {}
        # 17.10.2026: вход через Max читает только account_identities: max id -> ("active", telegram_user_id) | ("revoked", None).
        self.identities: dict[str, tuple[str, int | None]] = {}
        self.actors: dict[str, int | None] = {"telegram:whieda:700": 700}
        self.codes: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}

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
        # Запроса «lead_actor_channels(max) → telegram_user_id» здесь нет намеренно: строгий вход его не делает,
        # и любой такой запрос упадёт на AssertionError внизу.
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
        if q.startswith("select telegram_user_id from lead_actors"):
            return {"telegram_user_id": self.actors.get(params[1])} if params[1] in self.actors else None
        if q.startswith("insert into channel_link_codes"):
            self.codes[params[0]] = {"actor_id": params[2], "expires_at": params[3], "used_at": None, "used_channel_user_id": None}
            return {"code_hash": params[0]}
        # V31: заявка со стороны Max
        if q.startswith("insert into max_link_requests"):
            self.requests[params[0]] = {
                "max_user_id": params[2], "username": params[3], "display_name": params[4],
                "login_challenge_hash": params[5], "expires_at": params[6], "used_at": None, "used_by_telegram_user_id": None,
            }
            return {"code_hash": params[0]}
        if q.startswith("select max_user_id"):
            return self.requests.get(params[0])
        if q.startswith("update max_link_requests"):
            self.requests[params[1]].update(used_at=datetime.now(timezone.utc), used_by_telegram_user_id=params[0])
            return {"code_hash": params[1]}
        if q.startswith("insert into lead_actors"):
            self.actors[params[0]] = params[3]
            return {"actor_id": params[0]}
        raise AssertionError(f"unexpected query: {q[:80]}")


@pytest.fixture
def db():
    fake = FakeDb()

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    confirm = AsyncMock(return_value={"ok": True})
    by_hash = AsyncMock(return_value={"ok": True, "return_to": "https://wwc.best/me/"})
    # V34: account_identities — единственный источник «кто за этим Max»; по умолчанию строки нет.
    async def identity_state(_conn, _tenant, provider, external_id):
        assert provider == "max"
        return fake.identities.get(external_id, ("none", None))

    async def ensure_identity(_conn, _tenant, *, telegram_user_id, provider, external_id, **_kw):
        assert provider == "max"
        if fake.identities.get(external_id, ("none", None))[0] != "active":
            fake.identities[external_id] = ("active", int(telegram_user_id))

    identity = AsyncMock(side_effect=identity_state)
    ensure = AsyncMock(side_effect=ensure_identity)
    redeem = AsyncMock()
    bot_login = AsyncMock(return_value="cid.nonce")
    with patch.object(webapp, "get_settings", return_value=SETTINGS), \
         patch.object(webapp, "tenant_connection", connection), \
         patch.object(webapp, "fetch_one", fake.fetch_one), \
         patch.object(webapp, "warm_hosts", AsyncMock()), \
         patch.object(webapp, "confirm_content_from_telegram", confirm), \
         patch.object(webapp, "confirm_content_by_hash", by_hash), \
         patch.object(webapp, "identity_state", identity), \
         patch.object(webapp, "ensure_identity", ensure), \
         patch.object(webapp, "redeem_max_code", redeem), \
         patch.object(webapp, "create_bot_login", bot_login):
        fake.confirm = confirm
        fake.by_hash = by_hash
        fake.identity = identity
        fake.ensure = ensure
        fake.redeem = redeem
        fake.bot_login = bot_login
        yield fake


RETURN = "https://wwc.best/max-app/"


@pytest.mark.asyncio
async def test_linked_person_confirms_the_browser_login(db):
    db.identities["5001"] = ("active", 700)
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_AbC-123_x"), return_to="https://wwc.best/me/")
    assert out["status"] == "login_confirmed"
    db.confirm.assert_awaited_once_with(tenant_id="whieda", challenge_token="AbC-123_x", telegram_user_id=700)
    assert out["url"] == "https://wwc.best/me/#wwc-login=cid.nonce", "can also continue right inside Max"


@pytest.mark.asyncio
async def test_unknown_max_user_is_asked_to_link_first(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to=RETURN)
    assert out["status"] == "not_linked" and out["url"] == "" and out["browser_login_url"] == ""
    db.confirm.assert_not_awaited()
    # 12.10.2026: одна кнопка в Telegram-бот вместо «отправьте /max»
    assert out["telegram_url"].startswith("https://t.me/WWC_test_bot?start=maxlink_")
    assert len(out["telegram_url"].split("start=", 1)[1]) <= 64, "Telegram start payload limit"
    (request,) = db.requests.values()
    assert request["max_user_id"] == "5001"
    assert request["login_challenge_hash"] == hashlib.sha256(b"abc").hexdigest(), "the pending site login rides along"


@pytest.mark.asyncio
async def test_one_tap_in_telegram_links_max_and_confirms_the_waiting_login(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to=RETURN)
    code = out["telegram_url"].split("maxlink_", 1)[1]
    result = await webapp.redeem_link_request("whieda", code=code, telegram_user_id=700, telegram_chat_id=700, telegram_username="viktor")
    assert result == {"status": "linked", "login": "confirmed", "return_to": "https://wwc.best/me/"}
    assert db.channels["5001"] == "telegram:whieda:700"
    db.by_hash.assert_awaited_once_with(
        tenant_id="whieda", challenge_hash=hashlib.sha256(b"abc").hexdigest(), telegram_user_id=700, telegram_chat_id=700,
    )
    again = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_next"), return_to=RETURN)
    assert again["status"] == "login_confirmed", "next time Max signs in by itself"
    other = await webapp.redeem_link_request("whieda", code=code, telegram_user_id=999, telegram_chat_id=999, telegram_username=None)
    assert other["status"] == "used" and db.channels["5001"] == "telegram:whieda:700"


@pytest.mark.asyncio
async def test_one_tap_without_waiting_login_and_for_a_new_telegram_person(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(user_id=6002), return_to=RETURN)
    code = out["telegram_url"].split("maxlink_", 1)[1]
    result = await webapp.redeem_link_request("whieda", code=code, telegram_user_id=801, telegram_chat_id=801, telegram_username=None)
    assert result == {"status": "linked", "login": None, "return_to": ""}
    assert db.channels["6002"] == "telegram:whieda:801" and db.actors["telegram:whieda:801"] == 801
    db.by_hash.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_one_tap_link_does_not_bind(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    code = out["telegram_url"].split("maxlink_", 1)[1]
    next(iter(db.requests.values()))["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    result = await webapp.redeem_link_request("whieda", code=code, telegram_user_id=700, telegram_chat_id=700, telegram_username=None)
    assert result["status"] == "expired" and "5001" not in db.channels
    assert (await webapp.redeem_link_request("whieda", code="nope", telegram_user_id=700, telegram_chat_id=700, telegram_username=None))["status"] == "not_found"


@pytest.mark.asyncio
async def test_telegram_start_maxlink_answers_in_plain_words():
    from app.telegram import content_access as tg

    update = {"update_id": 1, "message": {"message_id": 5, "text": "/start maxlink_Abc", "chat": {"id": 700, "type": "private"}, "from": {"id": 700, "username": "viktor"}}}
    binding = SimpleNamespace(tenant=SimpleNamespace(tenant_id="whieda"), bot_token="t")
    sent = []
    with patch.object(tg, "current_bot_binding", return_value=binding), \
         patch.object(tg, "redeem_link_request", AsyncMock(return_value={"status": "linked", "login": "confirmed", "return_to": "https://wwc.best/me/"})) as redeem, \
         patch.object(tg, "deliver_text", AsyncMock(side_effect=lambda chat, text, markup=None: sent.append((text, markup)))):
        out = await tg.try_handle_content_access(update, trace_id="t")
    assert out["route"] == "max_link" and out["login"] == "confirmed"
    redeem.assert_awaited_once_with("whieda", code="Abc", telegram_user_id=700, telegram_chat_id=700, telegram_username="viktor")
    text, markup = sent[0]
    assert "Max привязан" in text and "Вход на сайт подтверждён" in text
    assert markup["inline_keyboard"][0][0]["web_app"]["url"].startswith("https://wwc.best/tg-open/")


@pytest.mark.asyncio
async def test_link_code_binds_max_to_the_telegram_person(db):
    db.channels["5001"] = "max:whieda:5001"  # уже писал боту Max — отдельная запись без Telegram
    db.actors["max:whieda:5001"] = None
    db.add_code("code-1")
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="link_code-1"), return_to=RETURN)
    assert out["status"] == "linked" and out["url"]
    assert db.channels["5001"] == "telegram:whieda:700"
    # 17.10.2026: вход читает только account_identities — подтверждённая ссылка /max обязана писать туда.
    assert db.identities["5001"] == ("active", 700)
    assert db.ensure.await_args.kwargs["linked_by"] == "telegram:/max" and db.ensure.await_args.kwargs["external_id"] == "5001"
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
    db.identities["5001"] = ("active", 700)
    db.confirm.side_effect = HTTPException(status_code=410, detail={"error": "challenge_expired"})
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_old"), return_to=RETURN)
    assert out["status"] == "login_expired" and out["url"]


@pytest.mark.asyncio
async def test_without_start_param_signs_in_inside_max(db):
    db.identities["5001"] = ("active", 700)
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


def test_max_command_text_matches_the_new_login_screen():
    """17.10.2026: текст /max повторяет путь на сайте «Войти» → «Не получается через Telegram?» → «Войти через Max»."""
    from app.telegram.referral_bonus import MAX_LINK_TEXT

    assert MAX_LINK_TEXT == (
        "Вход на сайт через Max\n\n"
        "Нажмите кнопку — откроется Max и запомнит, что это вы. Это нужно один раз.\n"
        "Дальше на сайте: «Войти» → «Не получается через Telegram?» → «Войти через Max» — "
        "сайт откроется в вашем браузере.\n\n"
        "Ссылка действует сутки и только для вас — не пересылайте её."
    )


# ---- V34 (15.10.2026): код владельца в мини-приложении, browser_login_url, account_identities ----------


@pytest.mark.asyncio
async def test_owner_code_links_an_unknown_max_and_opens_the_browser(db):
    from app.content_access.service import create_bot_login  # noqa: F401 — патчится в фикстуре

    db.redeem.return_value = {"telegram_user_id": 700, "identity_id": "id-1", "already_linked": False}
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to="https://wwc.best/max-app/", code="483 921")
    assert out["status"] == "linked" and out["browser_login_url"] == "https://wwc.best/max-app/#wwc-login=cid.nonce"
    db.redeem.assert_awaited_once_with("whieda", max_user_id=5001, display_name="Айгуль", code="483 921")
    assert "telegram_url" not in out, "no Telegram button once the person is known"
    # Одноразовая ссылка для системного браузера — 10 минут, не 30 как у входа внутри Max.
    ttls = {call.kwargs.get("ttl_minutes") for call in db.bot_login.await_args_list}
    assert 10 in ttls
    assert not db.requests, "no maxlink request when the code did the job"


@pytest.mark.asyncio
@pytest.mark.parametrize("code_error", ["code_invalid", "code_expired", "code_used", "code_locked", "identity_taken"])
async def test_wrong_owner_code_is_reported_as_a_status(db, code_error):
    from app.login_identities.rules import error

    db.redeem.side_effect = error(code_error)
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN, code="000000")
    assert out == {"ok": True, "status": code_error, "url": "", "browser_login_url": "", "telegram_url": ""}
    assert "5001" not in db.channels and not db.requests


@pytest.mark.asyncio
async def test_blank_code_is_ignored_and_the_old_telegram_button_still_comes(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN, code="   ")
    assert out["status"] == "not_linked" and out["telegram_url"].startswith("https://t.me/")
    db.redeem.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_table_wins_and_a_revoked_max_does_not_fall_back_to_the_old_link(db):
    db.identities["5001"] = ("active", 700)
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    assert out["status"] == "signed_in" and out["browser_login_url"].endswith("#wwc-login=cid.nonce")
    db.identities["5001"] = ("revoked", None)
    db.channels["5001"] = "telegram:whieda:700"  # прежняя привязка ещё в lead_actor_channels
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    assert out["status"] == "not_linked" and out["browser_login_url"] == ""


@pytest.mark.asyncio
async def test_channel_row_on_a_partner_actor_without_identity_is_not_a_login(db):
    """17.10.2026: Max-пользователь с username партнёра «прилип» к его actor в lead_actor_channels — входа это не даёт."""
    db.channels["5001"] = "telegram:whieda:700"  # actor партнёра, у actor есть telegram_user_id=700
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to=RETURN)
    assert out["status"] == "not_linked"
    assert out["url"] == "" and out["browser_login_url"] == ""
    db.confirm.assert_not_awaited()
    db.bot_login.assert_not_awaited()
    db.ensure.assert_not_awaited()
    assert db.identities == {}, "nothing is written into account_identities from the channel row"
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    assert out["status"] == "not_linked" and db.identities == {}


@pytest.mark.asyncio
async def test_one_tap_from_telegram_also_records_the_identity(db):
    out = await webapp.max_webapp("whieda", init_data=_signed(), return_to=RETURN)
    code = out["telegram_url"].split("maxlink_", 1)[1]
    await webapp.redeem_link_request("whieda", code=code, telegram_user_id=700, telegram_chat_id=700, telegram_username=None)
    assert db.ensure.await_args.kwargs["linked_by"] == "telegram:maxlink" and db.ensure.await_args.kwargs["external_id"] == "5001"


@pytest.mark.asyncio
async def test_browser_login_url_prefers_the_page_where_login_was_pressed(db):
    db.identities["5001"] = ("active", 700)
    db.confirm.return_value = {"ok": True, "return_to": "https://samtsova.wwc.best/price/"}
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to="https://wwc.best/max-app/")
    assert out["status"] == "login_confirmed"
    assert out["browser_login_url"] == "https://samtsova.wwc.best/price/#wwc-login=cid.nonce"
    assert out["url"] == "https://wwc.best/max-app/#wwc-login=cid.nonce", "in-app link unchanged"
    # Путь без хоста (старый клиент) — на хосте мини-приложения.
    db.confirm.return_value = {"ok": True, "return_to": "/otvety/"}
    out = await webapp.max_webapp("whieda", init_data=_signed(start_param="login_abc"), return_to="https://samtsova.wwc.best/max-app/")
    assert out["browser_login_url"] == "https://samtsova.wwc.best/otvety/#wwc-login=cid.nonce"


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_max_command_sends_the_link_button_or_a_plain_apology(fails):
    # 12.10.2026: /max падал с NameError (не было импортов) — бот отвечал «не удалось обработать».
    from app.telegram import referral_bonus as rb

    sent = []
    create = AsyncMock(side_effect=HTTPException(status_code=404, detail={"error": "actor_not_found"})) if fails \
        else AsyncMock(return_value="https://max.ru/se_test_bot?startapp=link_x")
    with patch.object(rb, "_actor_for_telegram", AsyncMock(return_value="telegram:whieda:700")), \
         patch.object(rb, "create_max_link", create), \
         patch.object(rb, "_deliver", AsyncMock(side_effect=lambda chat, text, reply_markup=None: sent.append((text, reply_markup)))):
        out = await rb.show_max_link(SimpleNamespace(tenant_id="whieda"), telegram_user_id=700, telegram_chat_id=700, raw_update={}, trace_id="t")
    text, markup = sent[0]
    if fails:
        assert out["status"] == "unavailable" and "временно недоступен" in text and markup is None
    else:
        assert out["ok"] and markup["inline_keyboard"][0][0] == {"text": "Привязать Max", "url": "https://max.ru/se_test_bot?startapp=link_x"}
