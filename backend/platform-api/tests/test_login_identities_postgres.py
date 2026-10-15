"""Вход через Google/Max по коду владельца (V34, 15.10.2026) на настоящем PostgreSQL.

  * миграция применяется дважды (и третий раз — на уже созданных таблицах); прежняя привязка Max
    (lead_actor_channels) переносится бэкфиллом; коды хранятся HMAC с серверным секретом;
  * код: по ref, по имени (несколько — список), по id; новый код гасит прежний; чужому не выдаётся;
  * Google: start → callback (обмен кода подделан, id_token без подписи) → «нужен код» → неверный код,
    верный код → сессия того же вида, что после Telegram; повторный вход — сразу; state одноразовый;
    callback и «ввести код» принимаются только из браузера, который начал вход (cookie/ключ);
    хост вне семьи cookie — подтверждение ключом через /login/link без кода (awaiting_claim);
  * занятый sub у другого человека — identity_taken (код не сгорает); отвязка закрывает вход до нового кода;
  * Max: код из мини-приложения; шестая попытка — code_locked; замок держится всё окно и для Google;
  * гонки: десять параллельных попыток — только пять проходят; два параллельных redeem одним кодом —
    один проходит; отозванный Max не входит по старой таблице;
  * чистка worker удаляет просроченное старше суток; RLS: соседний тенант строк не видит.
"""

from __future__ import annotations

import asyncio
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
CODE_SECRET = "login-code-secret-for-tests"

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
    # Вторая семья cookie — хост «вне семьи» для callback на wwc.best (путь с ключом сайта).
    monkeypatch.setenv("PLATFORM_COOKIE_SHARED_DOMAINS", "wwc.best,other.test")
    monkeypatch.setenv("PLATFORM_LOGIN_CODE_SECRET", CODE_SECRET)


def db_path(name: str) -> str:
    from tests.postgres_testkit import SQL_DIR

    return os.fspath(SQL_DIR / name)


