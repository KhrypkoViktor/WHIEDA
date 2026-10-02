"""Course pre-moderation in the owner's bot: «Опубликовать» / «Вернуть» with a one-line reason."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.academy.service import AcademyError, AcademyViewer
from app.telegram.academy import try_handle_academy_callback, try_handle_academy_text
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage

COURSE_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OWNER = AcademyViewer(telegram_user_id=1, is_preview_admin=True, partner_paid=False)
AUTHOR = AcademyViewer(telegram_user_id=7001, is_preview_admin=False, partner_paid=False)


def callback(data: str, user_id: int = 1) -> TelegramCallbackQuery:
    return TelegramCallbackQuery(chat_id=user_id, user_id=user_id, callback_query_id="cb", data=data, chat_type="private", raw={})


def message(text: str, *, user_id: int = 1, reply_to: str | None = None) -> TelegramMessage:
    body = {"message_id": 5, "text": text, "chat": {"id": user_id, "type": "private"}}
    if reply_to is not None:
        body["reply_to_message"] = {"message_id": 4, "from": {"id": 42, "is_bot": True}, "text": reply_to}
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=5, text=text, chat_type="private",
                           file_id=None, raw={"update_id": 1, "message": body})


@pytest.fixture
def bot(whieda_bot_binding):
    send = AsyncMock(return_value={"ok": True})
    with binding_context_scope(whieda_bot_binding), patch("app.telegram.academy.send_telegram_text", send), patch(
        "app.telegram.academy.answer_callback_query", AsyncMock()
    ), patch("app.telegram.academy.preview_admin_ids", return_value=frozenset({1})):
        yield send


@pytest.mark.asyncio
async def test_owner_publishes_from_the_card(whieda_tenant, bot):
    done = {"ok": True, "slug": "akvarel", "title": "Акварель", "status": "published"}
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.review_course", AsyncMock(return_value=done)
    ) as review:
        result = await try_handle_academy_callback(whieda_tenant, callback(f"acadrev:ok:{COURSE_ID}"), trace_id="t")
    assert result["status"] == "published"
    assert review.await_args.kwargs == {"course_id": COURSE_ID, "decision": "published", "note": ""}
    assert bot.await_args.kwargs["text"] == "✅ Курс «Акварель» опубликован. Автору отправлен ответ."


@pytest.mark.asyncio
async def test_only_the_owner_decides(whieda_tenant, bot):
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=AUTHOR)), patch(
        "app.telegram.academy.review_course", AsyncMock()
    ) as review:
        result = await try_handle_academy_callback(whieda_tenant, callback(f"acadrev:ok:{COURSE_ID}", 7001), trace_id="t")
    assert result["status"] == "not_owner"
    review.assert_not_awaited()
    assert bot.await_args.kwargs["text"] == "Решение по курсу принимает владелец."


@pytest.mark.asyncio
async def test_return_asks_for_a_reason_in_reply(whieda_tenant, bot):
    brief = {"slug": "akvarel", "title": "Акварель", "status": "review"}
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.course_brief", AsyncMock(return_value=brief)
    ):
        result = await try_handle_academy_callback(whieda_tenant, callback(f"acadrev:no:{COURSE_ID}"), trace_id="t")
    assert result["status"] == "reason_asked"
    kwargs = bot.await_args.kwargs
    assert kwargs["text"] == "↩️ Курс «Акварель» (akvarel) — что поправить? Ответьте на это сообщение одной строкой."
    assert kwargs["reply_markup"]["force_reply"] is True


@pytest.mark.asyncio
async def test_card_of_a_course_no_longer_in_review(whieda_tenant, bot):
    brief = {"slug": "akvarel", "title": "Акварель", "status": "published"}
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.course_brief", AsyncMock(return_value=brief)
    ):
        await try_handle_academy_callback(whieda_tenant, callback(f"acadrev:no:{COURSE_ID}"), trace_id="t")
    assert bot.await_args.kwargs["text"] == "Курс «Акварель» уже не на проверке."


@pytest.mark.asyncio
async def test_reply_with_the_reason_returns_the_course(whieda_tenant, bot):
    prompt = "↩️ Курс «Акварель» (akvarel) — что поправить? Ответьте на это сообщение одной строкой."
    done = {"ok": True, "slug": "akvarel", "title": "Акварель", "status": "draft"}
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.review_course", AsyncMock(return_value=done)
    ) as review:
        result = await try_handle_academy_text(
            whieda_tenant, message("Уберите обещание вылечить спину", reply_to=prompt), trace_id="t"
        )
    assert result["status"] == "returned"
    assert review.await_args.kwargs == {"slug": "akvarel", "decision": "returned", "note": "Уберите обещание вылечить спину"}
    assert bot.await_args.kwargs["text"] == "↩️ Курс «Акварель» вернули автору: Уберите обещание вылечить спину"


@pytest.mark.asyncio
async def test_return_by_command_and_strangers_are_ignored(whieda_tenant, bot):
    done = {"ok": True, "slug": "akvarel", "title": "Акварель", "status": "draft"}
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.review_course", AsyncMock(return_value=done)
    ) as review:
        result = await try_handle_academy_text(whieda_tenant, message("вернуть akvarel Нет домашки в уроке 2"), trace_id="t")
    assert result["status"] == "returned"
    assert review.await_args.kwargs["note"] == "Нет домашки в уроке 2"
    # Не владелец: «вернуть …» — обычный текст, премодерация его не трогает.
    with patch("app.telegram.academy.review_course", AsyncMock()) as untouched:
        assert await try_handle_academy_text(whieda_tenant, message("вернуть akvarel x", user_id=7001), trace_id="t") is None
    untouched.assert_not_awaited()


@pytest.mark.asyncio
async def test_reason_for_a_course_already_decided(whieda_tenant, bot):
    error = AcademyError(409, "not_in_review", {"status": "published", "title": "Акварель"})
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.telegram.academy.review_course", AsyncMock(side_effect=error)
    ):
        result = await try_handle_academy_text(whieda_tenant, message("вернуть akvarel поздно"), trace_id="t")
    assert result["status"] == "not_in_review"
    assert bot.await_args.kwargs["text"] == "Курс «Акварель» уже не на проверке."
