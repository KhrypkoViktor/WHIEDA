"""Вход через Google/Max по коду владельца (V34, 15.10.2026) на настоящем PostgreSQL.

  * миграция применяется дважды; прежняя привязка Max (lead_actor_channels) переносится бэкфиллом;
  * код: по ref, по имени (несколько — список), по id; новый код гасит прежний; чужому не выдаётся;
  * Google: start → callback (обмен кода подделан, id_token без подписи) → «нужен код» → неверный код,
    верный код → сессия того же вида, что после Telegram; повторный вход — сразу; state одноразовый;
  * занятый sub у другого человека — identity_taken; отвязка в кабинете закрывает вход до нового кода;
  * Max: код из мини-приложения; шестая попытка — code_locked; отозванный Max не входит по старой таблице;
  * RLS: соседний тенант строк не видит.
"""

from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database
from tests.test_login_identities import _claims, _jwt

# Сессии сайта: visitor_sessions (identity_journey) и content_access_sessions — на бою есть, в базовом
# наборе testkit их нет.
LOGIN_MIGRATIONS = (*MIGRATIONS, "platform_identity_journey_v1.sql", "platform_content_access_v1.sql")

OWNER, EVGENIA, PETROVA, STRANGER = 2, 700, 701, 702
MAX_ID = 5001

