"""Лимит устройств, ключи доступа, Яндекс ID и VK ID (V35, 08.11.2026) на настоящем PostgreSQL.

  * platform_login_devices_v35.sql применяется дважды, провайдеры yandex/vk проходят проверки таблиц V34;
  * лимит: третья сессия выбивает самую давно не виденную (revoked_by = 'device_limit'), чужие сессии
    не трогаются, десять параллельных входов оставляют не больше двух; список «Устройства» и
    «выйти на других устройствах»; подпись устройства и префикс IP сохраняются при входе по challenge;
  * ключ доступа: программный аутентификатор (tests/webauthn_testkit.py) → регистрация → вход → счётчик
    растёт; повтор того же ответа, чужой ключ, чужая подпись, отозванный ключ, чужое удаление — отказ;
  * Яндекс ID и VK ID: start → callback (обмен кода подделан) → код владельца → вход; повторный вход — сразу;
  * RLS: соседний тенант строк ключей не видит; чистка worker удаляет просроченные challenge.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database
from tests.test_login_identities_postgres import EVGENIA, OWNER, SEED, _env
from tests.webauthn_testkit import SoftAuthenticator

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
OTHER_USER = 701
V35 = "platform_login_devices_v35.sql"


def _migrations():
    return (*MIGRATIONS, "platform_identity_journey_v1.sql", "platform_content_access_v1.sql", V35)


@pytest.mark.integration
def test_v35_is_idempotent_and_widens_the_provider_checks(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_login_v35_schema") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, _migrations(), twice=True)
            conn.execute(SEED)
            columns = {
                r[0] for r in conn.execute(
                    "select column_name from information_schema.columns where table_name = 'content_access_sessions'"
                ).fetchall()
            }
            assert {"device_label", "created_ip_hint", "revoked_by"} <= columns
            for provider in ("yandex", "vk", "google", "max"):
                conn.execute(
                    "insert into account_identities (tenant_id, telegram_user_id, provider, external_id) values ('whieda', %s, %s, %s)",
                    (EVGENIA, provider, f"ext-{provider}"),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into account_identities (tenant_id, telegram_user_id, provider, external_id) values ('whieda', %s, 'icq', 'x')",
                    (EVGENIA,),
                )
            conn.execute(
                "insert into login_pending (tenant_id, provider, expires_at) values ('whieda', 'vk', now() + interval '1 minute')"
            )
            conn.execute(
                "insert into login_pending (tenant_id, provider, expires_at) values ('whieda', 'yandex', now() + interval '1 minute')"
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into login_pending (tenant_id, provider, expires_at) values ('whieda', 'icq', now())")
            # Третий прогон на уже заполненных таблицах ничего не ломает.
            conn.execute((db_sql(V35)))


def db_sql(name: str) -> str:
    from tests.postgres_testkit import SQL_DIR

    return (SQL_DIR / name).read_text(encoding="utf-8")


@pytest.mark.integration
def test_device_limit_list_and_revoke_others(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_login_devices") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, _migrations(), twice=True)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.content_access import devices
            from app.content_access import service as access
            from app.db import tenant_connection

            async def login(user: int, user_agent: str | None, ip: str | None = None) -> str:
                _, raw = await access.create_session_for_user(
                    "whieda", telegram_user_id=user, user_agent=user_agent, client_ip_value=ip
                )
                return raw

            async def state() -> dict[str, tuple[str | None, str | None]]:
                async with tenant_connection("whieda") as conn:
                    cur = await conn.execute(
                        "select device_label, revoked_by, revoked_at is not null as revoked, created_ip_hint, session_id::text as sid, last_seen_at "
                        "from content_access_sessions where tenant_id = 'whieda' and telegram_user_id = %s order by created_at",
                        (EVGENIA,),
                    )
                    return await cur.fetchall()

            # --- два устройства живут, третий вход выбивает самое давно не виденное ------------------
            first = await login(EVGENIA, IPHONE, "95.84.12.7")
            second = await login(EVGENIA, WINDOWS, "95.84.99.1")
            other_user = await login(OTHER_USER, ANDROID)
            assert [bool(r["revoked"]) for r in await state()] == [False, False]
            # Первое устройство «видели» после второго (запрос кабинета): значит, вытеснится второе.
            await asyncio.sleep(0.05)
            assert await access.validate_content_session("whieda", first)
            await asyncio.sleep(0.05)
            third = await login(EVGENIA, ANDROID, "10.20.30.40")
            rows = await state()
            assert [(r["device_label"], bool(r["revoked"]), r["revoked_by"]) for r in rows] == [
                ("iPhone · Safari", False, None),
                ("Windows · Chrome", True, "device_limit"),
                ("Android · Chrome", False, None),
            ]
            assert [r["created_ip_hint"] for r in rows] == ["95.84.*.*", "95.84.*.*", "10.20.*.*"], "only a prefix is stored"
            assert await access.validate_content_session("whieda", second) is None, "the evicted session is dead at once"
            assert await access.validate_content_session("whieda", first) and await access.validate_content_session("whieda", third)
            assert await access.validate_content_session("whieda", other_user), "another person is not touched"

            # --- список «Устройства» ---------------------------------------------------------------------
            current_id = next(r["sid"] for r in rows if r["device_label"] == "Android · Chrome")
            async with tenant_connection("whieda") as conn:
                listing = await devices.list_active_sessions(conn, "whieda", EVGENIA, current_session_id=current_id)
            assert sorted((i["device_label"], i["current"]) for i in listing) == [("Android · Chrome", True), ("iPhone · Safari", False)]
            assert all(len(i["id"]) == 8 and i["created_at"] and i["last_seen_at"] for i in listing)

            # --- «выйти на других устройствах» -----------------------------------------------------------
            async with tenant_connection("whieda") as conn:
                hashes = await devices.revoke_other_sessions(conn, "whieda", EVGENIA, current_session_id=current_id)
            assert len(hashes) == 1
            access.clear_session_cache()
            assert await access.validate_content_session("whieda", first) is None
            assert await access.validate_content_session("whieda", third), "the current device stays"
            assert await access.validate_content_session("whieda", other_user)

            # --- гонка: десять входов одновременно — активных не больше двух --------------------------------
            await asyncio.gather(*(login(EVGENIA, WINDOWS) for _ in range(10)))
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute(
                    "select count(*) as n from content_access_sessions where telegram_user_id = %s and revoked_at is null", (EVGENIA,)
                )
                assert (await cur.fetchone())["n"] <= devices.MAX_ACTIVE_SESSIONS

            # --- вход по challenge (Telegram): подпись устройства и лимит работают и здесь ---------------------
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "insert into visitor_sessions (tenant_id, session_id, journey_type, context) values ('whieda', gen_random_uuid(), 'organic', '{}'::jsonb)"
                )
                cur = await conn.execute("select session_id from visitor_sessions where tenant_id = 'whieda' limit 1")
                visitor = (await cur.fetchone())["session_id"]
                await conn.execute(
                    """
                    insert into content_access_challenges
                      (tenant_id, visitor_session_id, challenge_hash, browser_nonce_hash, return_to, status, telegram_user_id, expires_at, approved_at)
                    values ('whieda', %s, 'ch-1', %s, '/', 'approved', %s, now() + interval '5 minutes', now())
                    """,
                    (visitor, access._hash_value("nonce-1"), EVGENIA),
                )
                cur = await conn.execute("select challenge_id::text as cid from content_access_challenges where challenge_hash = 'ch-1'")
                challenge_id = (await cur.fetchone())["cid"]
            payload, raw = await access.poll_content_challenge(
                "whieda", challenge_id=challenge_id, browser_nonce="nonce-1", user_agent=IPHONE, client_ip_value="188.17.5.5"
            )
            assert payload["status"] == "authenticated" and raw
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute(
                    "select device_label, created_ip_hint, revoked_at is null as active from content_access_sessions where session_hash = %s",
                    (access._hash_value(raw),),
                )
                row = await cur.fetchone()
                assert (row["device_label"], row["created_ip_hint"], row["active"]) == ("iPhone · Safari", "188.17.*.*", True)
                cur = await conn.execute(
                    "select count(*) as n from content_access_sessions where telegram_user_id = %s and revoked_at is null", (EVGENIA,)
                )
                assert (await cur.fetchone())["n"] == 2

        db.run_with_app(proof)


@pytest.mark.integration
def test_passkey_register_sign_in_counter_and_refusals(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_login_passkey") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, _migrations(), twice=True)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import tenant_connection
            from app.login_identities import passkeys
            from app.login_identities.rules import LoginIdentityError

            async def fails(coro, reason: str | None = None) -> None:
                with pytest.raises(LoginIdentityError) as info:
                    await coro
                assert info.value.code == "passkey_failed" and info.value.status_code == 401
                if reason:
                    assert info.value.details.get("reason") == reason

            phone = SoftAuthenticator()
            # --- регистрация под сессией -------------------------------------------------------------------
            options = await passkeys.registration_options("whieda", telegram_user_id=EVGENIA, host="wwc.best")
            assert options["rp"]["id"] == "wwc.best"
            assert options["user"]["name"] == "Евгения Волкова", "display_name from lead_actors"
            assert options["authenticatorSelection"]["residentKey"] == "required"
            assert options["user"]["id"] != options["user"]["name"] and str(EVGENIA) not in options["user"]["id"]
            reg = phone.create(options)
            # Чужому человеку этот challenge не подходит (и у владельца он при этом не сгорает).
            await fails(passkeys.register("whieda", telegram_user_id=OTHER_USER, body={"credential": reg}), "challenge")
            assert options["challenge_id"], "options carry challenge_id for the site"
            # challenge_id чужой строки: отказ, а свой challenge при этом не сгорает.
            await fails(passkeys.register(
                "whieda", telegram_user_id=EVGENIA, body={"credential": reg, "challenge_id": "11111111-1111-1111-1111-111111111111"}
            ), "challenge")
            await fails(passkeys.register("whieda", telegram_user_id=EVGENIA, body={"credential": reg, "challenge_id": "junk"}), "challenge_id")
            saved = await passkeys.register(
                "whieda", telegram_user_id=EVGENIA, body={"credential": reg, "challenge_id": options["challenge_id"]}, user_agent=IPHONE
            )
            assert saved["label"] == "iPhone" and saved["id"]
            # Тот же ответ второй раз — challenge одноразовый.
            await fails(passkeys.register("whieda", telegram_user_id=EVGENIA, body={"credential": reg}), "challenge")
            # Тот же ключ второй раз не регистрируется (ни у этого, ни у другого человека).
            options = await passkeys.registration_options("whieda", telegram_user_id=OTHER_USER, host="wwc.best")
            await fails(passkeys.register("whieda", telegram_user_id=OTHER_USER, body={"credential": phone.create(options)}), "duplicate")
            # Подпись чужого origin — отказ.
            evil = SoftAuthenticator()
            options = await passkeys.registration_options("whieda", telegram_user_id=EVGENIA, host="wwc.best")
            await fails(passkeys.register("whieda", telegram_user_id=EVGENIA, body={"credential": evil.create(options, origin="https://evil.example")}), "verify")
            # Уже зарегистрированные ключи исключаются из следующих options.
            options = await passkeys.registration_options("whieda", telegram_user_id=EVGENIA, host="wwc.best")
            assert len(options["excludeCredentials"]) == 1

            listing = await passkeys.list_passkeys("whieda", EVGENIA)
            assert [(p["label"], p["last_used_at"]) for p in listing] == [("iPhone", None)]
            assert await passkeys.list_passkeys("whieda", OTHER_USER) == []

            # --- вход без сессии: discoverable, без имени -------------------------------------------------------
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            assert auth["rpId"] == "wwc.best" and not auth.get("allowCredentials")
            assert auth["challenge_id"], "no cookie binds the challenge: the id comes back in verify"
            answer = phone.get(auth)  # счётчик 1
            person = await passkeys.authenticate("whieda", body={"credential": answer, "challenge_id": auth["challenge_id"]})
            assert person["telegram_user_id"] == EVGENIA
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select sign_count, last_used_at from passkey_credentials where telegram_user_id = %s", (EVGENIA,))
                row = await cur.fetchone()
                assert row["sign_count"] == 1 and row["last_used_at"] is not None, "the counter is stored"
            # Тот же ответ второй раз — challenge уже использован.
            await fails(passkeys.authenticate("whieda", body={"credential": answer}), "challenge")
            # Следующий вход: счётчик растёт.
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            await passkeys.authenticate("whieda", body=phone.get(auth))
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select sign_count from passkey_credentials where telegram_user_id = %s", (EVGENIA,))
                assert (await cur.fetchone())["sign_count"] == 2
            # Клон: счётчик не вырос — отказ, счётчик в базе прежний.
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            await fails(passkeys.authenticate("whieda", body={"credential": phone.get(auth, sign_count=2)}), "verify")
            # Чужой (незарегистрированный) ключ и подпись другим ключом под чужим credential_id — 401 без подробностей.
            stranger = SoftAuthenticator()
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            await fails(passkeys.authenticate("whieda", body={"credential": stranger.get(auth)}), "unknown_credential")
            from cryptography.hazmat.primitives.asymmetric import ec

            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            await fails(
                passkeys.authenticate("whieda", body={"credential": phone.get(auth, sign_count=9, private_key=ec.generate_private_key(ec.SECP256R1()))}),
                "verify",
            )
            # Challenge из регистрации не годится для входа, и наоборот.
            options = await passkeys.registration_options("whieda", telegram_user_id=EVGENIA, host="wwc.best")
            await fails(passkeys.authenticate("whieda", body={"credential": phone.get({"challenge": options["challenge"]})}), "challenge")
            # Просроченный challenge.
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            async with tenant_connection("whieda") as conn:
                await conn.execute("update passkey_challenges set expires_at = now() - interval '1 second' where used_at is null and kind = 'passkey_auth'")
            await fails(passkeys.authenticate("whieda", body={"credential": phone.get(auth)}), "challenge")

            # --- счётчик ключей для /me, удаление ---------------------------------------------------------------
            async with tenant_connection("whieda") as conn:
                assert await passkeys.count_passkeys(conn, "whieda", EVGENIA) == 1
                assert await passkeys.count_passkeys(conn, "whieda", OTHER_USER) == 0
            assert not await passkeys.delete_passkey("whieda", telegram_user_id=OTHER_USER, passkey_id=saved["id"]), "not your key"
            assert not await passkeys.delete_passkey("whieda", telegram_user_id=EVGENIA, passkey_id="not-a-uuid")
            assert await passkeys.delete_passkey("whieda", telegram_user_id=EVGENIA, passkey_id=saved["id"])
            assert not await passkeys.delete_passkey("whieda", telegram_user_id=EVGENIA, passkey_id=saved["id"]), "second delete"
            assert await passkeys.list_passkeys("whieda", EVGENIA) == []
            auth = await passkeys.authentication_options("whieda", host="wwc.best")
            await fails(passkeys.authenticate("whieda", body={"credential": phone.get(auth)}), "unknown_credential")

            # --- RLS: соседний тенант ключей не видит, чистка удаляет старые challenge -----------------------------
            async with tenant_connection("other") as conn:
                cur = await conn.execute("select count(*) as n from passkey_credentials")
                assert (await cur.fetchone())["n"] == 0
                cur = await conn.execute("select count(*) as n from passkey_challenges")
                assert (await cur.fetchone())["n"] == 0
            async with tenant_connection("whieda") as conn:
                await conn.execute("update passkey_challenges set expires_at = now() - interval '2 days'")
            from app.login_identities import service

            assert await service.purge_expired_login_rows(["whieda"]) >= 1
            async with tenant_connection("whieda") as conn:
                cur = await conn.execute("select count(*) as n from passkey_challenges")
                assert (await cur.fetchone())["n"] == 0

        db.run_with_app(proof)


@pytest.mark.integration
def test_yandex_and_vk_link_by_owner_code_then_sign_in_directly(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_ID", "y-id")
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_SECRET", "y-secret")
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_REDIRECT", "https://wwc.best/api/v1/content-access/login/yandex/callback")
    monkeypatch.setenv("PLATFORM_VK_ID_CLIENT_ID", "vk-id")
    monkeypatch.setenv("PLATFORM_VK_ID_REDIRECT", "https://wwc.best/api/v1/content-access/login/vk/callback")
    with temporary_database("whieda_login_oauth_v35") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, _migrations(), twice=True)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import tenant_connection
            from app.login_identities import rules, service
            from app.login_identities import vk as vk_oauth
            from app.login_identities import yandex as yandex_oauth
            from app.login_identities.rules import LoginIdentityError

            cases = (
                ("yandex", "ya-4001", "Evgenia@Yandex.ru", {}),
                ("vk", "vk-5001", None, {"device_id": "dev-1"}),
            )
            for provider, external_id, email, extra in cases:
                async def run_start():
                    url, cookie = await service.oauth_start(provider, "whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
                    return parse_qs(urlparse(url).query), cookie

                async def callback(state: str, cookie: str | None, code: str | None = "auth-code"):
                    profile = {"sub": external_id, "email": email.lower() if email else None, "name": "Евгения В."}
                    exchange = AsyncMock(return_value={"access_token": "tok", "user_id": external_id})
                    fetch = AsyncMock(return_value=profile)
                    module = yandex_oauth if provider == "yandex" else vk_oauth
                    with patch.object(module, "exchange_code", exchange), patch.object(module, "fetch_profile", fetch):
                        result = await service.oauth_callback(
                            provider, "whieda", state=state, code=code, oauth_error=None, cookie_key=cookie, extra=extra
                        )
                    return result, exchange

                # Неизвестная личность → экран «код от Виктора».
                query, cookie = await run_start()
                assert query["code_challenge_method"] == ["S256"]
                result, exchange = await callback(query["state"][0], cookie)
                assert result["outcome"] == "link_required" and result["claim"] is False
                if provider == "vk":
                    assert exchange.await_args.kwargs["device_id"] == "dev-1", "device_id from the callback reaches the exchange"
                info = await service.pending_info("whieda", link_token=result["link_token"])
                assert info["provider"] == provider and info["display_name"] == "Евгения В."
                # State одноразовый; чужой браузер (другая cookie) — отказ.
                with pytest.raises(LoginIdentityError) as err:
                    await callback(query["state"][0], cookie)
                assert err.value.code == f"{provider}_state_invalid"
                query2, cookie2 = await run_start()
                with pytest.raises(LoginIdentityError) as err:
                    await callback(query2["state"][0], rules.new_browser_key())
                assert err.value.code == "oauth_browser_mismatch"
                # Неверный код, затем верный код владельца → привязка и сессия.
                with pytest.raises(LoginIdentityError) as err:
                    await service.link_pending("whieda", link_token=result["link_token"], code="000000", cookie_key=cookie, body_key=None)
                assert err.value.code == "code_invalid"
                issued = await service.issue_code("whieda", issued_by=OWNER, target="volkova")
                linked = await service.link_pending("whieda", link_token=result["link_token"], code=issued["code"], cookie_key=cookie, body_key=None)
                assert linked["telegram_user_id"] == EVGENIA and linked["provider"] == provider
                payload, raw = await service.session_for("whieda", telegram_user_id=EVGENIA, visitor_session_id=None, source=f"{provider}_login")
                assert payload["status"] == "authenticated" and raw
                # Следующий вход тем же аккаунтом — сразу, без кода.
                query3, cookie3 = await run_start()
                again, _ = await callback(query3["state"][0], cookie3)
                assert again["outcome"] == "signed_in" and again["telegram_user_id"] == EVGENIA
                # Отказ человека на стороне провайдера и сбой обмена возвращаются кодом провайдера.
                query4, cookie4 = await run_start()
                denied = await service.oauth_callback(provider, "whieda", state=query4["state"][0], code=None, oauth_error="access_denied", cookie_key=cookie4)
                assert denied["outcome"] == "error" and denied["error"] == f"{provider}_denied"
                query5, cookie5 = await run_start()
                module = yandex_oauth if provider == "yandex" else vk_oauth
                with patch.object(module, "exchange_code", AsyncMock(side_effect=rules.error(f"{provider}_token_invalid", reason="status"))):
                    failed = await service.oauth_callback(provider, "whieda", state=query5["state"][0], code="x", oauth_error=None, cookie_key=cookie5, extra=extra)
                assert failed["error"] == f"{provider}_token_invalid"
            # VK без device_id в callback — обмен кода не начинается.
            url, cookie = await service.oauth_start("vk", "whieda", host="wwc.best", return_to="/me/", visitor_session_id=None)
            with patch.object(vk_oauth, "exchange_code", AsyncMock()) as exchange:
                no_device = await service.oauth_callback(
                    "vk", "whieda", state=parse_qs(urlparse(url).query)["state"][0], code="c", oauth_error=None,
                    cookie_key=cookie, extra={},
                )
            assert no_device["error"] == "vk_token_invalid"
            exchange.assert_not_awaited()
            async with tenant_connection("whieda") as conn:
                methods = await service.list_login_methods("whieda", EVGENIA, conn=conn)
            assert [m["provider"] for m in methods] == ["telegram", "yandex", "vk"]
            # Отвязка работает для новых провайдеров, а Telegram по-прежнему нельзя.
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="yandex", revoked_by="self") == 1
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="vk", revoked_by="self") == 1
            async with tenant_connection("whieda") as conn:
                state, uid = await service.identity_state(conn, "whieda", "yandex", "ya-4001")
            assert (state, uid) == ("revoked", None)

        db.run_with_app(proof)


@pytest.mark.integration
def test_logged_in_person_links_yandex_without_the_owner_code(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_ID", "y-id")
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_SECRET", "y-secret")
    monkeypatch.setenv("PLATFORM_YANDEX_OAUTH_REDIRECT", "https://wwc.best/api/v1/content-access/login/yandex/callback")
    with temporary_database("whieda_login_selflink") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, _migrations(), twice=True)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import tenant_connection
            from app.login_identities import service
            from app.login_identities import yandex as yandex_oauth

            async def attempt(sub: str, session_user: int | None, *, host: str = "wwc.best"):
                url, cookie = await service.oauth_start(
                    "yandex", "whieda", host=host, return_to="/me/", visitor_session_id=None,
                    site_key="site-key-0123456789abcdef" if host != "wwc.best" else None, session_user_id=session_user,
                )
                state = parse_qs(urlparse(url).query)["state"][0]
                profile = {"sub": sub, "email": "e@yandex.ru", "name": "Евгения В."}
                with patch.object(yandex_oauth, "exchange_code", AsyncMock(return_value={"access_token": "t"})), \
                     patch.object(yandex_oauth, "fetch_profile", AsyncMock(return_value=profile)):
                    return await service.oauth_callback(
                        "yandex", "whieda", state=state, code="c", oauth_error=None, cookie_key=cookie
                    )

            async def identity_rows(sub: str):
                async with tenant_connection("whieda") as conn:
                    cur = await conn.execute(
                        "select telegram_user_id, linked_by, revoked_by from account_identities where provider = 'yandex' and external_id = %s order by linked_at",
                        (sub,),
                    )
                    return await cur.fetchall()

            # Без сессии — как раньше: нужен код владельца.
            guest = await attempt("ya-self", None)
            assert guest["outcome"] == "link_required" and guest["claim"] is False
            assert await identity_rows("ya-self") == []

            # Со своей сессией — привязано сразу, без кода.
            linked = await attempt("ya-self", EVGENIA)
            assert linked["outcome"] == "linked" and linked["already_linked"] is False and linked["telegram_user_id"] == EVGENIA
            rows = await identity_rows("ya-self")
            assert [(r["telegram_user_id"], r["linked_by"]) for r in rows] == [(EVGENIA, f"self:{EVGENIA}")]
            # Повторно у того же человека — не ошибка и не дубль.
            again = await attempt("ya-self", EVGENIA)
            assert again["outcome"] == "linked" and again["already_linked"] is True and len(await identity_rows("ya-self")) == 1
            # Тот же аккаунт Яндекса у другого вошедшего — отказ, привязка не меняется.
            stolen = await attempt("ya-self", OTHER_USER)
            assert stolen["outcome"] == "error" and stolen["error"] == "identity_taken"
            assert [r["telegram_user_id"] for r in await identity_rows("ya-self")] == [EVGENIA]
            # Без сессии вошедший по этой привязке — сразу в кабинет.
            direct = await attempt("ya-self", None)
            assert direct["outcome"] == "signed_in" and direct["telegram_user_id"] == EVGENIA

            # Отвязал сам — привязать заново можно без кода; отвязал владелец — только по коду владельца.
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="yandex", revoked_by=f"self:{EVGENIA}") == 1
            relinked = await attempt("ya-self", EVGENIA)
            assert relinked["outcome"] == "linked" and relinked["already_linked"] is False
            assert await service.revoke_identity("whieda", telegram_user_id=EVGENIA, provider="yandex", revoked_by="owner:2") == 1
            by_owner = await attempt("ya-self", EVGENIA)
            assert by_owner["outcome"] == "link_required", "an identity the owner cut off does not come back by itself"

            # Витрина вне семьи cookie: браузер подтверждает ключ сайта, поэтому прямой привязки нет — только код.
            showcase = await attempt("ya-showcase", EVGENIA, host="site.other.test")
            assert showcase["outcome"] == "link_required"
            assert await identity_rows("ya-showcase") == []

        db.run_with_app(proof)
