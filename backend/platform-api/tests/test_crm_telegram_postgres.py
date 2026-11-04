"""Адрес Telegram в карточке WWC CRM против настоящего PostgreSQL (platform_crm_telegram_v50.sql, роль API без bypassrls).

1. V50: колонки и проверка имени, частичные уникальные индексы на живые карточки (регистр, другой аккаунт,
   мягко удалённая карточка), повторный прогон на данных V20 ничего не меняет;
2. API-сервис: создание и правка с «telegram» (сброс ID при другом адресе, смена регистра, очистка), 409 duplicate
   с полем field, 400 invalid_telegram, поиск по имени Telegram, выгрузка CSV, «Вернуть» при занятом адресе;
   гонка двух запросов за один адрес — 409, а не ошибка базы;
3. бот: add_telegram_contacts (новые, повтор по ID / имени / номеру, дописать недостающее, не затирать, не
   брать занятое другой карточкой, весь пакет или ничего) и обработчик выбора людей и контакта на настоящей базе.

Telegram и ссылка входа — подмены: никаких сообщений людям.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED
from tests.test_crm_v2_postgres import _clean_env

V50 = "platform_crm_telegram_v50.sql"
ACC_A = "00000000-0000-4000-8000-0000000000a0"
ACC_B = "00000000-0000-4000-8000-0000000000b0"


def _card(n: int) -> str:
    return f"00000000-0000-4000-8000-0000000001{n:02d}"


def _insert(conn, n: int, account: str, *, username: str | None = None, user_id: int | None = None, deleted: bool = False):
    conn.execute(
        "insert into crm_contacts (tenant_id, contact_id, account_id, name, telegram_username, telegram_user_id, deleted_at) "
        "values ('whieda', %s, %s, %s, %s, %s, case when %s then now() end)",
        (_card(n), account, f"Карточка {n}", username, user_id, deleted),
    )


@pytest.mark.integration
def test_v50_columns_check_and_unique_indexes(monkeypatch):
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_tg50") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, names=[name for name in MIGRATIONS if name != V50])
            conn.execute(SEED)
            conn.execute(f"insert into platform_accounts (tenant_id, account_id, telegram_user_id) values "
                         f"('whieda', '{ACC_A}', 7001), ('whieda', '{ACC_B}', 7002)")
            for n in (1, 2):  # cards of the time before V50: no telegram columns yet
                conn.execute("insert into crm_contacts (tenant_id, contact_id, account_id, name) values ('whieda', %s, %s, %s)",
                             (_card(n), ACC_A, f"Карточка {n}"))
            before = conn.execute("select count(*) from crm_contacts").fetchone()
            sql = (SQL_DIR / V50).read_text(encoding="utf-8")
            assert "$" not in sql  # боевые правки идут через n8n, он режет этот знак
            conn.execute(sql)
            conn.execute(sql)  # второй прогон ничего не ломает
            assert conn.execute("select count(*) from crm_contacts").fetchone() == before
            columns = dict(conn.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_name = 'crm_contacts' and column_name like 'telegram_%'"
            ).fetchall())
            assert columns == {"telegram_username": "text", "telegram_user_id": "bigint"}
            assert conn.execute(
                "select count(*) from crm_contacts where telegram_username is not null or telegram_user_id is not null"
            ).fetchone() == (0,)  # old cards: both empty

            # проверка имени: сначала буква, дальше латиница, цифры, «_», всего 4–32 знака
            for good in ("abcd", "Tanya_K", "a" * 32, "a1_b"):
                conn.execute("update crm_contacts set telegram_username = %s where contact_id = %s", (good, _card(1)))
            for bad in ("abc", "a" * 33, "_abc", "1234", "89001234567", "tanya k", "таня_к", "tanya-k", "@tanya", "tanya\n", ""):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute("update crm_contacts set telegram_username = %s where contact_id = %s", (bad, _card(1)))
            conn.execute("update crm_contacts set telegram_username = null where contact_id = %s", (_card(1),))

            # одна живая карточка на имя (без учёта регистра) и на ID в аккаунте
            conn.execute("update crm_contacts set telegram_username = 'Tanya_K', telegram_user_id = 111 where contact_id = %s",
                         (_card(1),))
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("update crm_contacts set telegram_username = 'tanya_k' where contact_id = %s", (_card(2),))
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("update crm_contacts set telegram_user_id = 111 where contact_id = %s", (_card(2),))
            # пустые не конфликтуют: сколько угодно карточек без адреса и без ID
            _insert(conn, 3, ACC_A)
            _insert(conn, 4, ACC_A)
            # другой аккаунт — другой человек, тот же адрес можно
            _insert(conn, 5, ACC_B, username="TANYA_K", user_id=111)
            # мягко удалённая карточка адрес не держит, а «Вернуть» его проверяет код (см. ниже)
            conn.execute("update crm_contacts set deleted_at = now() where contact_id = %s", (_card(1),))
            conn.execute("update crm_contacts set telegram_username = 'tanya_k', telegram_user_id = 111 where contact_id = %s",
                         (_card(2),))
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("update crm_contacts set deleted_at = null where contact_id = %s", (_card(1),))
            indexes = {row[0] for row in conn.execute(
                "select indexname from pg_indexes where tablename = 'crm_contacts' and indexname like 'crm_contacts_telegram%'"
            ).fetchall()}
            assert indexes == {"crm_contacts_telegram_username_live", "crm_contacts_telegram_user_id_live"}


def _setup(db) -> None:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        db.apply_migrations(conn, twice=True)
        conn.execute(LEAD_TABLES)
        conn.execute(SEED)
        db.grant_api_role(conn)


def _admin(db, statement: str, params: tuple = ()) -> None:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        conn.execute(statement, params)


def _once_blind(module, name: str):
    """A check that misses the first time: the state of a request that read the table just before another one
    wrote — so the unique index, not the check, has to refuse the second write."""
    real = getattr(module, name)
    state = {"calls": 0}

    async def blind(*args, **kwargs):
        state["calls"] += 1
        return None if state["calls"] == 1 else await real(*args, **kwargs)

    return blind


@pytest.mark.integration
def test_api_service_telegram_address(monkeypatch):
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_tgapi") as db:
        _setup(db)

        async def proof() -> None:
            from app.crm import service as crm
            from app.crm.queries import list_contacts

            igor = await crm.get_or_create_account("whieda", 7001)
            petr = await crm.get_or_create_account("whieda", 7002)

            async def error_of(coro) -> crm.CrmError:
                with pytest.raises(crm.CrmError) as caught:
                    await coro
                return caught.value

            # ---- create: every form of the address, the name as sent ----------------------------
            tanya = await crm.create_contact("whieda", igor, name="Таня", telegram="https://t.me/Tanya_K/?start=x")
            assert (tanya["telegram_username"], tanya["telegram_user_id"]) == ("Tanya_K", None)
            assert tanya["phone"] == ""  # no number is needed
            assert (tanya["status"], tanya["next_step"]) == ("new", "invite")
            plain = await crm.create_contact("whieda", igor, name="Без адреса")
            assert (plain["telegram_username"], plain["telegram_user_id"]) == (None, None)
            for empty in ("", "   ", None):
                card = await crm.create_contact("whieda", igor, name=f"Пусто {empty!r}", telegram=empty)
                assert card["telegram_username"] is None

            # ---- 400 and 409 ----------------------------------------------------------------------
            for bad in ("t.me/joinchat/AAAA", "t.me/+79001234567", "t.me/tanya_k/123", "t.me/share", "abc", "привет"):
                err = await error_of(crm.create_contact("whieda", igor, name="Плохой", telegram=bad))
                assert (err.status, err.code) == (400, "invalid_telegram"), bad
            for again in ("tanya_k", "@TANYA_K", "tg://resolve?domain=Tanya_K", "https://telegram.me/tanya_k"):
                err = await error_of(crm.create_contact("whieda", igor, name="Ещё Таня", telegram=again))
                assert (err.status, err.code, err.extra) == (409, "duplicate", {"contact_id": tanya["id"], "field": "telegram"})
            # the same address in another account is another person's card
            assert (await crm.create_contact("whieda", petr, name="Таня у Пети", telegram="tanya_k"))["telegram_username"] == "tanya_k"
            anna = await crm.create_contact("whieda", igor, name="Анна", phone="8 928 672-92-88", telegram="anna_p")
            err = await error_of(crm.create_contact("whieda", igor, name="Анна 2", phone="+7 (928) 672-92-88", telegram="other_n"))
            assert (err.code, err.extra) == ("duplicate", {"contact_id": anna["id"], "field": "phone"})
            err = await error_of(crm.create_contact("whieda", igor, name="Анна 3", phone="+7 999 000-00-00", telegram="anna_p"))
            assert (err.code, err.extra) == ("duplicate", {"contact_id": anna["id"], "field": "telegram"})
            assert [c["name"] for c in (await list_contacts("whieda", igor, q="Анна"))["items"]] == ["Анна"]

            # ---- the reading side answers both fields --------------------------------------------
            one = await crm.get_contact("whieda", igor, tanya["id"])
            assert (one["telegram_username"], one["telegram_user_id"]) == ("Tanya_K", None)
            listed = {c["name"]: c for c in (await list_contacts("whieda", igor))["items"]}
            assert listed["Таня"]["telegram_username"] == "Tanya_K" and listed["Без адреса"]["telegram_username"] is None

            # ---- update: set, same name in another case, another name, clear -----------------------
            _admin(db, "update crm_contacts set telegram_user_id = 555 where contact_id = %s", (tanya["id"],))  # what the bot writes
            card = await crm.update_contact("whieda", igor, tanya["id"], {"telegram": "TANYA_K"})
            assert (card["telegram_username"], card["telegram_user_id"]) == ("TANYA_K", 555)  # the case changes, the person stays
            card = await crm.update_contact("whieda", igor, tanya["id"], {"name": "Таня К"})
            assert (card["telegram_username"], card["telegram_user_id"]) == ("TANYA_K", 555)  # not sent — not touched
            card = await crm.update_contact("whieda", igor, tanya["id"], {"phone": "8 900 111-22-33"})
            assert (card["telegram_username"], card["telegram_user_id"]) == ("TANYA_K", 555)
            card = await crm.update_contact("whieda", igor, tanya["id"], {"telegram": "https://t.me/tatiana_new"})
            assert (card["telegram_username"], card["telegram_user_id"]) == ("tatiana_new", None)  # maybe another person: id dropped
            _admin(db, "update crm_contacts set telegram_user_id = 556 where contact_id = %s", (tanya["id"],))
            for cleared in ("", None):
                _admin(db, "update crm_contacts set telegram_username = 'tatiana_new', telegram_user_id = 556 where contact_id = %s",
                       (tanya["id"],))
                card = await crm.update_contact("whieda", igor, tanya["id"], {"telegram": cleared})
                assert (card["telegram_username"], card["telegram_user_id"]) == (None, None), repr(cleared)
            # an id-only card: a name arrives — the id goes (another person), the name is set
            _admin(db, "update crm_contacts set telegram_user_id = 777 where contact_id = %s", (plain["id"],))
            card = await crm.update_contact("whieda", igor, plain["id"], {"telegram": "@only_name"})
            assert (card["telegram_username"], card["telegram_user_id"]) == ("only_name", None)
            # …and «clear» on an id-only card clears the id too (the contract: both fields)
            _admin(db, "update crm_contacts set telegram_username = null, telegram_user_id = 778 where contact_id = %s", (plain["id"],))
            card = await crm.update_contact("whieda", igor, plain["id"], {"telegram": None})
            assert (card["telegram_username"], card["telegram_user_id"]) == (None, None)
            card = await crm.update_contact("whieda", igor, plain["id"], {"telegram": "only_name"})
            assert card["telegram_username"] == "only_name"

            err = await error_of(crm.update_contact("whieda", igor, plain["id"], {"telegram": "t.me/joinchat/AAAA"}))
            assert (err.status, err.code) == (400, "invalid_telegram")
            err = await error_of(crm.update_contact("whieda", igor, plain["id"], {"telegram": "anna_p"}))
            assert (err.status, err.code, err.extra) == (409, "duplicate", {"contact_id": anna["id"], "field": "telegram"})
            err = await error_of(crm.update_contact("whieda", igor, plain["id"], {"phone": "8 928 672-92-88"}))
            assert (err.code, err.extra) == ("duplicate", {"contact_id": anna["id"], "field": "phone"})
            same = await crm.update_contact("whieda", igor, anna["id"], {"telegram": "@Anna_P"})  # a card is not its own twin
            assert same["telegram_username"] == "Anna_P"

            # ---- search finds the Telegram name -----------------------------------------------------
            async def found(q: str) -> list[str]:
                return sorted(c["name"] for c in (await list_contacts("whieda", igor, q=q))["items"])

            assert await found("anna_p") == ["Анна"]
            assert await found("@anna_p") == ["Анна"]
            assert await found("t.me/anna_p") == ["Анна"]
            assert await found("https://t.me/ANNA_") == ["Анна"]
            assert await found("nna_") == ["Анна"]
            assert await found("only_name") == ["Без адреса"]
            assert await found("@") == []  # «@» alone is not «everyone with an address»
            assert await found("anna%p") == [] and await found("anna_p") == ["Анна"]  # % and _ are letters
            assert await found("annaXp") == []  # «_» is not a wildcard

            # ---- the export has the column at the end ------------------------------------------------
            csv_text = await crm.export_csv("whieda", igor)
            lines = csv_text.split("\r\n")
            assert lines[0].endswith(";Заметки;Telegram")
            assert any(line.startswith("Анна;+79286729288;") and line.endswith(";t.me/Anna_P") for line in lines)
            assert any(line.startswith("Без адреса;") and line.endswith(";t.me/only_name") for line in lines)
            assert any(line.startswith("Таня К;") and line.endswith(";") for line in lines)  # no address — an empty cell

            # ---- «Вернуть»: the address was taken meanwhile -----------------------------------------------
            await crm.delete_contact("whieda", igor, anna["id"])
            thief = await crm.create_contact("whieda", igor, name="Другая Анна", telegram="ANNA_P")
            err = await error_of(crm.restore_contact("whieda", igor, anna["id"]))
            assert (err.status, err.code, err.extra) == (409, "duplicate", {"contact_id": thief["id"], "field": "telegram"})
            await crm.delete_contact("whieda", igor, thief["id"])
            back = await crm.restore_contact("whieda", igor, anna["id"])
            assert back["telegram_username"] == "Anna_P"
            # the id counts too (set by the bot)
            await crm.delete_contact("whieda", igor, back["id"])
            _admin(db, "update crm_contacts set telegram_user_id = 9001 where contact_id = %s", (back["id"],))
            holder = await crm.create_contact("whieda", igor, name="Держит ID")
            _admin(db, "update crm_contacts set telegram_user_id = 9001 where contact_id = %s", (holder["id"],))
            err = await error_of(crm.restore_contact("whieda", igor, back["id"]))
            assert (err.code, err.extra) == ("duplicate", {"contact_id": holder["id"], "field": "telegram"})

            # ---- a race: the check misses, the unique index refuses — a 409, not a database error ---------
            real_match = crm._duplicate_match
            with patch.object(crm, "_duplicate_match", _once_blind(crm, "_duplicate_match")):
                err = await error_of(crm.create_contact("whieda", igor, name="Гонка", telegram="only_name"))
            assert (err.status, err.code, err.extra["field"]) == (409, "duplicate", "telegram")
            assert err.extra["contact_id"] == plain["id"]
            other = await crm.create_contact("whieda", igor, name="Гонка 2", telegram="race_two")
            with patch.object(crm, "_duplicate_match", _once_blind(crm, "_duplicate_match")):
                err = await error_of(crm.update_contact("whieda", igor, other["id"], {"telegram": "only_name"}))
            assert (err.status, err.code, err.extra) == (409, "duplicate", {"contact_id": plain["id"], "field": "telegram"})
            assert (await crm.get_contact("whieda", igor, other["id"]))["telegram_username"] == "race_two"  # nothing half-written
            await crm.delete_contact("whieda", igor, plain["id"])
            squatter = await crm.create_contact("whieda", igor, name="Занял", telegram="only_name")
            # restore: the check passes (blind), the index refuses
            with patch.object(crm, "_duplicate_match", _once_blind(crm, "_duplicate_match")):
                err = await error_of(crm.restore_contact("whieda", igor, plain["id"]))
            assert (err.code, err.extra) == ("duplicate", {"contact_id": squatter["id"], "field": "telegram"})
            assert crm._duplicate_match is real_match

            # two requests at once for one new address: exactly one card, the other gets the 409
            results = await asyncio.gather(
                crm.create_contact("whieda", igor, name="Один", telegram="at_once"),
                crm.create_contact("whieda", igor, name="Два", telegram="AT_ONCE"),
                return_exceptions=True,
            )
            assert sum(isinstance(r, dict) for r in results) == 1
            loser = next(r for r in results if not isinstance(r, dict))
            assert isinstance(loser, crm.CrmError) and (loser.status, loser.code) == (409, "duplicate")
            assert await found("at_once") in (["Один"], ["Два"])

        db.run_with_app(proof)


@pytest.mark.integration
def test_bot_adds_people_from_telegram(monkeypatch):
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_tgbot") as db:
        _setup(db)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection

            igor = await crm.get_or_create_account("whieda", 7001)
            petr = await crm.get_or_create_account("whieda", 7002)

            async def rows(account: dict, **where) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    result = await fetch_all(
                        conn,
                        "select name, telegram_username, telegram_user_id, phone_e164, phone_raw, source, status, next_step, "
                        "next_at, deleted_at from crm_contacts where account_id = %s::uuid and deleted_at is null order by name",
                        (account["account_id"],),
                    )
                return [dict(r) for r in result]

            async def add(account, *people, **kwargs):
                return await crm.add_telegram_contacts("whieda", account, list(people), **kwargs)

            tanya = {"name": "Татьяна Козлова", "telegram_username": "tanya_k", "telegram_user_id": 111}
            ivan = {"name": "Иван", "telegram_username": None, "telegram_user_id": 222}

            # ---- new people: card, first step, history -------------------------------------------
            first = await add(igor, tanya, ivan)
            assert [(r["status"], r["updated"]) for r in first] == [("created", False), ("created", False)]
            assert [r["card"]["name"] for r in first] == ["Татьяна Козлова", "Иван"]
            cards = await rows(igor)
            assert [(c["name"], c["telegram_username"], c["telegram_user_id"], c["phone_e164"]) for c in cards] == [
                ("Иван", None, 222, None), ("Татьяна Козлова", "tanya_k", 111, None)]
            assert {(c["source"], c["status"], c["next_step"], c["next_at"]) for c in cards} == {
                ("Telegram", "new", "invite", igor["today"])}
            async with tenant_connection("whieda") as conn:
                history = await fetch_all(conn, "select kind, payload from crm_activities where contact_id = %s::uuid",
                                          (first[0]["card"]["contact_id"],))
            assert [(h["kind"], h["payload"]) for h in history] == [("created", {"source": "telegram"})]

            # ---- the same people again: no new cards, nothing to add -----------------------------------
            again = await add(igor, tanya, ivan)
            assert [(r["status"], r["updated"]) for r in again] == [("exists", False), ("exists", False)]
            assert len(await rows(igor)) == 2
            # …by the name in any case, when the id was not known yet
            await crm.create_contact("whieda", igor, name="Лена", telegram="Lena_Sm")
            lena = (await add(igor, {"name": "Елена", "telegram_username": "lena_sm", "telegram_user_id": 333}))[0]
            assert (lena["status"], lena["updated"], lena["card"]["name"]) == ("exists", True, "Лена")  # the partner's name stays
            assert (lena["card"]["telegram_user_id"], lena["card"]["telegram_username"]) == (333, "Lena_Sm")  # id added, name as it was
            # …by the id, when the card has no address yet: the address is added, never over a value
            ivan_card = (await add(igor, {"name": "Иван П", "telegram_username": "ivan_p", "telegram_user_id": 222}))[0]
            assert (ivan_card["status"], ivan_card["updated"], ivan_card["card"]["name"]) == ("exists", True, "Иван")
            assert ivan_card["card"]["telegram_username"] == "ivan_p"
            kept_name = (await add(igor, {"name": "Иван", "telegram_username": "ivan_new", "telegram_user_id": 222}))[0]
            assert (kept_name["status"], kept_name["updated"], kept_name["card"]["telegram_username"]) == ("exists", False, "ivan_p")

            # ---- by number: the card gets the id (a forwarded contact) and an old number is not replaced --------
            nina = await crm.create_contact("whieda", igor, name="Нина", phone="8 900 000-11-22")
            by_phone = (await add(igor, {"name": "Нина К", "phone": "+79000001122", "telegram_user_id": 600}))[0]
            assert (by_phone["status"], by_phone["updated"], by_phone["card"]["contact_id"]) == ("exists", True, nina["id"])
            assert by_phone["card"]["telegram_user_id"] == 600 and by_phone["card"]["phone_raw"] == "8 900 000-11-22"
            again_phone = (await add(igor, {"name": "Нина", "phone": "+79000001122", "telegram_user_id": 600}))[0]
            assert (again_phone["status"], again_phone["updated"]) == ("exists", False)
            # a card that has an id but no number gets the number
            only_id = (await add(igor, {"name": "Только ID", "telegram_user_id": 700}))[0]
            assert only_id["status"] == "created" and only_id["card"]["name"] == "Только ID"
            got_number = (await add(igor, {"name": "Только ID", "phone": "79001112233", "telegram_user_id": 700}))[0]
            assert (got_number["status"], got_number["updated"]) == ("exists", True)
            assert got_number["card"]["phone_e164"] == "+79001112233"
            # a card whose number is free text keeps it: the forwarded number does not replace what the partner wrote
            await crm.create_contact("whieda", igor, name="Позвонить вечером", phone="позвонить вечером")
            _admin(db, "update crm_contacts set telegram_user_id = 800 where name = 'Позвонить вечером'")
            kept = (await add(igor, {"name": "Позвонить вечером", "phone": "+79005556677", "telegram_user_id": 800}))[0]
            assert (kept["status"], kept["updated"], kept["card"]["phone_raw"], kept["card"]["phone_e164"]) == (
                "exists", False, "позвонить вечером", None)

            # ---- a value another card holds is not taken ----------------------------------------------------
            await add(igor, {"name": "Карточка А", "telegram_user_id": 900})
            await add(igor, {"name": "Карточка Б", "telegram_username": "card_b_name"})
            clash = (await add(igor, {"name": "Человек", "telegram_user_id": 900, "telegram_username": "card_b_name"}))[0]
            assert clash["status"] == "exists" and clash["card"]["name"] == "Карточка А"  # the id wins
            assert (clash["updated"], clash["card"]["telegram_username"]) == (False, None)  # the name is card Б's
            b = next(c for c in await rows(igor) if c["name"] == "Карточка Б")
            assert (b["telegram_username"], b["telegram_user_id"]) == ("card_b_name", None)

            # ---- two accounts are two worlds; a deleted card is not a match ----------------------------------------
            for_petr = (await add(petr, tanya))[0]
            assert for_petr["status"] == "created"
            assert len(await rows(petr)) == 1
            await crm.delete_contact("whieda", igor, first[0]["card"]["contact_id"])
            revived = (await add(igor, tanya))[0]
            assert revived["status"] == "created" and revived["card"]["contact_id"] != first[0]["card"]["contact_id"]

            # ---- the same person twice in one batch, an empty name, a name from Telegram's address -------------------
            batch = await add(igor, {"name": "Двойной", "telegram_user_id": 1001}, {"name": "Двойной", "telegram_user_id": 1001},
                              {"name": "", "telegram_user_id": 1002}, {"name": "   ", "telegram_username": "nameless_x"})
            assert [r["status"] for r in batch] == ["created", "exists", "created", "created"]
            assert [r["card"]["name"] for r in batch[2:]] == ["Контакт Telegram", "Контакт Telegram"]
            assert len(await add(igor)) == 0  # nobody — nothing

            # ---- all or nothing -------------------------------------------------------------------------------------
            before = len(await rows(igor))
            with pytest.raises(psycopg.errors.DataError):
                await add(igor, {"name": "Первый", "telegram_user_id": 2001}, {"name": "Второй", "telegram_user_id": 2 ** 70})
            assert len(await rows(igor)) == before  # the first one was not left behind

            # ---- a request that lost the race finds the winner instead of failing -----------------------------------------
            await add(igor, {"name": "Победитель", "telegram_user_id": 3001, "telegram_username": "winner_w"})
            with patch.object(crm, "_find_telegram_card", _once_blind(crm, "_find_telegram_card")):
                racer = (await add(igor, {"name": "Гонщик", "telegram_user_id": 3001, "telegram_username": "winner_w"}))[0]
            assert (racer["status"], racer["card"]["name"]) == ("exists", "Победитель")
            assert len([c for c in await rows(igor) if c["telegram_user_id"] == 3001]) == 1

            # ---- the CSV and the lists show them -----------------------------------------------------------------------
            csv_text = await crm.export_csv("whieda", igor)
            assert any(line.startswith("Победитель;") and line.endswith(";t.me/winner_w") for line in csv_text.split("\r\n"))

        db.run_with_app(proof)


@pytest.mark.integration
def test_bot_handler_on_a_real_database(monkeypatch):
    """The whole handler on the real service: the picker's answer and a forwarded contact, Telegram replaced."""
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_tgh") as db:
        _setup(db)

        async def proof() -> None:
            from app.crm import service as crm
            from app.crm import telegram_import
            from app.crm.queries import list_contacts
            from app.telegram.update_parser import SharedContact, SharedUser, SharedUsers, TelegramMessage
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                   entitlements={"structure_basic": True, "crm": True})
            send = AsyncMock(return_value={"ok": True})
            login = AsyncMock(return_value="https://igor.wwc.best/crm/#wwc-login=t")
            sent = lambda: [(c.kwargs["text"], c.kwargs.get("reply_markup")) for c in send.await_args_list]  # noqa: E731

            def message(user_id: int = 7001, **extra) -> TelegramMessage:
                return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text="", chat_type="private",
                                       file_id=None, raw={}, **extra)

            picked = message(users_shared=SharedUsers(request_id=telegram_import.REQUEST_ID, users=(
                SharedUser(user_id=111, first_name="Татьяна", last_name="Козлова", username="tanya_k"),
                SharedUser(user_id=222, first_name="Иван"),
            )))
            with patch.object(telegram_import, "send_telegram_text", send), patch.object(
                telegram_import, "with_site_login", login
            ), patch.object(telegram_import, "current_bot_binding", return_value=SimpleNamespace(bot_token="t")):
                result = await telegram_import.try_handle_crm_shared(tenant, picked, trace_id="t1")
                assert (result["status"], result["created"], result["existing"]) == ("added", 2, 0)
                assert sent()[0] == ("Добавлено в WWC CRM: 2\n• Татьяна Козлова (@tanya_k)\n• Иван", {"remove_keyboard": True})
                assert sent()[1][0] == "Откройте карточки, чтобы написать людям:"
                assert sent()[1][1]["inline_keyboard"][0][0]["url"] == "https://igor.wwc.best/crm/#wwc-login=t"

                send.reset_mock()
                again = await telegram_import.try_handle_crm_shared(tenant, picked, trace_id="t2")
                assert (again["created"], again["existing"]) == (0, 2)
                assert sent()[0][0] == "Уже были в WWC CRM: Татьяна Козлова, Иван"

                send.reset_mock()
                contact = message(shared_contact=SharedContact(
                    phone_number="79000000000", first_name="Пётр", last_name="", user_id=333))
                await telegram_import.try_handle_crm_shared(tenant, contact, trace_id="t3")
                assert sent()[0][0] == "Добавлено в WWC CRM: Пётр, +7 900 000-00-00"
                send.reset_mock()
                await telegram_import.try_handle_crm_shared(tenant, contact, trace_id="t4")
                assert sent()[0][0] == "Этот человек уже в WWC CRM."
                send.reset_mock()
                # the same person already a card by number (no id): the id is written
                await crm.create_contact("whieda", await crm.get_or_create_account("whieda", 7001), name="Мария",
                                         phone="8 900 123-45-67")
                maria = message(shared_contact=SharedContact(phone_number="79001234567", first_name="Маша", user_id=444))
                await telegram_import.try_handle_crm_shared(tenant, maria, trace_id="t5")
                assert sent()[0][0] == "Этот человек уже в WWC CRM. Карточка обновлена."
                # one's own card shared — not touched
                send.reset_mock()
                own = message(shared_contact=SharedContact(phone_number="79000000009", first_name="Я", user_id=7001))
                assert await telegram_import.try_handle_crm_shared(tenant, own, trace_id="t6") is None
                send.assert_not_awaited()
                # a person whose CRM is closed (no paid site): the forwarded contact is left alone
                stranger = message(user_id=999001, shared_contact=SharedContact(
                    phone_number="79000000010", first_name="Чужой", user_id=5))
                assert await telegram_import.try_handle_crm_shared(tenant, stranger, trace_id="t7") is None

            igor = await crm.get_or_create_account("whieda", 7001)
            items = (await list_contacts("whieda", igor, sort="name"))["items"]
            assert {c["name"]: (c["telegram_username"], c["telegram_user_id"], c["phone_e164"]) for c in items} == {
                "Татьяна Козлова": ("tanya_k", 111, None), "Иван": (None, 222, None),
                "Пётр": (None, 333, "+79000000000"), "Мария": (None, 444, "+79001234567"),
            }
            assert {c["source"] for c in items if c["name"] != "Мария"} == {"Telegram"}

        db.run_with_app(proof)
