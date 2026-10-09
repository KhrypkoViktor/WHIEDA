"""Группа потока после «Оплачено»: личная одноразовая ссылка, без отметок в группе (09.10.2026)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.bindings import binding_context_scope
from app.telegram.course_groups import COURSE_GROUPS, invite_to_course_group


@pytest.mark.asyncio
@pytest.mark.parametrize(("member_status", "expected", "dm"), [("left", "invited", True), ("member", "member", False)])
async def test_buyer_gets_a_one_time_link_unless_already_in_the_group(whieda_bot_binding, member_status, expected, dm):
    group_id = COURSE_GROUPS["online-start-4w"][0]

    async def telegram(method, payload, *, bot_token):
        assert payload["chat_id"] == group_id
        if method == "getChatMember":
            return {"ok": True, "result": {"status": member_status}}
        assert method == "createChatInviteLink" and payload["member_limit"] == 1
        return {"ok": True, "result": {"invite_link": "https://t.me/+one"}}

    send = AsyncMock(return_value={"ok": True})
    with patch("app.telegram.course_groups._call_telegram", AsyncMock(side_effect=telegram)), \
         patch("app.telegram.course_groups.send_telegram_text", send), binding_context_scope(whieda_bot_binding):
        status = await invite_to_course_group(course_slug="online-start-4w", chat_id=7, user_id=7, name="Альберт")
    assert status == expected
    assert bool(send.await_count) is dm
    if dm:
        kwargs = send.await_args.kwargs
        assert kwargs["chat_id"] == "7" and "https://t.me/+one" in kwargs["text"] and "одноразовая" in kwargs["text"]


@pytest.mark.asyncio
async def test_course_without_a_group_sends_nothing(whieda_bot_binding):
    call = AsyncMock()
    with patch("app.telegram.course_groups._call_telegram", call), binding_context_scope(whieda_bot_binding):
        assert await invite_to_course_group(course_slug="vozrazheniya", chat_id=7, user_id=7, name="x") == "no_group"
    call.assert_not_awaited()
