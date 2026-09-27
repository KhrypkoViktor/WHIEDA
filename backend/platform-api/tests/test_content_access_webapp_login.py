"""Вход из мини-приложения Telegram (/tg-open/) — проверка initData и ссылка со входом."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app.content_access.webapp_login import verify_init_data, webapp_login_url

BOT_TOKEN = "123456:TEST-token-for-webapp"
NOW = 1_790_000_000


def _signed(fields: dict[str, str], token: str = BOT_TOKEN, *, include_signature_in_hash: bool = True) -> str:
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    hashed = {k: v for k, v in fields.items() if include_signature_in_hash or k != "signature"}
    check = "\n".join(f"{k}={hashed[k]}" for k in sorted(hashed))
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def _fields(user_id: int = 688931415, auth_date: int = NOW) -> dict[str, str]:
    return {
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Нина", "username": "nina19157"}, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(auth_date),
    }


def test_valid_init_data_returns_user():
    user = verify_init_data(_signed(_fields()), [BOT_TOKEN], now=NOW + 5)
    assert user["id"] == 688931415


def test_signature_field_is_accepted_with_or_without_it_in_hash():
    fields = {**_fields(), "signature": "ed25519-sig"}
    assert verify_init_data(_signed(fields), [BOT_TOKEN], now=NOW)["id"] == 688931415
    assert verify_init_data(_signed(fields, include_signature_in_hash=False), [BOT_TOKEN], now=NOW)["id"] == 688931415


def test_any_tenant_bot_token_may_sign():
    signed = _signed(_fields(), token="999:OTHER")
    assert verify_init_data(signed, ["123:unrelated", "999:OTHER"], now=NOW)["id"] == 688931415


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("688931415", "688931416"),       # подменили пользователя
        lambda s: s + "&extra=1",                             # добавили поле
        lambda s: s.replace("hash=", "hash=0"),                # испорченная подпись
    ],
)
def test_tampered_init_data_is_rejected(mutate):
    with pytest.raises(HTTPException) as info:
        verify_init_data(mutate(_signed(_fields())), [BOT_TOKEN], now=NOW)
    assert info.value.status_code == 401


def test_foreign_bot_token_is_rejected():
    with pytest.raises(HTTPException) as info:
        verify_init_data(_signed(_fields(), token="777:FOREIGN"), [BOT_TOKEN], now=NOW)
    assert info.value.status_code == 401


def test_stale_or_future_init_data_is_rejected():
    with pytest.raises(HTTPException) as stale:
        verify_init_data(_signed(_fields(auth_date=NOW - 16 * 60)), [BOT_TOKEN], now=NOW)
    assert stale.value.detail == {"error": "init_data_expired"}
    with pytest.raises(HTTPException) as future:
        verify_init_data(_signed(_fields(auth_date=NOW + 600)), [BOT_TOKEN], now=NOW)
    assert future.value.detail == {"error": "init_data_expired"}


@pytest.mark.parametrize("raw", ["", "no-hash=1", "x" * 5000])
def test_malformed_init_data_is_rejected(raw):
    with pytest.raises(HTTPException) as info:
        verify_init_data(raw, [BOT_TOKEN], now=NOW)
    assert info.value.status_code == 400


@pytest.mark.asyncio
async def test_login_url_keeps_host_and_path_and_carries_single_use_login():
    signed = _signed(_fields())
    with patch("app.content_access.webapp_login.tenant_bot_tokens", AsyncMock(return_value=[BOT_TOKEN])), \
         patch("app.content_access.webapp_login.verify_init_data", wraps=lambda d, t: verify_init_data(d, t, now=NOW)), \
         patch("app.content_access.webapp_login.create_bot_login", AsyncMock(return_value="cid.nonce")) as bot_login:
        url = await webapp_login_url("whieda", init_data=signed, return_to="https://lara.wwc.best/reviews/?story=bem-1")
    assert url == "https://lara.wwc.best/reviews/?story=bem-1#wwc-login=cid.nonce"
    bot_login.assert_awaited_once_with("whieda", telegram_user_id=688931415, return_to="/reviews/?story=bem-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("return_to", ["https://evil.example/", "/reviews/"])
async def test_login_url_needs_an_absolute_family_address(return_to):
    with patch("app.content_access.webapp_login.tenant_bot_tokens", AsyncMock(return_value=[BOT_TOKEN])), \
         patch("app.content_access.webapp_login.create_bot_login", AsyncMock()) as bot_login:
        with pytest.raises(HTTPException) as info:
            await webapp_login_url("whieda", init_data=_signed(_fields()), return_to=return_to)
    assert info.value.status_code == 400
    bot_login.assert_not_called()
