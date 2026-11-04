"""«Добавить из Telegram» в боте WWC CRM (04.11.2026): вход, отмена, выбор людей, пересланный контакт.

База и Telegram подменены (настоящий SQL — в tests/test_crm_telegram_postgres.py). Последний раздел
проводит обновления через процессор целиком, с тремя меню бота и без них.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.crm import bot, telegram_import
from app.crm.rules import CrmViewer
from app.settings import get_settings
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import (
    SharedContact,
    SharedUser,
    SharedUsers,
    TelegramCallbackQuery,
    TelegramMessage,
    parse_shared_message,
)
from app.tenancy import TenantContext
from tests.test_bot_media import _through_processor, _update

WITH_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                         entitlements={"structure_basic": True, "crm": True})
WITHOUT_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                            entitlements={"structure_basic": True})
PAID = CrmViewer(telegram_user_id=5, is_preview_admin=False, partner_paid=True, ref_code="igor",
                 public_profile={"subdomain": "igor"})
UNPAID = CrmViewer(telegram_user_id=5, is_preview_admin=False, partner_paid=False, ref_code="igor")
ACCOUNT = {"account_id": "9f0c3c1e-0000-4000-8000-000000000001", "telegram_user_id": 5, "timezone": "Europe/Moscow"}
CRM_URL = "https://igor.wwc.best/crm/#wwc-login=t"
OPEN_MARKUP = {"inline_keyboard": [[{"text": "📒 Открыть WWC CRM", "url": CRM_URL}]]}
REMOVE = {"remove_keyboard": True}


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class World:
    """Everything the handler reaches for: the viewer, the account, the service, the Telegram sends."""

    def __init__(self) -> None:
        self.viewer = AsyncMock(return_value=PAID)
        self.account = AsyncMock(return_value=ACCOUNT)
        self.add = AsyncMock(return_value=[])
        self.send = AsyncMock(return_value={"ok": True})
        self.answer = AsyncMock(return_value={"ok": True})
        self.login = AsyncMock(return_value=CRM_URL)

    @property
    def sent(self) -> list[tuple[str, dict | None]]:
        return [(call.kwargs["text"], call.kwargs.get("reply_markup")) for call in self.send.await_args_list]

    def install(self, stack: ExitStack) -> "World":
        binding = SimpleNamespace(bot_token="token")
        for name, value in (
            ("load_viewer", self.viewer), ("get_or_create_account", self.account), ("add_telegram_contacts", self.add),
            ("send_telegram_text", self.send), ("answer_callback_query", self.answer), ("with_site_login", self.login),
        ):
            stack.enter_context(patch.object(telegram_import, name, value))
        stack.enter_context(patch.object(telegram_import, "current_bot_binding", return_value=binding))
        return self


@pytest.fixture
def world():
    with ExitStack() as stack:
        yield World().install(stack)


def _msg(text: str = "", *, chat_type: str = "private", user_id: int = 5, **extra) -> TelegramMessage:
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type=chat_type,
                           file_id=None, raw={}, **extra)


def _card(name: str, *, username: str | None = None, phone: str | None = None, user_id: int | None = None) -> dict:
    return {"contact_id": f"id-{name}", "name": name, "telegram_username": username, "telegram_user_id": user_id,
            "phone_e164": phone, "phone_raw": phone}


def created(name: str, **kwargs) -> dict:
    return {"status": "created", "updated": False, "card": _card(name, **kwargs)}


def existing(name: str, *, updated: bool = False, **kwargs) -> dict:
    return {"status": "exists", "updated": updated, "card": _card(name, **kwargs)}


def shared(*users: SharedUser, request_id: int = telegram_import.REQUEST_ID) -> TelegramMessage:
    return _msg(users_shared=SharedUsers(request_id=request_id, users=tuple(users)))


def forwarded(phone: str = "79000000000", first: str = "Татьяна", last: str = "Козлова", user_id: int | None = 111,
              *, sender: int = 5, chat_type: str = "private") -> TelegramMessage:
    return _msg(shared_contact=SharedContact(phone_number=phone, first_name=first, last_name=last, user_id=user_id),
                user_id=sender, chat_type=chat_type)


TANYA = SharedUser(user_id=111, first_name="Татьяна", last_name="Козлова", username="tanya_k")
IVAN = SharedUser(user_id=222, first_name="Иван")


# ---- вход ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "/start crm_add_tg", "/start CRM_ADD_TG", "/start@wwc_bot crm_add_tg", "  /start crm_add_tg  ",
    "добавить из телеграм", "Добавить из Telegram", "добавить из telegram!", "добавить из телеграма", "/добавить из телеграм",
])
def test_entry_words(text):
    assert telegram_import.is_entry_text(text)


@pytest.mark.parametrize("text", [
    "", "/start", "/start crm_add_tg extra", "/start crm", "добавить", "добавить из телефона", "добавить из телеграм всех",
    "Добавить из Telegram контакты", "telegram", "crm_add_tg",
])
def test_other_text_is_not_an_entry(text):
    assert not telegram_import.is_entry_text(text)


@pytest.mark.parametrize("text", ["✖️ Не добавлять", "✖ Не добавлять", "  ✖️  не добавлять ", "✖️ НЕ ДОБАВЛЯТЬ"])
def test_cancel_words(text):
    assert telegram_import.is_cancel_text(text)
    assert not telegram_import.is_cancel_text(text + " сейчас")


async def test_the_prompt_and_the_keyboard_are_exactly_the_agreed_ones(world):
    result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("/start crm_add_tg"), trace_id="t1")
    assert result == {"ok": True, "route": "crm_add_tg", "status": "prompt", "trace_id": "t1"}
    [(text, markup)] = world.sent
    assert text == (
        "Кого добавить в WWC CRM? Нажмите кнопку под полем ввода и выберите людей в Telegram, до 10 за раз.\n\n"
        "В карточке сохранятся имя и адрес Telegram, оттуда можно сразу написать. "
        "Номер Telegram не передаёт: его можно вписать в карточке или переслать мне контакт человека."
    )
    assert markup == {
        "keyboard": [
            [{"text": "👥 Выбрать людей", "request_users": {
                "request_id": telegram_import.REQUEST_ID, "user_is_bot": False, "max_quantity": 10,
                "request_name": True, "request_username": True}}],
            [{"text": "✖️ Не добавлять"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    assert isinstance(telegram_import.REQUEST_ID, int)
    assert world.send.await_args.kwargs["chat_id"] == "5" and world.send.await_args.kwargs["bot_token"] == "token"


async def test_the_words_open_the_same_prompt(world):
    for text in ("добавить из телеграм", "Добавить из Telegram"):
        world.send.reset_mock()
        result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg(text), trace_id="t")
        assert result["status"] == "prompt"
        assert world.sent[0][1]["keyboard"][0][0]["text"] == "👥 Выбрать людей"


async def test_cancel_takes_the_keyboard_away_and_asks_the_database_nothing(world):
    result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("✖️ Не добавлять"), trace_id="t")
    assert result["status"] == "cancelled"
    assert world.sent == [("Хорошо, никого не добавляю.", REMOVE)]
    world.viewer.assert_not_awaited()


async def test_a_closed_crm_answers_the_entry_with_the_lock_text(world):
    world.viewer.return_value = UNPAID
    result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("/start crm_add_tg"), trace_id="t")
    assert result["status"] == "pro_required"
    [(text, markup)] = world.sent
    assert text == bot.LOCK_TEXT["pro_required"]
    assert markup == {"inline_keyboard": [[{"text": "Продлить платформу", "callback_data": "renew:start"}]]}

    world.send.reset_mock()
    world.viewer.return_value = PAID
    with patch.dict("os.environ", {"PLATFORM_CRM_PILOT_TELEGRAM_IDS": "77"}):
        get_settings.cache_clear()
        result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("добавить из телеграм"), trace_id="t")
    assert result["status"] == "crm_pilot_only"
    assert world.sent == [(bot.LOCK_TEXT["crm_pilot_only"], None)]


async def test_no_crm_for_the_tenant_or_a_group_chat_is_not_ours(world):
    for tenant, msg in ((WITHOUT_CRM, _msg("/start crm_add_tg")), (WITH_CRM, _msg("/start crm_add_tg", chat_type="group")),
                        (WITHOUT_CRM, _msg("✖️ Не добавлять")), (WITH_CRM, _msg("✖️ Не добавлять", chat_type="supergroup"))):
        assert await telegram_import.try_handle_crm_add_telegram(tenant, msg, trace_id="t") is None
    world.viewer.assert_not_awaited()
    world.send.assert_not_awaited()


async def test_other_text_falls_through(world):
    assert await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("привет"), trace_id="t") is None
    assert await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("/start src_abc"), trace_id="t") is None
    world.send.assert_not_awaited()


async def test_a_failed_lookup_says_so_briefly_and_does_not_raise(world, caplog):
    world.viewer.side_effect = RuntimeError("db down: Татьяна +79000000000")
    with caplog.at_level(logging.WARNING):
        result = await telegram_import.try_handle_crm_add_telegram(WITH_CRM, _msg("/start crm_add_tg"), trace_id="t")
    assert result["ok"] is False
    assert world.sent == [("Не получилось добавить, попробуйте ещё раз.", REMOVE)]
    assert "Татьяна" not in caplog.text and "79000000000" not in caplog.text


def _callback(data: str = "crm:addtg", chat_type: str = "private") -> TelegramCallbackQuery:
    return TelegramCallbackQuery(chat_id=5, user_id=5, callback_query_id="cb1", data=data, chat_type=chat_type, raw={})


async def test_the_button_opens_the_prompt_and_answers_the_tap(world):
    result = await telegram_import.try_handle_crm_add_telegram_callback(WITH_CRM, _callback(), trace_id="t")
    assert result["status"] == "prompt"
    world.answer.assert_awaited_once_with(callback_query_id="cb1", bot_token="token")
    assert world.sent[0][1]["keyboard"][0][0]["text"] == "👥 Выбрать людей"


async def test_the_button_of_a_closed_crm_gives_the_lock_text(world):
    world.viewer.return_value = UNPAID
    result = await telegram_import.try_handle_crm_add_telegram_callback(WITH_CRM, _callback(), trace_id="t")
    assert result["status"] == "pro_required"
    assert world.sent[0][0] == bot.LOCK_TEXT["pro_required"]


async def test_other_buttons_are_not_ours_and_a_dead_button_still_stops_the_spinner(world):
    assert await telegram_import.try_handle_crm_add_telegram_callback(WITH_CRM, _callback("crm:other"), trace_id="t") is None
    world.answer.assert_not_awaited()
    result = await telegram_import.try_handle_crm_add_telegram_callback(WITHOUT_CRM, _callback(), trace_id="t")
    assert result["status"] == "unavailable"
    world.answer.assert_awaited_once()
    world.send.assert_not_awaited()


async def test_the_open_message_carries_the_second_button(world):
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)), patch.object(
        bot, "with_site_login", AsyncMock(return_value=CRM_URL)
    ), patch.object(bot, "current_bot_binding", return_value=SimpleNamespace(bot_token="t")), patch.object(
        bot, "send_telegram_text", world.send
    ):
        await bot.try_handle_crm_text(WITH_CRM, _msg("ежедневник"), trace_id="t")
    assert world.sent[0][1] == {"inline_keyboard": [
        [{"text": "📒 Открыть WWC CRM", "url": CRM_URL}],
        [{"text": "➕ Добавить из Telegram", "callback_data": "crm:addtg"}],
    ]}
    assert bot.ADD_TG_CALLBACK == "crm:addtg"


# ---- выбор людей (users_shared) ----------------------------------------------------------------------


async def test_new_people_are_added_and_the_keyboard_is_taken_away(world):
    world.add.return_value = [created("Татьяна Козлова", username="tanya_k", user_id=111), created("Иван", user_id=222)]
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA, IVAN), trace_id="t1")
    assert result == {"ok": True, "route": "crm_add_tg", "status": "added", "created": 2, "existing": 0, "trace_id": "t1"}
    assert world.sent == [
        ("Добавлено в WWC CRM: 2\n• Татьяна Козлова (@tanya_k)\n• Иван", REMOVE),
        ("Откройте карточки, чтобы написать людям:", OPEN_MARKUP),
    ]
    tenant_id, account, people = world.add.await_args.args
    assert (tenant_id, account) == ("whieda", ACCOUNT)
    assert world.add.await_args.kwargs == {"source": "Telegram"}
    assert people == [
        {"name": "Татьяна Козлова", "telegram_username": "tanya_k", "telegram_user_id": 111},
        {"name": "Иван", "telegram_username": None, "telegram_user_id": 222},
    ]
    world.login.assert_awaited_once()
    assert world.login.await_args.kwargs == {"tenant_id": "whieda", "telegram_user_id": 5}


async def test_already_there_by_id_or_name_nobody_new(world):
    world.add.return_value = [existing("Анна", username="anna_p")]
    await telegram_import.try_handle_crm_shared(WITH_CRM, shared(SharedUser(user_id=9, first_name="Анна")), trace_id="t")
    assert world.sent[0] == ("Уже были в WWC CRM: Анна", REMOVE)
    assert world.sent[1][0] == "Откройте карточки, чтобы написать людям:"


async def test_some_new_some_already_there(world):
    world.add.return_value = [created("Татьяна Козлова", username="tanya_k"), existing("Анна"), existing("Пётр")]
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA, IVAN, IVAN), trace_id="t")
    assert (result["created"], result["existing"]) == (1, 2)
    assert world.sent[0] == ("Добавлено в WWC CRM: 1\n• Татьяна Козлова (@tanya_k)\n\nУже были: Анна, Пётр", REMOVE)


async def test_people_without_an_address_or_a_name_get_the_fallbacks(world):
    world.add.return_value = [created("Иван"), created("@only_name", username="only_name"), created("Контакт Telegram")]
    await telegram_import.try_handle_crm_shared(
        WITH_CRM,
        shared(IVAN, SharedUser(user_id=3, username="only_name"), SharedUser(user_id=4)),
        trace_id="t",
    )
    people = world.add.await_args.args[2]
    assert [p["name"] for p in people] == ["Иван", "@only_name", ""]  # an empty name becomes «Контакт Telegram» in the service
    assert people[1]["telegram_username"] == "only_name" and people[2]["telegram_username"] is None
    assert world.sent[0][0] == "Добавлено в WWC CRM: 3\n• Иван\n• @only_name\n• Контакт Telegram"


async def test_a_strange_username_from_telegram_is_dropped_not_stored(world):
    world.add.return_value = [created("Иван")]
    await telegram_import.try_handle_crm_shared(
        WITH_CRM, shared(SharedUser(user_id=3, first_name="Иван", username="a b")), trace_id="t"
    )
    assert world.add.await_args.args[2][0]["telegram_username"] is None


async def test_more_than_ten_people_are_cut_to_ten(world):
    users = [SharedUser(user_id=100 + i, first_name=f"Чел{i}") for i in range(14)]
    world.add.return_value = [created(f"Чел{i}") for i in range(10)]
    await telegram_import.try_handle_crm_shared(WITH_CRM, shared(*users), trace_id="t")
    assert len(world.add.await_args.args[2]) == 10


async def test_html_in_a_name_cannot_break_the_message(world):
    world.add.return_value = [created("<b>Вася <i>Пупкин", username="vasya_p")]
    await telegram_import.try_handle_crm_shared(
        WITH_CRM, shared(SharedUser(user_id=3, first_name="<b>Вася", last_name="<i>Пупкин", username="vasya_p")), trace_id="t"
    )
    text = world.sent[0][0]
    assert "<" not in text and ">" not in text
    assert "bВася iПупкин (@vasya_p)" in text


async def test_an_answer_to_another_button_is_not_ours(world):
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA, request_id=1), trace_id="t") is None
    world.viewer.assert_not_awaited()
    world.add.assert_not_awaited()
    world.send.assert_not_awaited()


async def test_picked_people_with_a_closed_crm_get_the_lock_text_and_nothing_is_written(world):
    world.viewer.return_value = UNPAID
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA), trace_id="t")
    assert result["status"] == "pro_required"
    assert world.sent[0][0] == bot.LOCK_TEXT["pro_required"]
    world.add.assert_not_awaited()


async def test_picked_people_in_a_group_or_without_crm_are_not_ours(world):
    group = TelegramMessage(chat_id=-1, user_id=5, message_id=1, text="", chat_type="supergroup", file_id=None, raw={},
                            users_shared=SharedUsers(request_id=telegram_import.REQUEST_ID, users=(TANYA,)))
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, group, trace_id="t") is None
    assert await telegram_import.try_handle_crm_shared(WITHOUT_CRM, shared(TANYA), trace_id="t") is None
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, _msg(), trace_id="t") is None  # neither people nor a contact
    world.add.assert_not_awaited()
    world.send.assert_not_awaited()


async def test_a_database_failure_is_a_short_answer_and_a_log_without_personal_data(world, caplog):
    world.add.side_effect = RuntimeError("insert failed: Татьяна Козлова tanya_k +79000000000")
    with caplog.at_level(logging.WARNING):
        result = await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA), trace_id="t")
    assert result["ok"] is False and result["status"] == "error"
    assert world.sent == [("Не получилось добавить, попробуйте ещё раз.", REMOVE)]
    assert "crm_add_tg_failed" in caplog.text
    assert "Татьяна" not in caplog.text and "tanya_k" not in caplog.text and "79000000000" not in caplog.text

    world.send.reset_mock()
    world.add.side_effect = None
    world.account.side_effect = RuntimeError("account")
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, shared(TANYA), trace_id="t")
    assert result["ok"] is False
    assert world.sent == [("Не получилось добавить, попробуйте ещё раз.", REMOVE)]


async def test_a_failed_login_link_still_sends_the_button_without_the_sign_in(world):
    world.add.return_value = [created("Иван")]
    world.login.side_effect = RuntimeError("no login")
    await telegram_import.try_handle_crm_shared(WITH_CRM, shared(IVAN), trace_id="t")
    assert world.sent[1] == ("Откройте карточки, чтобы написать людям:",
                             {"inline_keyboard": [[{"text": "📒 Открыть WWC CRM", "url": "https://igor.wwc.best/crm/"}]]})


# ---- пересланный контакт --------------------------------------------------------------------------


async def test_a_forwarded_contact_becomes_a_card_with_the_number(world):
    world.add.return_value = [created("Татьяна", phone="+79000000000", user_id=111)]
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(first="Татьяна", last=""), trace_id="t1")
    assert result == {"ok": True, "route": "crm_add_tg", "status": "created", "updated": False, "trace_id": "t1"}
    assert world.sent == [("Добавлено в WWC CRM: Татьяна, +7 900 000-00-00", OPEN_MARKUP)]
    assert world.add.await_args.args[2] == [
        {"name": "Татьяна", "phone": "+79000000000", "telegram_user_id": 111, "telegram_username": None}
    ]
    assert world.add.await_args.kwargs == {"source": "Telegram"}


async def test_a_number_already_with_a_plus_and_a_foreign_number(world):
    world.add.return_value = [created("Ян", phone="+375291234567")]
    await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded("+375291234567", "Ян", ""), trace_id="t")
    assert world.add.await_args.args[2][0]["phone"] == "+375291234567"
    assert world.sent[0][0] == "Добавлено в WWC CRM: Ян, +375291234567"


async def test_a_contact_without_a_telegram_account_is_still_a_card(world):
    world.add.return_value = [created("Ян", phone="+375291234567")]
    await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded("375291234567", "Ян", "", user_id=None), trace_id="t")
    person = world.add.await_args.args[2][0]
    assert person["telegram_user_id"] is None and person["phone"] == "+375291234567"


async def test_a_known_person_is_not_added_again(world):
    world.add.return_value = [existing("Таня", updated=True, phone="+79000000000")]
    result = await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t")
    assert result["status"] == "exists" and result["updated"] is True
    assert world.sent == [("Этот человек уже в WWC CRM. Карточка обновлена.", OPEN_MARKUP)]

    world.send.reset_mock()
    world.add.return_value = [existing("Таня", updated=False, phone="+79000000000")]
    await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t")
    assert world.sent == [("Этот человек уже в WWC CRM.", OPEN_MARKUP)]


async def test_the_senders_own_number_is_never_taken(world):
    own = forwarded(user_id=5, sender=5)
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, own, trace_id="t") is None
    world.viewer.assert_not_awaited()
    world.add.assert_not_awaited()
    world.send.assert_not_awaited()


async def test_a_forwarded_contact_with_a_closed_crm_is_left_alone(world):
    world.viewer.return_value = UNPAID
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t") is None
    world.viewer.return_value = PAID
    assert await telegram_import.try_handle_crm_shared(WITHOUT_CRM, forwarded(), trace_id="t") is None
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(chat_type="group"), trace_id="t") is None
    with patch.dict("os.environ", {"PLATFORM_CRM_PILOT_TELEGRAM_IDS": "77"}):
        get_settings.cache_clear()
        assert await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t") is None
    world.viewer.side_effect = RuntimeError("db down")
    assert await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t") is None
    world.add.assert_not_awaited()
    world.send.assert_not_awaited()


async def test_a_database_failure_on_a_contact_is_a_short_answer(world, caplog):
    world.add.side_effect = RuntimeError("insert failed +79000000000 Татьяна")
    with caplog.at_level(logging.WARNING):
        result = await telegram_import.try_handle_crm_shared(WITH_CRM, forwarded(), trace_id="t")
    assert result["ok"] is False
    assert world.sent == [("Не получилось добавить, попробуйте ещё раз.", REMOVE)]
    assert "Татьяна" not in caplog.text and "79000000000" not in caplog.text


def test_pretty_phone():
    assert telegram_import.pretty_phone({"phone_e164": "+79000000000"}) == "+7 900 000-00-00"
    assert telegram_import.pretty_phone({"phone_e164": "+375291234567"}) == "+375291234567"
    assert telegram_import.pretty_phone({"phone_e164": None, "phone_raw": "позвонить <вечером>"}) == "позвонить вечером"
    assert telegram_import.pretty_phone({}) == ""


# ---- через процессор: с тремя меню бота и без них ------------------------------------------------------


def _update_for(message: dict, user_id: int = 5) -> dict:
    return _update(message, user_id=user_id)


def _through(stack: ExitStack, world: World) -> dict[str, AsyncMock]:
    mocks = _through_processor(stack)
    for name in ("first_start_consent_notice", "offer_marketing_consent"):
        mocks[name] = stack.enter_context(patch(f"app.telegram.processor.{name}", AsyncMock(return_value=None)))
    mocks["record_source_start"] = stack.enter_context(patch("app.telegram.processor.record_source_start", AsyncMock()))
    mocks["handle_start_token"] = stack.enter_context(
        patch("app.telegram.processor.handle_start_token", AsyncMock(return_value={"ok": True, "route": "start_token"}))
    )
    mocks["referral_start"] = stack.enter_context(
        patch("app.telegram.processor.handle_referral_start_token", AsyncMock(return_value={"ok": True, "route": "referral"}))
    )
    mocks["site_start"] = stack.enter_context(
        patch("app.telegram.processor.handle_site_start_token", AsyncMock(return_value={"ok": True, "route": "site"}))
    )
    world.install(stack)
    return mocks


@pytest.fixture(params=["stages_off", "stages_on"])
def both_menus(request, monkeypatch):
    """The three bot menus (PLATFORM_BOT_STAGES) off — the old suite — and on: «Добавить из Telegram» works the same."""
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "true" if request.param == "stages_on" else "false")
    get_settings.cache_clear()
    yield request.param
    get_settings.cache_clear()


async def _run(update: dict, world: World, whieda_bot_binding, *, tenant: TenantContext = WITH_CRM) -> tuple[dict, dict]:
    with ExitStack() as stack:
        mocks = _through(stack, world)
        result = await process_core_telegram_update(tenant, update, "t1", binding=whieda_bot_binding)
    return result, mocks


async def test_the_link_from_the_site_reaches_us_before_the_start_labels(both_menus, whieda_bot_binding):
    world = World()
    result, mocks = await _run(_update_for({"text": "/start crm_add_tg"}), world, whieda_bot_binding)
    assert result["route"] == "crm_add_tg" and result["status"] == "prompt"
    assert world.sent[0][1]["keyboard"][0][0]["request_users"]["request_id"] == telegram_import.REQUEST_ID
    for name in ("record_source_start", "handle_start_token", "referral_start", "site_start"):
        mocks[name].assert_not_awaited()  # not a referral, not a source label, not an unknown start


async def test_other_start_parameters_still_go_their_way(both_menus, whieda_bot_binding):
    world = World()
    result, mocks = await _run(_update_for({"text": "/start something_else"}), world, whieda_bot_binding)
    assert result["route"] == "start_token"
    mocks["handle_start_token"].assert_awaited_once()
    world.send.assert_not_awaited()


async def test_the_words_and_the_cancel_go_through_the_processor(both_menus, whieda_bot_binding):
    world = World()
    result, _ = await _run(_update_for({"text": "добавить из телеграм"}), world, whieda_bot_binding)
    assert result["status"] == "prompt"
    world2 = World()
    result, _ = await _run(_update_for({"text": "✖️ Не добавлять"}), world2, whieda_bot_binding)
    assert result["status"] == "cancelled"
    assert world2.sent == [("Хорошо, никого не добавляю.", REMOVE)]


async def test_the_button_goes_through_the_processor(both_menus, whieda_bot_binding):
    world = World()
    update = {"update_id": 1, "callback_query": {
        "id": "cb1", "data": "crm:addtg", "from": {"id": 5}, "message": {"message_id": 3, "chat": {"id": 5, "type": "private"}}}}
    with ExitStack() as stack:
        _through(stack, world)
        result = await process_core_telegram_update(WITH_CRM, update, "t1", binding=whieda_bot_binding)
    assert result["route"] == "crm_add_tg" and result["status"] == "prompt"
    world.answer.assert_awaited_once()


async def test_picked_people_go_through_the_processor(both_menus, whieda_bot_binding):
    world = World()
    world.add.return_value = [created("Татьяна Козлова", username="tanya_k")]
    update = _update_for({"users_shared": {"request_id": telegram_import.REQUEST_ID, "users": [
        {"user_id": 111, "first_name": "Татьяна", "last_name": "Козлова", "username": "tanya_k"}]}})
    result, mocks = await _run(update, world, whieda_bot_binding)
    assert result["status"] == "added" and result["created"] == 1
    assert [text for text, _ in world.sent] == ["Добавлено в WWC CRM: 1\n• Татьяна Козлова (@tanya_k)",
                                                "Откройте карточки, чтобы написать людям:"]
    for name in ("unrouted", "support_media", "site_voice"):
        mocks[name].assert_not_awaited()  # not lost to «фото без подписи» and not forwarded to support


async def test_a_forwarded_contact_goes_through_the_processor(both_menus, whieda_bot_binding):
    world = World()
    world.add.return_value = [created("Татьяна", phone="+79000000000")]
    update = _update_for({"contact": {"phone_number": "79000000000", "first_name": "Татьяна", "user_id": 111}})
    result, _ = await _run(update, world, whieda_bot_binding)
    assert result["status"] == "created"
    assert world.sent == [("Добавлено в WWC CRM: Татьяна, +7 900 000-00-00", OPEN_MARKUP)]


async def test_a_contact_nobody_takes_is_ignored_as_before(both_menus, whieda_bot_binding):
    """Своя карточка, закрытая CRM, чужой арендатор без CRM — как раньше: «ignored», без ответа и без записи."""
    own = _update_for({"contact": {"phone_number": "79000000000", "first_name": "Я", "user_id": 5}})
    for update, tenant, viewer in (
        (own, WITH_CRM, PAID),
        (_update_for({"contact": {"phone_number": "79000000000", "first_name": "Т", "user_id": 111}}), WITH_CRM, UNPAID),
        (_update_for({"contact": {"phone_number": "79000000000", "first_name": "Т", "user_id": 111}}), WITHOUT_CRM, PAID),
        (_update_for({"users_shared": {"request_id": 1, "users": [{"user_id": 111}]}}), WITH_CRM, PAID),
    ):
        world = World()
        world.viewer.return_value = viewer
        result, mocks = await _run(update, world, whieda_bot_binding, tenant=tenant)
        assert result == {"ok": True, "route": "ignored"}
        world.add.assert_not_awaited()
        world.send.assert_not_awaited()
        for name in ("unrouted", "support_media", "site_voice"):
            mocks[name].assert_not_awaited()


async def test_the_whole_road_from_the_raw_update_through_the_inbox_copy(both_menus, whieda_bot_binding):
    """The durable inbox keeps a compact copy of the update; the CRM must work from that copy too."""
    from app.telegram.inbox import compact_telegram_payload

    world = World()
    world.add.return_value = [created("Иван", user_id=222)]
    raw = _update_for({"users_shared": {"request_id": telegram_import.REQUEST_ID, "users": [
        {"user_id": 222, "first_name": "Иван", "username": None}]}})
    result, _ = await _run(compact_telegram_payload(raw), world, whieda_bot_binding)
    assert result["status"] == "added"
    assert parse_shared_message(compact_telegram_payload(raw)).users_shared.users[0].user_id == 222