async def _google_pending(
    service, google_oauth, *, host: str, sub: str, site_key: str | None = None, return_to: str = "/me/",
    cookie_key: str | None | object = "auto",
):
    """start → callback с подделанным обменом; (результат callback, ключ cookie от start или None).

    ``cookie_key="auto"`` — браузер предъявляет ту cookie, что выдал start (у витрины её нет);
    иначе — переданное значение (чужая или посторонняя cookie)."""
    auth_url, issued = await service.google_start(
        "whieda", host=host, return_to=return_to, visitor_session_id=None, site_key=site_key
    )
    query = parse_qs(urlparse(auth_url).query)
    tokens = {"id_token": _jwt(_claims(nonce=query["nonce"][0], sub=sub))}
    with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
        result = await service.google_callback(
            "whieda", state=query["state"][0], code="auth-code", oauth_error=None,
            cookie_key=issued if cookie_key == "auto" else cookie_key,
        )
    return result, issued


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
            from app.login_identities import rules, service
            from app.login_identities.rules import LoginIdentityError
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
                cur = await conn.execute("select code_hash from login_link_codes where telegram_user_id = %s and used_at is null and revoked_at is null", (EVGENIA,))
                stored = (await cur.fetchone())["code_hash"]
            assert stored == rules.code_hash("whieda", second["code"], CODE_SECRET), "HMAC with the server secret"
            assert stored != rules.code_hash("whieda", second["code"]), "not a bare sha256 of six digits"

            # --- Google: start → callback → код; браузер проверяется --------------------------------------
            auth_url, key = await service.google_start("whieda", host="samtsova.wwc.best", return_to="/price/", visitor_session_id=None)
            assert key, "family host: the server issues the cookie key"
            query = parse_qs(urlparse(auth_url).query)
            state = query["state"][0]
            # Чужой браузер (нет cookie семьи) — отказ, и state сгорает.
            with pytest.raises(LoginIdentityError) as info:
                await service.google_callback("whieda", state=state, code="auth-code", oauth_error=None, cookie_key=None)
            assert info.value.code == "oauth_browser_mismatch"
            with pytest.raises(LoginIdentityError) as info:
                await service.google_callback("whieda", state=state, code="auth-code", oauth_error=None, cookie_key=key)
            assert info.value.code == "google_state_invalid", "state is single-use even after a refused callback"
            # Другая cookie — тоже отказ.
            auth_url, key = await service.google_start("whieda", host="samtsova.wwc.best", return_to="/price/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            with pytest.raises(LoginIdentityError) as info:
                await service.google_callback("whieda", state=query["state"][0], code="c", oauth_error=None, cookie_key=rules.new_browser_key())
            assert info.value.code == "oauth_browser_mismatch"

            # Атака из повторного ревью: чужая страница открывает жертве start?browser_key=K, атакующий делает
            # свой start с тем же K. На хосте семьи K не становится ни cookie, ни ключом строки — callback
            # атакующего в браузере жертвы (cookie = то, что выдал сервер, или K) не проходит.
            attacker_k = rules.new_browser_key()
            _, victim_cookie = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None, site_key=attacker_k)
            assert victim_cookie and victim_cookie != attacker_k, "query key never becomes the cookie"
            auth_url, _ = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None, site_key=attacker_k)
            attacker_state = parse_qs(urlparse(auth_url).query)["state"][0]
            for presented in (attacker_k, victim_cookie):
                auth_url, _ = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None, site_key=attacker_k)
                with pytest.raises(LoginIdentityError) as info:
                    await service.google_callback(
                        "whieda", state=parse_qs(urlparse(auth_url).query)["state"][0], code="c", oauth_error=None, cookie_key=presented,
                    )
                assert info.value.code == "oauth_browser_mismatch"
            with pytest.raises(LoginIdentityError) as info:
                await service.google_callback("whieda", state=attacker_state, code="c", oauth_error=None, cookie_key=attacker_k)
            assert info.value.code == "oauth_browser_mismatch"
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute(
                    "select count(*) as n from login_pending where browser_key_hash = %s or site_key_hash = %s",
                    (rules.sha256_hex(attacker_k), rules.sha256_hex(attacker_k)),
                )
                assert (await cur.fetchone())["n"] == 0, "a client key is never stored for a family host"

            result, key = await _google_pending(service, google_oauth, host="samtsova.wwc.best", sub="g-700", return_to="/price/")
            assert result["outcome"] == "link_required" and result["claim"] is False and result["host"] == "samtsova.wwc.best"
            assert result["return_to"] == "https://samtsova.wwc.best/price/"
            link_token = result["link_token"]
            info_screen = await service.pending_info("whieda", link_token=link_token)
            assert info_screen == {"provider": "google", "email_masked": "e***a@example.com", "display_name": "Евгения Волкова", "status": "awaiting_code"}

            # Хост семьи: тот же ключ в body без cookie — отказ (body не заменяет cookie), pending закрывается.
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=second["code"], cookie_key=None, body_key=key)
            assert info.value.code == "oauth_browser_mismatch"
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=second["code"], cookie_key=key, body_key=None)
            assert info.value.code == "pending_invalid", "a pending refused once is closed for good"

            result, key = await _google_pending(service, google_oauth, host="samtsova.wwc.best", sub="g-700", return_to="/price/")
            link_token = result["link_token"]
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=second["code"], cookie_key=rules.new_browser_key(), body_key=None)
            assert info.value.code == "oauth_browser_mismatch", "code from another browser"

            result, key = await _google_pending(service, google_oauth, host="samtsova.wwc.best", sub="g-700", return_to="/price/")
            link_token = result["link_token"]
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=None, cookie_key=key, body_key=None)
            assert info.value.code == "code_required"
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=first["code"], cookie_key=key, body_key=None)
            assert info.value.code == "code_invalid", "the first code was cancelled by the second"
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token="nope", code=second["code"], cookie_key=key, body_key=None)
            assert info.value.code == "pending_invalid"
            linked = await service.link_pending("whieda", link_token=link_token, code=second["code"], cookie_key=key, body_key=None)
            assert linked["telegram_user_id"] == EVGENIA and not linked["already_linked"]
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=link_token, code=second["code"], cookie_key=key, body_key=None)
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

            # Повторный вход через Google (семья cookie) — сразу человек, без кода.
            again, _ = await _google_pending(service, google_oauth, host="wwc.best", sub="g-700")
            assert again["outcome"] == "signed_in" and again["telegram_user_id"] == EVGENIA

            # --- Витрина (хост вне семьи cookie): только ключ сайта, cookie не смотрится ----------------------
            with pytest.raises(LoginIdentityError) as info:
                await service.google_start("whieda", host="site.other.test", return_to="/me/", visitor_session_id=None)
            assert info.value.code == "browser_key_required"
            site_key = rules.new_browser_key()
            claim, issued = await _google_pending(service, google_oauth, host="site.other.test", sub="g-700", site_key=site_key)
            assert issued is None, "no cookie for a host outside the family"
            assert claim["outcome"] == "link_required" and claim["claim"] is True and claim["host"] == "site.other.test"
            assert (await service.pending_info("whieda", link_token=claim["link_token"]))["status"] == "awaiting_claim"
            # Витрина: cookie не помогает — даже со значением ключа сайта, без body — отказ.
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=claim["link_token"], code=None, cookie_key=site_key, body_key=None)
            assert info.value.code == "oauth_browser_mismatch"
            # Посторонняя cookie в callback витрины — не ложный mismatch: cookie игнорируется.
            claim, _ = await _google_pending(
                service, google_oauth, host="site.other.test", sub="g-700", site_key=site_key, cookie_key=rules.new_browser_key()
            )
            assert claim["outcome"] == "link_required" and claim["claim"] is True
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=claim["link_token"], code=None, cookie_key=None, body_key=rules.new_browser_key())
            assert info.value.code == "oauth_browser_mismatch", "a victim's browser has no such site key"
            claim, _ = await _google_pending(service, google_oauth, host="site.other.test", sub="g-700", site_key=site_key)
            claimed = await service.link_pending("whieda", link_token=claim["link_token"], code=None, cookie_key=None, body_key=site_key)
            assert claimed["telegram_user_id"] == EVGENIA and claimed["already_linked"]
            with pytest.raises(LoginIdentityError) as info:
                await service.link_pending("whieda", link_token=claim["link_token"], code=None, cookie_key=None, body_key=site_key)
            assert info.value.code == "pending_invalid", "claim is single-use"

            # Чужой nonce / отказ Google — ошибка с возвратом на страницу, не исключение.
            auth_url, key = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            tokens = {"id_token": _jwt(_claims(nonce="forged", sub="g-700"))}
            with patch.object(google_oauth, "exchange_code", AsyncMock(return_value=tokens)):
                bad = await service.google_callback("whieda", state=query["state"][0], code="c3", oauth_error=None, cookie_key=key)
            assert bad == {"outcome": "error", "error": "google_token_invalid", "host": "wwc.best", "return_to": "https://wwc.best/me/"}
            auth_url, key = await service.google_start("whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            query = parse_qs(urlparse(auth_url).query)
            denied = await service.google_callback("whieda", state=query["state"][0], code=None, oauth_error="access_denied", cookie_key=key)
            assert denied["outcome"] == "error" and denied["error"] == "google_denied"

            # Тот же Google у другого человека — занято; код Петровой остаётся действующим (захват откатился).
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_code("whieda", code=by_id["code"], provider="google", external_id="g-700", email="x@y.z")
            assert info.value.code == "identity_taken"
            async with tenant_connection("whieda") as conn:
                assert await service.active_code_info(conn, "whieda", PETROVA) is not None, "identity_taken rolled the claim back"

            # Отвязка в кабинете: следующий Google-вход снова просит код.
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="google", revoked_by="self:700") == 1
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="google", revoked_by="self:700") == 0
            after_revoke, _ = await _google_pending(service, google_oauth, host="wwc.best", sub="g-700")
            assert after_revoke["outcome"] == "link_required" and after_revoke["claim"] is False

            # --- Max: замок пяти попыток, параллельно и для Google ----------------------------------------
            code_for_petrova = by_id["code"]
            outcomes = await asyncio.gather(
                *[service.redeem_max_code("whieda", max_user_id=MAX_ID, display_name="Айгуль", code="000000") for _ in range(10)],
                return_exceptions=True,
            )
            codes = sorted(getattr(o, "code", "ok") for o in outcomes)
            assert codes == ["code_invalid"] * 5 + ["code_locked"] * 5, codes
            with pytest.raises(LoginIdentityError) as info:
                await service.redeem_max_code("whieda", max_user_id=MAX_ID, display_name="Айгуль", code=code_for_petrova)
            assert info.value.code == "code_locked", "the lock holds for the whole window even with the right code"
            # Google: та же личность после пяти неверных кодов — новый вход через Google тоже заперт.
            pending_g, key_g = await _google_pending(service, google_oauth, host="wwc.best", sub="g-locked")
            for _ in range(5):
                with pytest.raises(LoginIdentityError):
                    await service.link_pending("whieda", link_token=pending_g["link_token"], code="000000", cookie_key=key_g, body_key=None)
            locked_g, _ = await _google_pending(service, google_oauth, host="wwc.best", sub="g-locked")
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

            # --- гонка redeem: один код, два параллельных запроса — проходит один ----------------------------
            race = await service.issue_code("whieda", issued_by=OWNER, target="stranger") if False else await service.issue_code("whieda", issued_by=OWNER, target=str(STRANGER))
            outcomes = await asyncio.gather(
                service.redeem_code("whieda", code=race["code"], provider="max", external_id="7001", display_name="A"),
                service.redeem_code("whieda", code=race["code"], provider="max", external_id="7002", display_name="B"),
                return_exceptions=True,
            )
            codes = sorted(getattr(o, "code", "ok") for o in outcomes)
            assert codes == ["code_used", "ok"], codes
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select count(*) as n from account_identities where telegram_user_id = %s and revoked_at is null", (STRANGER,))
                assert (await cur.fetchone())["n"] == 1

            # --- чистка worker --------------------------------------------------------------------------------
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select count(*) as n from login_pending")
                pending_before = (await cur.fetchone())["n"]
                await conn.execute("update login_pending set expires_at = now() - interval '2 days' where status in ('failed', 'linked')")
                await conn.execute("update login_link_codes set expires_at = now() - interval '2 days' where used_at is not null")
            purged = await service.purge_expired_login_rows(["whieda", "other", "missing-tenant"])
            assert purged > 0
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select count(*) as n from login_pending")
                assert (await cur.fetchone())["n"] < pending_before
                cur = await conn.execute("select count(*) as n from login_pending where expires_at < now() - interval '1 day'")
                assert (await cur.fetchone())["n"] == 0
                cur = await conn.execute("select count(*) as n from login_link_codes where used_at is null and revoked_at is null")
                assert (await cur.fetchone())["n"] >= 1, "live codes stay"
            assert await service.login_cleanup_step() == 0, "nothing left for the worker step"

            # --- RLS -------------------------------------------------------------------------------------
            async with tenant_connection("other") as conn:
                for table in ("account_identities", "login_link_codes", "login_pending"):
                    cur = await conn.execute(f"select count(*) as n from {table}")
                    assert (await cur.fetchone())["n"] == 0, table

        db.run_with_app(proof)
