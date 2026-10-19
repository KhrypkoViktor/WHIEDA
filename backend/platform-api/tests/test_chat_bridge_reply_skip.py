"""Группа с мостом в Max: реплай на сообщение самого бота не идёт в обычную обработку.

Владелец ответил реплаем на сообщение бота, пересланное мостом из Max: мост отправил ответ в Max,
а обычная обработка приняла реплай за вопрос боту, и подборщик написал в группу «Не понял вопрос…».
Теперь такое сообщение (без @бота и не команда) после постановки моста дальше не идёт; всё остальное —
вступления, упоминания, команды, ответы людям, чужие группы — как раньше.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.max.bridge import is_reply_to_bot_only
from app.max.bridges import BRIDGES
from app.telegram.bindings import BotBindingContext
from app.telegram.inbox import InMemoryInboxStore, set_inbox_store_for_tests

BOT_ID, BOT_NAME = 7700001, "WWC_Support_bot"
BRIDGED_CHAT = BRIDGES[0].tg_chat_id
OWNER = 482284673


def reply(text="Спасибо, ответили", *, to=None, **extra):
    """Сообщение-ответ; ``to`` — автор сообщения, на которое отвечают (по умолчанию бот)."""
    author = to or {"id": BOT_ID, "is_bot": True, "username": BOT_NAME}
    return {"text": text, "from": {"id": OWNER}, "reply_to_message": {"message_id": 5, "from": author}, **extra}


def skipped(message, **kwargs):
    return is_reply_to_bot_only(
        message, bot_id=kwargs.get("bot_id", BOT_ID), bot_username=kwargs.get("bot_username", BOT_NAME)
    )


# ---- the pure condition -------------------------------------------------------------------------


def test_reply_to_the_bot_without_mention_is_skipped():
    assert skipped(reply()) is True
    assert skipped(reply("")) is True  # подпись и текст пустые (стикер, фото): тоже ответ мосту
    assert skipped(reply(None, caption="фото в ответ")) is True


def test_the_bot_is_found_by_id_or_by_username_alone():
    only_id = {"id": BOT_ID, "is_bot": True}
    only_name = {"id": 1, "username": "wwc_support_BOT"}
    assert skipped(reply(to=only_id)) is True
    assert skipped(reply(to=only_name)) is True
    assert skipped(reply(to=only_id), bot_username=None) is True
    assert skipped(reply(to=only_name), bot_id=None) is True


def test_explicit_mention_of_the_bot_is_processed():
    assert skipped(reply(f"@{BOT_NAME} какой курс взять?")) is False
    assert skipped(reply(f"подскажите, @{BOT_NAME.lower()}")) is False
    assert skipped(reply(None, caption=f"@{BOT_NAME} вот фото")) is False
    # Похожее имя — не упоминание этого бота.
    assert skipped(reply(f"@{BOT_NAME}_old привет")) is True
    assert skipped(reply(f"почта me@{BOT_NAME}")) is True


def test_commands_are_processed():
    assert skipped(reply("/start")) is False
    assert skipped(reply("  /help@WWC_Support_bot")) is False


def test_reply_to_a_person_or_a_plain_message_is_as_before():
    assert skipped(reply(to={"id": 42, "username": "someone"})) is False
    assert skipped(reply(to={"id": 42, "is_bot": True, "username": "other_bot"})) is False  # другой бот
    assert skipped({"text": "просто сообщение", "from": {"id": OWNER}}) is False
    assert skipped({"text": "x", "reply_to_message": {"message_id": 1}}) is False  # автор ответа неизвестен
    assert skipped({"text": "x", "reply_to_message": None}) is False
    assert skipped({}) is False


def test_without_bot_identity_nothing_is_skipped():
    person = {"id": 42}
    assert skipped(reply(to=person), bot_id=None, bot_username=None) is False
    assert skipped(reply(), bot_id=None, bot_username=None) is False  # бота не опознать — как раньше


# ---- the webhook ----------------------------------------------------------------------------------


def _context(tenant, *, binding_id: str = "support-binding") -> BotBindingContext:
    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref="env:X",
        webhook_secret_ref="env:Y",
        bot_username=BOT_NAME,
        status="active",
        processing_mode="core",
        bot_token=f"{BOT_ID}:token",
        webhook_secret="support-secret",
    )


@pytest.fixture(autouse=True)
def inbox_store():
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    yield store
    set_inbox_store_for_tests(None)


def _update(message: dict, *, chat_id: int = BRIDGED_CHAT) -> dict:
    return {"update_id": 900, "message": {"message_id": 77, "chat": {"id": chat_id, "type": "supergroup"}, **message}}


async def _post(client, whieda_tenant, monkeypatch, update, *, durable=False):
    monkeypatch.setattr("app.telegram.routes.resolve_bot_binding_context", AsyncMock(return_value=_context(whieda_tenant)))
    monkeypatch.setattr("app.telegram.routes._durable_inbox_enabled", lambda binding: durable)
    handled = {
        "bridge": AsyncMock(),
        "process": AsyncMock(),
        "enqueue": AsyncMock(return_value=True),
    }
    monkeypatch.setattr("app.telegram.routes._bridge_group_message", handled["bridge"])
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", handled["process"])
    monkeypatch.setattr("app.telegram.routes._enqueue_durable_update", handled["enqueue"])
    response = await client.post(
        "/v1/telegram/support-binding/webhook", json=update, headers={"x-telegram-bot-api-secret-token": "support-secret"}
    )
    assert response.status_code == 200 and response.json() == {"ok": True}
    return handled


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
async def test_reply_to_the_bot_is_bridged_and_stops_there(client, whieda_tenant, monkeypatch, durable):
    handled = await _post(client, whieda_tenant, monkeypatch, _update(reply()), durable=durable)
    handled["bridge"].assert_awaited_once()
    handled["process"].assert_not_awaited()
    handled["enqueue"].assert_not_awaited()  # ни в обычную обработку, ни в durable inbox


@pytest.mark.asyncio
@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize(
    "message",
    [
        reply(f"@{BOT_NAME} вопрос"),
        reply("/start"),
        reply("ответ человеку", to={"id": 42, "username": "someone"}),
        {"text": "обычное сообщение", "from": {"id": OWNER}},
        {"new_chat_members": [{"id": 5}], "from": {"id": 5}},
    ],
)
async def test_everything_else_in_a_bridged_group_goes_on_as_before(client, whieda_tenant, monkeypatch, message, durable):
    handled = await _post(client, whieda_tenant, monkeypatch, _update(message), durable=durable)
    handled["bridge"].assert_awaited_once()
    if durable:
        handled["enqueue"].assert_awaited_once()
        handled["process"].assert_not_awaited()  # durable: обработку берёт inbox, здесь её запускает он же
    else:
        handled["process"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_group_without_a_bridge_is_untouched(client, whieda_tenant, monkeypatch):
    handled = await _post(client, whieda_tenant, monkeypatch, _update(reply(), chat_id=-100999), durable=False)
    handled["bridge"].assert_not_awaited()
    handled["process"].assert_awaited_once()  # не мостовая группа: реплай на бота обрабатывается как всегда
