"""Группа потока курса: после «Оплачено» — личная одноразовая ссылка покупателю (09.10.2026).

Телеграм не даёт боту добавлять людей в группу. Поэтому бот присылает ссылку на одного
человека; кто уже в группе — ничего не получает. В самой группе бот никого не отмечает,
пока человек не вступил (владелец: «странно писать человеку, когда его нет в группе»).
"""

from __future__ import annotations

import logging

from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import _call_telegram, send_telegram_text

logger = logging.getLogger(__name__)

# Курс Академии → группа потока, где бот — админ.
COURSE_GROUPS: dict[str, tuple[int, str]] = {
    "online-start-4w": (-1004497305833, "WWC : AI \\ SMM 1й поток"),
}
_IN_GROUP = {"member", "administrator", "creator", "restricted"}


def invite_text(title: str, url: str) -> str:
    return (
        f"Ваша личная ссылка в группу потока «{title}»:\n{url}\n\n"
        "Ссылка одноразовая — только для вас. В группе — живые занятия, вопросы и разборы."
    )


async def invite_to_course_group(*, course_slug: str, chat_id: int, user_id: int, name: str) -> str:
    """invited / member / no_group / failed."""
    group = COURSE_GROUPS.get(str(course_slug or ""))
    if not group:
        return "no_group"
    group_id, title = group
    token = current_bot_binding().bot_token
    state = await _call_telegram("getChatMember", {"chat_id": group_id, "user_id": int(user_id)}, bot_token=token)
    if state.get("ok") and (state.get("result") or {}).get("status") in _IN_GROUP:
        return "member"
    link = await _call_telegram(
        "createChatInviteLink", {"chat_id": group_id, "member_limit": 1, "name": f"WWC {name}"[:32]}, bot_token=token
    )
    url = str((link.get("result") or {}).get("invite_link") or "")
    if not url:
        logger.warning("course_group_invite_failed", extra={"course": course_slug})
        return "failed"
    sent = await send_telegram_text(chat_id=str(chat_id), text=invite_text(title, url), bot_token=token)
    return "invited" if sent.get("ok") else "failed"


GROUP_NOTE = {
    "invited": " Ссылка в группу потока отправлена.",
    "member": " Покупатель уже в группе потока.",
    "failed": " ⚠️ Ссылку в группу потока отправить не удалось — добавьте вручную.",
    "no_group": "",
}
