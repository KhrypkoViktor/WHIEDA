"""Почта для заявок с сайта (17.10.2026): команды партнёра и владельца в боте, поле lead_email в /me; база — подделка.

Настоящий SQL (jsonb_set, RLS, партнёр без telegram_user_id, общий поиск «кода входа») —
tests/test_lead_email_postgres.py.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.lead_email import bot, service
from app.login_identities import service as login_service
from app.settings import get_settings
from app.telegram import processor
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramMessage
from tests.fake_db import FakeDb as TxCountingDb

HOST = {"host": "wwc.best"}
OWNER = 2
VOLKOVA, PETROVA, PLAIN = 700, 701, 703
TENANT = SimpleNamespace(tenant_id="whieda")


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.delenv("PLATFORM_SUPER_ADMIN_TELEGRAM_IDS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _msg(text: str, user_id: int = VOLKOVA, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={})


# ---- разбор команд ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("почта", ("show", "", "")),
    ("Почта", ("show", "", "")),
    ("почта для заявок", ("show", "", "")),
    ("/leademail", ("show", "", "")),
    ("/leademail@WWC_Bot", ("show", "", "")),
    ("почта ivan@gmail.com", ("set", "ivan@gmail.com", "")),
    ("ПОЧТА Ivan@Gmail.com", ("set", "Ivan@Gmail.com", "")),
    ("почта для заявок ivan@gmail.com", ("set", "ivan@gmail.com", "")),
    ("Почта   для  заявок   ivan@gmail.com", ("set", "ivan@gmail.com", "")),
    ("/leademail ivan@gmail.com", ("set", "ivan@gmail.com", "")),
    ("/leademail@WWC_Bot ivan@gmail.com", ("set", "ivan@gmail.com", "")),
    ("почта убрать", ("clear", "", "")),
    ("почта для заявок убрать", ("clear", "", "")),
    ("/leademail убрать", ("clear", "", "")),
    ("почта Удалить", ("clear", "", "")),
    # неверный адрес — подсказка: явная форма всегда наша, голая — если похоже на адрес
    ("почта для заявок ivan", ("set", "ivan", "")),
    ("/leademail что-то там", ("set", "что-то там", "")),
    ("почта ivan@gmail", ("set", "ivan@gmail", "")),
    ("почта gmail.com", ("set", "gmail.com", "")),
])
def test_partner_commands_are_recognised(text, expected):
    command = service.parse_command(text, is_owner=False)
    assert command is not None and (command.action, command.value, command.target) == expected


@pytest.mark.parametrize("text", [
    "привет", "", "почтальон принёс письмо", "почтамт", "моя почта ivan@gmail.com",
    "почта не приходит", "почта пришла вчера вечером", "почта ivan@gmail.com спасибо",
    "почта volkova ivan@gmail.com",       # «<кто> <адрес>» без «для заявок» — не команда ни у кого
    "/leademailx ivan@gmail.com",
])
def test_ordinary_phrases_are_not_commands(text):
    assert service.parse_command(text, is_owner=False) is None
    assert service.parse_command(text, is_owner=True) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("почта для заявок volkova ivan@gmail.com", ("set", "ivan@gmail.com", "volkova")),
    ("Почта для заявок Евгения Волкова Ivan@Gmail.com", ("set", "Ivan@Gmail.com", "Евгения Волкова")),
    ("почта для заявок 688931415 ivan@gmail.com", ("set", "ivan@gmail.com", "688931415")),
    ("/leademail volkova ivan@gmail.com", ("set", "ivan@gmail.com", "volkova")),
    ("почта для заявок volkova убрать", ("clear", "", "volkova")),
    ("почта для заявок Евгения Волкова убрать", ("clear", "", "Евгения Волкова")),
    ("почта для заявок volkova ivan@", ("set", "ivan@", "volkova")),
])
def test_owner_command_with_a_target_is_recognised_only_for_the_owner(text, expected):
    command = service.parse_command(text, is_owner=True)
    assert command is not None and (command.action, command.value, command.target) == expected
    assert service.parse_command(text, is_owner=False) is None, "для остальных «<кто> <адрес>» — не команда"


@pytest.mark.parametrize("is_owner", [False, True])
def test_two_addresses_are_a_wrong_address_not_a_target(is_owner):
    command = service.parse_command("почта для заявок a@b.c, d@e.f", is_owner=is_owner)
    assert (command.action, command.value, command.target) == ("set", "a@b.c, d@e.f", "")
    assert service.normalize_email(command.value) is None


def test_owner_own_address_is_still_the_partner_form():
    command = service.parse_command("почта для заявок my@gmail.com", is_owner=True)
    assert (command.action, command.value, command.target) == ("set", "my@gmail.com", "")
    command = service.parse_command("почта для заявок убрать", is_owner=True)
    assert (command.action, command.target) == ("clear", "")


@pytest.mark.parametrize(("raw", "expected"), [
    ("ivan@gmail.com", "ivan@gmail.com"),
    ("Ivan.Petrov+wwc@Gmail.COM", "Ivan.Petrov+wwc@gmail.com"),
    ("<ivan@gmail.com>", "ivan@gmail.com"),
    ("«ivan@gmail.com».", "ivan@gmail.com"),
    ("<ivan@gmail.com", "ivan@gmail.com"),
    ("mailto:ivan@mail.ru", "ivan@mail.ru"),
    ("ivan@gmail.com,", "ivan@gmail.com"),
    ("иван@почта.рф", "иван@почта.рф"),
    ("a@b.c", "a@b.c"),
])
def test_valid_addresses_are_normalised(raw, expected):
    assert service.normalize_email(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "ivan", "ivan@", "@gmail.com", "ivan@gmail", "ivan@@gmail.com", "iv an@gmail.com",
    "a@b.c,d@e.f", "a@b.c;d@e.f", "a@b.c d@e.f", "a@b..c", "a@.com", "a@-b.com", "a@b.com-",
    "a(b)@c.com", 'a"b@c.com', "a@b.com\nBcc: x@y.z", "a\\b@c.com",
    "x" * 250 + "@b.co",
])
def test_bad_addresses_are_refused(raw):
    assert service.normalize_email(raw) is None


def test_address_of_exactly_254_characters_passes_and_255_does_not():
    domain = "b" * 60 + ".ru"
    local = "a" * (254 - len(domain) - 1)
    assert len(f"{local}@{domain}") == 254 and service.normalize_email(f"{local}@{domain}") is not None
    assert service.normalize_email(f"a{local}@{domain}") is None


# ---- подделка базы -----------------------------------------------------------------------------------


class FakeDb:
    """lead_actors + referral_profiles. Запросы узнаются по самой константе SQL из service."""

    def __init__(self) -> None:
        self.actors = {
            "volkova": {"telegram_user_id": VOLKOVA, "display_name": "Евгения Волкова"},
            "petrova": {"telegram_user_id": PETROVA, "display_name": "Евгения Петрова"},
            "ghost": {"telegram_user_id": None, "display_name": "Светлана Призрак"},  # из реестра, боту не писала
            "plain": {"telegram_user_id": PLAIN, "display_name": "Без сайта"},
            "viktor": {"telegram_user_id": OWNER, "display_name": "Виктор"},
        }
        self.profiles = {
            "volkova": {"owner_id": "volkova", "enabled": True, "public_profile": {"display_name": "Евгения Волкова", "bio": "О себе", "contacts": {"phone": "+7"}}},
            "volkova2": {"owner_id": "volkova", "enabled": True, "public_profile": {"display_name": "Евгения Волкова"}},
            "petrova": {"owner_id": "petrova", "enabled": True, "public_profile": {"display_name": "Евгения Петрова", "lead_email": "old@x.ru"}},
            "ghost": {"owner_id": "ghost", "enabled": True, "public_profile": {"display_name": "Светлана Призрак"}},
            "viktor": {"owner_id": "viktor", "enabled": True, "public_profile": {"display_name": "Виктор"}},
            "off": {"owner_id": "plain", "enabled": False, "public_profile": {"display_name": "Закрытый"}},
        }
        self.version = {ref: 1 for ref in self.profiles}
        self.writes: list[tuple[str, list[str]]] = []

    def _owned(self, telegram_user_id: int) -> list[str]:
        owners = {a for a, row in self.actors.items() if row["telegram_user_id"] == telegram_user_id}
        return sorted(ref for ref, p in self.profiles.items() if p["owner_id"] in owners and p["enabled"])

    def _row(self, ref: str) -> dict:
        profile = self.profiles[ref]
        actor = self.actors[profile["owner_id"]]
        return {
            "ref_code": ref,
            "lead_email": profile["public_profile"].get("lead_email") or None,
            "display_name": profile["public_profile"].get("display_name") or actor["display_name"],
            "telegram_user_id": actor["telegram_user_id"],
        }

    async def fetch_all(self, conn, query, params=()):  # noqa: ARG002
        if query is service._SITES_BY_TELEGRAM_SQL:
            return [self._row(ref) for ref in self._owned(params[1])]
        if query is service._SITE_BY_REF_SQL:
            ref = params[1]
            return [self._row(ref)] if ref in self.profiles and self.profiles[ref]["enabled"] else []
        if query is service._SITES_BY_NAME_SQL:
            needle = params[1].lower()
            return [self._row(r) for r in sorted(self.profiles) if self.profiles[r]["enabled"] and needle in self._row(r)["display_name"].lower()]
        if query is service._SET_SQL:
            email, _tenant, refs = params
            done = [r for r in refs if r in self.profiles and self.profiles[r]["enabled"]]
            for ref in done:
                self.profiles[ref]["public_profile"]["lead_email"] = email
            self.writes.append(("set", done))
            return [{"ref_code": r} for r in done]
        if query is service._CLEAR_SQL:
            _tenant, refs = params
            done = [r for r in refs if self.profiles.get(r, {}).get("enabled") and "lead_email" in self.profiles[r]["public_profile"]]
            for ref in done:
                del self.profiles[ref]["public_profile"]["lead_email"]
            self.writes.append(("clear", done))
            return [{"ref_code": r} for r in done]
        raise AssertionError(f"unexpected query: {' '.join(query.split())[:80]}")

    async def find_people(self, conn, tenant_id, target):  # noqa: ARG002
        """Как login_identities.service.find_people: только люди с telegram_user_id; ref → id → имя."""
        value = target.strip()
        known = {a: row for a, row in self.actors.items() if row["telegram_user_id"] is not None}

        def person(actor: str, ref: str | None) -> dict:
            return {"ref_code": ref, "telegram_user_id": known[actor]["telegram_user_id"], "display_name": known[actor]["display_name"]}

        ref = value.lower()
        if ref in self.profiles and self.profiles[ref]["owner_id"] in known:
            return [person(self.profiles[ref]["owner_id"], ref)]
        if value.isdigit():
            for actor, row in known.items():
                if row["telegram_user_id"] == int(value):
                    refs = self._owned(int(value))
                    return [person(actor, refs[0] if refs else None)]
        by_name = [person(a, next((r for r, p in self.profiles.items() if p["owner_id"] == a and p["enabled"]), None))
                   for a, row in known.items() if value.lower() in row["display_name"].lower()]
        return sorted(by_name, key=lambda p: p["display_name"])

    def stored(self, ref: str) -> dict:
        return self.profiles[ref]["public_profile"]


@pytest.fixture
def db(owner_env):
    fake = FakeDb()

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    with patch.object(service, "tenant_connection", connection), \
         patch.object(service, "fetch_all", fake.fetch_all), \
         patch.object(login_service, "find_people", fake.find_people):
        yield fake


@pytest.fixture
def sent():
    out: list[str] = []
    with patch.object(bot, "_deliver", AsyncMock(side_effect=lambda chat, text: out.append(text))):
        yield out


# ---- партнёр ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partner_sets_sees_and_clears_the_address(db, sent, caplog):
    caplog.set_level(logging.INFO, logger="whieda.lead_email")
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта"), trace_id="t")
    assert result["status"] == "shown" and sent[-1] == "Почта для заявок не задана. Напишите: почта имя@gmail.com"

    result = await bot.try_handle_lead_email_message(TENANT, _msg("Почта для заявок Ivan@Gmail.COM"), trace_id="t")
    assert result == {"ok": True, "route": "lead_email", "action": "set", "trace_id": "t", "status": "set"}
    assert sent[-1] == "Готово. Заявки с сайта будут приходить и на Ivan@gmail.com. Убрать: «почта убрать»."
    assert db.stored("volkova")["lead_email"] == "Ivan@gmail.com", "домен — в нижнем регистре, имя как набрали"
    assert db.stored("volkova")["bio"] == "О себе" and db.stored("volkova")["contacts"] == {"phone": "+7"}, "остальной профиль цел"
    assert db.stored("volkova2")["lead_email"] == "Ivan@gmail.com", "второй сайт того же человека — тот же адрес"
    assert db.stored("petrova")["lead_email"] == "old@x.ru" and "lead_email" not in db.stored("viktor"), "чужие профили не тронуты"
    assert db.version["volkova"] == 1, "profile_version не растёт: на сайте адрес не виден"

    await bot.try_handle_lead_email_message(TENANT, _msg("/leademail"), trace_id="t")
    assert sent[-1] == "Почта для заявок: Ivan@gmail.com. Заявки приходят и на неё, и в Telegram. Убрать: «почта убрать»."

    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта убрать"), trace_id="t")
    assert result["status"] == "cleared" and sent[-1] == "Почта для заявок убрана, остаётся Telegram."
    assert "lead_email" not in db.stored("volkova") and "lead_email" not in db.stored("volkova2")
    assert db.stored("volkova")["bio"] == "О себе"

    # в логе — кто и где, без самого адреса
    records = {r.getMessage(): r for r in caplog.records if r.name == "whieda.lead_email"}
    assert set(records) == {"lead_email_set", "lead_email_cleared"}
    for event in records.values():
        assert event.telegram_user_id == VOLKOVA and event.tenant_id == "whieda" and event.by == "self"
        assert event.ref_codes == ["volkova", "volkova2"]
    assert "gmail" not in caplog.text and "Ivan" not in caplog.text


@pytest.mark.asyncio
async def test_clear_without_an_address_changes_nothing_and_logs_nothing(db, sent, caplog):
    caplog.set_level(logging.INFO, logger="whieda.lead_email")
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта убрать"), trace_id="t")
    assert result["status"] == "cleared" and sent == ["Почта для заявок убрана, остаётся Telegram."]
    assert db.writes == [("clear", [])] and not [r for r in caplog.records if r.name == "whieda.lead_email"]


@pytest.mark.asyncio
async def test_partner_sees_the_current_address(db, sent):
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта", user_id=PETROVA), trace_id="t")
    assert result["status"] == "shown"
    assert sent == ["Почта для заявок: old@x.ru. Заявки приходят и на неё, и в Telegram. Убрать: «почта убрать»."]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["почта для заявок ivan", "/leademail ivan@gmail", "почта ivan@", "почта для заявок a@b.c, d@e.f", "почта gmail.com"])
async def test_wrong_address_gets_a_hint_and_nothing_is_written(db, sent, text):
    result = await bot.try_handle_lead_email_message(TENANT, _msg(text), trace_id="t")
    assert result["ok"] is False and result["status"] == "invalid_email"
    assert sent == ["Не похоже на адрес почты. Пример: имя@gmail.com"]
    assert db.writes == [] and "lead_email" not in db.stored("volkova")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["почта ivan@gmail.com", "почта", "почта убрать"])
async def test_a_person_without_a_site_is_pointed_to_the_order_button(db, sent, text):
    for user_id in (PLAIN, 999):  # есть в базе без сайта; в базе нет вовсе; сайт у PLAIN выключен (off)
        sent.clear()
        result = await bot.try_handle_lead_email_message(TENANT, _msg(text, user_id=user_id), trace_id="t")
        assert result["status"] == "no_site"
        assert sent == ["Почта для заявок нужна партнёрам с сайтом. Чтобы заказать сайт, откройте /start и нажмите «Заказать сайт WWC»."]
    assert db.writes == []


@pytest.mark.asyncio
async def test_group_chat_is_refused_without_a_reply(db, sent):
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта ivan@gmail.com", chat_type="supergroup"), trace_id="t")
    assert result["status"] == "private_chat_required" and sent == [] and db.writes == []


@pytest.mark.asyncio
async def test_other_words_are_left_to_the_next_handler(db, sent):
    for text in ("привет", "почта не приходит", "код входа volkova", "моя почта ivan@gmail.com"):
        assert await bot.try_handle_lead_email_message(TENANT, _msg(text), trace_id="t") is None, text
    assert sent == [] and db.writes == []


# ---- владелец -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_sets_the_address_by_ref_and_by_name(db, sent, caplog):
    caplog.set_level(logging.INFO, logger="whieda.lead_email")
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок petrova a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "set" and result["telegram_user_id"] == PETROVA
    assert sent[-1] == "Почта для заявок у Евгения Петрова (petrova): a@b.c"
    assert db.stored("petrova")["lead_email"] == "a@b.c"

    await bot.try_handle_lead_email_message(TENANT, _msg("Почта для заявок Евгения Волкова Volkova@Mail.RU", user_id=OWNER), trace_id="t")
    assert sent[-1] == "Почта для заявок у Евгения Волкова (volkova): Volkova@mail.ru"
    assert db.stored("volkova")["lead_email"] == "Volkova@mail.ru" and db.stored("volkova2")["lead_email"] == "Volkova@mail.ru"

    await bot.try_handle_lead_email_message(TENANT, _msg(f"/leademail {VOLKOVA} убрать", user_id=OWNER), trace_id="t")
    assert sent[-1] == "Почта для заявок у Евгения Волкова (volkova) убрана."
    assert "lead_email" not in db.stored("volkova")

    events = [r for r in caplog.records if r.name == "whieda.lead_email"]
    assert [e.getMessage() for e in events] == ["lead_email_set", "lead_email_set", "lead_email_cleared"]
    assert {e.by for e in events} == {"owner"} and events[0].telegram_user_id == PETROVA
    assert "a@b.c" not in caplog.text and "mail.ru" not in caplog.text


@pytest.mark.asyncio
async def test_owner_command_with_a_target_does_not_write_to_the_owner(db, sent):
    """«почта для заявок volkova a@b.c» от владельца — адрес Волковой, а не владельца."""
    await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок volkova a@b.c", user_id=OWNER), trace_id="t")
    assert db.stored("volkova")["lead_email"] == "a@b.c" and "lead_email" not in db.stored("viktor")
    # а без «кто» — владельцу, как любому партнёру
    await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок boss@b.c", user_id=OWNER), trace_id="t")
    assert db.stored("viktor")["lead_email"] == "boss@b.c" and db.stored("volkova")["lead_email"] == "a@b.c"


@pytest.mark.asyncio
async def test_owner_can_set_the_address_of_a_partner_who_never_wrote_to_the_bot(db, sent):
    """Партнёр из реестра: telegram_user_id пуст, общий поиск «кода входа» его не видит — ищем по профилю."""
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок ghost light@x.ru", user_id=OWNER), trace_id="t")
    assert result["status"] == "set" and result["telegram_user_id"] is None
    assert sent[-1] == "Почта для заявок у Светлана Призрак (ghost): light@x.ru"
    assert db.stored("ghost")["lead_email"] == "light@x.ru"

    await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок Призрак убрать", user_id=OWNER), trace_id="t")
    assert sent[-1] == "Почта для заявок у Светлана Призрак (ghost) убрана." and "lead_email" not in db.stored("ghost")


@pytest.mark.asyncio
async def test_owner_gets_an_explanation_for_unknown_ambiguous_and_siteless_targets(db, sent):
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок zzz a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "partner_not_found" and sent[-1] == "Не нашёл: zzz. Укажите ref, id Telegram или имя как на сайте."

    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок Евгения a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "partner_ambiguous"
    assert sent[-1].startswith("Нашлось несколько") and "Евгения Волкова (volkova) · id 700" in sent[-1] and "Евгения Петрова (petrova) · id 701" in sent[-1]

    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок plain a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "partner_not_found", "plain — не ref, а actor_id: по ref и имени «plain» никого нет"
    result = await bot.try_handle_lead_email_message(TENANT, _msg(f"почта для заявок {PLAIN} a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "no_site" and sent[-1] == "У Без сайта нет сайта WWC — почту для заявок задать нельзя."
    assert db.writes == []


@pytest.mark.asyncio
async def test_owner_ambiguity_among_partners_without_telegram_lists_refs_without_ids(db, sent):
    db.actors["ghost2"] = {"telegram_user_id": None, "display_name": "Светлана Вторая"}
    db.profiles["ghost2"] = {"owner_id": "ghost2", "enabled": True, "public_profile": {"display_name": "Светлана Вторая"}}
    db.version["ghost2"] = 1
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок Светлана a@b.c", user_id=OWNER), trace_id="t")
    assert result["status"] == "partner_ambiguous"
    assert "• Светлана Призрак (ghost)" in sent[-1] and "• Светлана Вторая (ghost2)" in sent[-1] and "id None" not in sent[-1]


@pytest.mark.asyncio
async def test_owner_form_with_a_wrong_address_does_not_even_look_the_partner_up(db, sent):
    with patch.object(service, "resolve_owner_target", AsyncMock()) as lookup:
        result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок volkova ivan@", user_id=OWNER), trace_id="t")
    assert result["status"] == "invalid_email" and sent == ["Не похоже на адрес почты. Пример: имя@gmail.com"]
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_non_owner_with_a_target_is_not_a_command_and_gets_no_reply(db, sent):
    for text in ("почта для заявок petrova a@b.c", "почта для заявок Евгения Петрова убрать", "/leademail petrova a@b.c"):
        assert await bot.try_handle_lead_email_message(TENANT, _msg(text, user_id=VOLKOVA), trace_id="t") is None, text
    assert sent == [] and db.writes == [] and db.stored("petrova")["lead_email"] == "old@x.ru"


@pytest.mark.asyncio
async def test_owner_command_in_a_group_chat_is_refused_quietly(db, sent):
    result = await bot.try_handle_lead_email_message(TENANT, _msg("почта для заявок volkova a@b.c", user_id=OWNER, chat_type="group"), trace_id="t")
    assert result["status"] == "private_chat_required" and sent == [] and db.writes == []


# ---- через процессор ----------------------------------------------------------------------------------

_EARLIER_HANDLERS = (
    "try_handle_wish_message", "try_handle_profile_reject_reason", "try_handle_shop_message", "try_handle_support_message",
    "try_handle_renewal_message", "try_handle_site_request_message", "try_start_renewal_by_text",
    "try_handle_billing_message", "try_handle_referral_admin_message", "try_handle_login_identity_message",
)


def _update(text: str, user_id: int) -> dict:
    return {"message": {"message_id": 5, "text": text, "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id}}}


def _through_processor(stack: ExitStack):
    stack.enter_context(patch("app.telegram.processor._link_partner_chat", AsyncMock()))
    for name in _EARLIER_HANDLERS:
        stack.enter_context(patch(f"app.telegram.processor.{name}", AsyncMock(return_value=None)))
    return stack.enter_context(
        patch("app.telegram.processor.try_handle_profile_requests_command", AsyncMock(return_value={"ok": True, "route": "next_handler"}))
    )


@pytest.mark.asyncio
async def test_partner_command_is_answered_by_our_handler_through_the_processor(whieda_tenant, whieda_bot_binding, db, sent):
    with ExitStack() as stack:
        next_handler = _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update("почта ivan@gmail.com", VOLKOVA), "t1", binding=whieda_bot_binding)
    assert result["route"] == "lead_email" and result["status"] == "set"
    next_handler.assert_not_awaited()
    assert sent == ["Готово. Заявки с сайта будут приходить и на ivan@gmail.com. Убрать: «почта убрать»."]
    assert db.stored("volkova")["lead_email"] == "ivan@gmail.com"


@pytest.mark.asyncio
async def test_owner_command_goes_through_the_processor_to_the_partner_not_the_owner(whieda_tenant, whieda_bot_binding, db, sent):
    with ExitStack() as stack:
        _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update("почта для заявок petrova new@x.ru", OWNER), "t1", binding=whieda_bot_binding)
    assert result["route"] == "lead_email" and db.stored("petrova")["lead_email"] == "new@x.ru"
    assert "lead_email" not in db.stored("viktor") and sent == ["Почта для заявок у Евгения Петрова (petrova): new@x.ru"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "user_id"), [
    ("почта для заявок petrova a@b.c", VOLKOVA),   # не-владелец с «кто» — не команда
    ("почта не приходит", VOLKOVA),                # обычная фраза — дальше по цепочке
])
async def test_what_is_not_our_command_falls_through_to_the_next_handler(whieda_tenant, whieda_bot_binding, db, sent, text, user_id):
    with ExitStack() as stack:
        next_handler = _through_processor(stack)
        result = await process_core_telegram_update(whieda_tenant, _update(text, user_id), "t1", binding=whieda_bot_binding)
    assert result["route"] == "next_handler"
    next_handler.assert_awaited_once()
    assert sent == [] and db.writes == []


def test_processor_routes_to_the_lead_email_handler_after_the_login_one():
    assert processor.try_handle_lead_email_message is bot.try_handle_lead_email_message
    source = open(processor.__file__, encoding="utf-8").read()
    assert source.index("try_handle_login_identity_message(tenant, msg") < source.index("try_handle_lead_email_message(tenant, msg")
    assert source.index("try_handle_support_message(tenant, msg") < source.index("try_handle_lead_email_message(tenant, msg"), "диалоги поддержки и заявок — раньше"
    assert source.index("try_handle_site_request_message(tenant, msg") < source.index("try_handle_lead_email_message(tenant, msg")


# ---- /api/v1/content-access/me ----------------------------------------------------------------------

SESSION = {
    "session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
    "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": VOLKOVA,
}


def _me_patches(stack: ExitStack, *, session: dict, lead_email) -> AsyncMock:
    stack.enter_context(patch("app.content_access.routes.tenant_connection", TxCountingDb().tenant_connection))
    stack.enter_context(patch("app.content_access.routes.read_session_cookie", return_value="raw"))
    stack.enter_context(patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=session)))
    stack.enter_context(patch("app.content_access.routes.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value=None)))
    stack.enter_context(patch("app.content_access.routes.load_site_account", AsyncMock(return_value=None)))
    stack.enter_context(patch("app.content_access.routes.list_login_methods", AsyncMock(return_value=[])))
    return stack.enter_context(patch("app.content_access.routes.lead_email_of", lead_email))


@pytest.mark.asyncio
async def test_me_returns_the_lead_email_next_to_login_methods(client):
    with ExitStack() as stack:
        lookup = _me_patches(stack, session=SESSION, lead_email=AsyncMock(return_value="ivan@gmail.com"))
        response = await client.get("/api/v1/content-access/me", headers=HOST)
    body = response.json()
    assert response.status_code == 200 and body["lead_email"] == "ivan@gmail.com" and "login_methods" in body
    lookup.assert_awaited_once()
    assert lookup.await_args.args == ("whieda", VOLKOVA)  # и открытая «/me» транзакция в conn


@pytest.mark.asyncio
async def test_me_gives_null_when_no_address_is_set_or_the_lookup_fails_or_there_is_no_telegram_id(client):
    with ExitStack() as stack:
        _me_patches(stack, session=SESSION, lead_email=AsyncMock(return_value=None))
        assert (await client.get("/api/v1/content-access/me", headers=HOST)).json()["lead_email"] is None
    with ExitStack() as stack:
        _me_patches(stack, session=SESSION, lead_email=AsyncMock(side_effect=RuntimeError("no pool")))
        response = await client.get("/api/v1/content-access/me", headers=HOST)
        assert response.status_code == 200 and response.json()["lead_email"] is None, "кабинет не ломается из-за поля"
    with ExitStack() as stack:
        lookup = _me_patches(stack, session={**SESSION, "telegram_user_id": None}, lead_email=AsyncMock(return_value="x@y.z"))
        response = await client.get("/api/v1/content-access/me", headers=HOST)
        assert response.json()["lead_email"] is None
        lookup.assert_not_awaited()