SEED = """
insert into tenants (tenant_id, display_name, status) values ('other', 'Other', 'active')
on conflict (tenant_id) do nothing;
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('viktor', 'whieda', 'Виктор', '2'),
  ('volkova', 'whieda', 'Евгения Волкова', '700'),
  ('petrova', 'whieda', 'Евгения Петрова', '701'),
  ('stranger', 'whieda', 'Посторонний', '702'),
  ('max-only', 'whieda', 'Max без Telegram', null);
update lead_actors set telegram_user_id = 2 where actor_id = 'viktor';
update lead_actors set telegram_user_id = 700 where actor_id = 'volkova';
update lead_actors set telegram_user_id = 701 where actor_id = 'petrova';
update lead_actors set telegram_user_id = 702 where actor_id = 'stranger';
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('volkova', 'whieda', 'volkova', 'named', '{"display_name": "Евгения Волкова"}', true),
  ('petrova', 'whieda', 'petrova', 'named', '{"display_name": "Евгения Петрова"}', true);
-- Прежняя привязка Max (V30/V31) у Петровой и Max без Telegram — бэкфилл берёт только первую.
insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, display_name) values
  ('whieda', 'petrova', 'max', '6001', 'Евгения П.'),
  ('whieda', 'max-only', 'max', '6002', 'Без Telegram');
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "")
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_CLIENT_ID", "client-1")
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_REDIRECT", "https://wwc.best/api/v1/content-access/login/google/callback")
    monkeypatch.setenv("PLATFORM_COOKIE_SHARED_DOMAINS", "wwc.best")


@pytest.mark.integration
def test_login_identities_end_to_end(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_login_identities") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, LOGIN_MIGRATIONS, twice=True)
            conn.execute(SEED)
            # Бэкфилл идемпотентен: третий прогон ничего не дублирует.
            conn.execute(open(db_path("platform_login_identities_v34.sql"), encoding="utf-8").read())
            rows = conn.execute(
                "select telegram_user_id, external_id, linked_by from account_identities where provider = 'max' order by external_id"
            ).fetchall()
            assert rows == [(PETROVA, "6001", "telegram:/max")], "legacy Max link of a Telegram person only, once"
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.content_access.service import validate_content_session
            from app.db import tenant_connection
            from app.login_identities import google as google_oauth
            from app.login_identities import service
            from app.login_identities.rules import LoginIdentityError, sha256_hex
            from app.max import webapp

            # --- коды владельца -----------------------------------------------------------------
            with pytest.raises(LoginIdentityError) as info:
                await service.issue_code("whieda", issued_by=STRANGER, target="volkova")
            assert info.value.code == "forbidden"
            with pytest.raises(LoginIdentityError) as info:
                await service.issue_code("whieda", issued_by=OWNER, target="Евгения")
            assert info.value.code == "partner_ambiguous"
            assert sorted(p["ref_code"] for p in info.value.details["candidates"]) == ["petrova", "volkova"]
            with pytest.raises(LoginIdentityError) as info:
                await service.issue_code("whieda", issued_by=OWNER, target="никого")
            assert info.value.code == "partner_not_found"

            first = await service.issue_code("whieda", issued_by=OWNER, target="Волкова")
            assert first["person"]["telegram_user_id"] == EVGENIA and first["person"]["ref_code"] == "volkova"
            second = await service.issue_code("whieda", issued_by=OWNER, target="VOLKOVA")
            by_id = await service.issue_code("whieda", issued_by=OWNER, target=str(PETROVA))
            assert by_id["person"]["ref_code"] == "petrova"
            async with tenant_connection("whieda") as conn:
                until = await service.active_code_info(conn, "whieda", EVGENIA)
                assert until is not None and until > second["expires_at"] - timedelta(seconds=5)
                assert (await service.find_people(conn, "whieda", "Посторонний"))[0]["telegram_user_id"] == STRANGER, "person without a site by lead_actors name"

            # --- Google: start → callback → код -----------------------------------------------------
            auth_url = await service.google_start(
                "whieda", host="samtsova.wwc.best", return_to="/price/", visitor_session_id=None
            )
            query = parse_qs(urlparse(auth_url).query)
            state, nonce = query["state"][0], query["nonce"][0]
            tokens = {"id_token": _jwt(_claims(nonce=nonce, sub="g-700"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)) as exchange:
                result = await service.google_callback("whieda", state=state, code="auth-code", oauth_error=None)
            assert exchange.await_args.kwargs["code"] == "auth-code" and exchange.await_args.kwargs["code_verifier"]
            assert result["outcome"] == "link_required" and result["host"] == "samtsova.wwc.best"
            assert result["return_to"] == "https://samtsova.wwc.best/price/"
            link_token = result["link_token"]
            with pytest.raises(LoginIdentityError) as info:
                await service.google_callback("whieda", state=state, code="auth-code", oauth_error=None)
            assert info.value.code == "google_state_invalid", "state is single-use"

            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=first["code"])
            assert info.value.code == "code_invalid", "the first code was cancelled by the second"
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token="nope", code=second["code"])
            assert info.value.code == "pending_invalid"
            linked = await service.link_pending("whieda", link_token=link_token, code=second["code"])
            assert linked["telegram_user_id"] == EVGENIA and not linked["already_linked"]
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=second["code"])
            assert info.value.code == "pending_invalid", "pending is single-use too"
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_code("whieda", code=second["code"], provider="google", external_id="g-700")
            assert info.value.code == "code_used"

            payload, raw_session = await service.session_for(
                "whieda", telegram_user_id=linked["telegram_user_id"], visitor_session_id=linked["visitor_session_id"], source="google_login"
            )
            assert payload["status"] == "authenticated"
            session = await validate_content_session("whieda", raw_session)
            assert session and int(session["telegram_user_id"]) == EVGENIA and session["scope"] == "telegram_verified"

            methods = await service.list_login_methods("whieda", EVGENIA)
            assert [m["provider"] for m in methods] == ["telegram", "google"] and methods[1]["label"] == "evgenia@example.com"

            # Повторный вход через Google — сразу человек, без кода.
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce=query["nonce"][0], sub="g-700"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                again = await service.google_callback("whieda", state=query["state"][0], code="c2", oauth_error=None)
            assert again["outcome"] == "signed_in" and again["telegram_user_id"] == EVGENIA

            # Чужой nonce / отказ Google — ошибка с возвратом на страницу, не исключение.
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce="forged", sub="g-700"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                bad = await service.google_callback("whieda", state=query["state"][0], code="c3", oauth_error=None)
            assert bad == {"outcome": "error", "error": "google_token_invalid", "host": "wwc.best", "return_to": "https://wwc.best/me/"}
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            denied = await service.google_callback("whieda", state=query["state"][0], code=None, oauth_error="access_denied")
            assert denied["outcome"] == "error" and denied["error"] == "google_denied"

            # Тот же Google у другого человека — занято; код Петровой остаётся действующим.
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_code("whieda", code=by_id["code"], provider="google", external_id="g-700", email="x@y.z")
            assert info.value.code == "identity_taken"

            # Отвязка в кабинете: следующий Google-вход снова просит код.
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="google", revoked_by="self:700") == 1
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="google", revoked_by="self:700") == 0
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce=query["nonce"][0], sub="g-700"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                after_revoke = await service.google_callback("whieda", state=query["state"][0], code="c4", oauth_error=None)
            assert after_revoke["outcome"] == "link_required"

            # --- Max ------------------------------------------------------------------------------------
            code_for_petrova = by_id["code"]
            for wrong in range(5):
                with pytest.raises(LoginIdentityError) as info:
                    await service.redeem_max_code("whieda", max_user_id=MAX_ID, display_name="Айгуль", code="000000")
                assert info.value.code == "code_invalid", wrong
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_max_code("whieda", max_user_id=MAX_ID, display_name="Айгуль", code=code_for_petrova)
            assert info.value.code == "code_locked", "sixth try in the window is locked even with the right code"
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_max_code("whieda", max_user_id=MAX_ID, display_name="Айгуль", code=code_for_petrova)
            assert info.value.code == "code_locked", "and the lock holds for the whole window, no fresh five tries"
            # Google: та же личность после пяти неверных кодов — новый вход через Google тоже заперт.
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce=query["nonce"][0], sub="g-locked"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                pending_g = await service.google_callback("whieda", state=query["state"][0], code="c5", oauth_error=None)
            for _ in range(5):
                with pytest.raises(LoginIdentityError):
                    await service.link_pending("whieda", link_token=pending_g["link_token"], code="000000")
            auth_url = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce=query["nonce"][0], sub="g-locked"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                locked_g = await service.google_callback("whieda", state=query["state"][0], code="c6", oauth_error=None)
            assert locked_g["outcome"] == "error" and locked_g["error"] == "code_locked"
            fresh = await service.issue_code("whieda", issued_by=OWNER, target="volkova")
            max_linked = await service.redeem_max_code("whieda", max_user_id=MAX_ID + 1, display_name="Евгения В.", code=fresh["code"])
            assert max_linked["telegram_user_id"] == EVGENIA
            async with tenant_connection("whieda") as conn:
                assert await webapp._telegram_id_for_max(conn, "whieda", MAX_ID + 1) == EVGENIA
                assert await webapp._telegram_id_for_max(conn, "whieda", 6001) == PETROVA, "backfilled legacy link"
                assert await webapp._telegram_id_for_max(conn, "whieda", 6002) is None, "Max without a Telegram person"
            assert await service.revoke_identity("whieda", telegram_user_id=PETROVA, provider="max", revoked_by="owner:2") == 1
            async with tenant_connection("whieda") as conn:
                assert await webapp._telegram_id_for_max(conn, "whieda", 6001) is None, "revoked Max does not come back through lead_actor_channels"
                lingering = await conn.execute("select count(*) as n from lead_actor_channels where channel_user_id = '6001'")
                assert (await lingering.fetchone())["n"] == 1, "the Max chat identity of the bot is untouched"

            # --- RLS -------------------------------------------------------------------------------------
            async with tenant_connection("other") as conn:
                for table in ("account_identities", "login_link_codes", "login_pending"):
                    cur = await conn.execute(f"select count(*) as n from {table}")
                    assert (await cur.fetchone())["n"] == 0, table
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select count(*) as n from login_link_codes")
                assert (await cur.fetchone())["n"] >= 4
                cur = await conn.execute("select code_hash from login_link_codes limit 1")
                assert len((await cur.fetchone())["code_hash"]) == 64 and sha256_hex("x") != ""

        db.run_with_app(proof)


def db_path(name: str) -> str:
    from tests.postgres_testkit import SQL_DIR

    return os.fspath(SQL_DIR / name)
