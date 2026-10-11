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
# Та же группа в Max (мост V27, app/max/bridges.py): ссылка-приглашение от владельца
# (11.10.2026, «скопировал тебе ссылку»). Бот в Max ссылки на закрытую группу не создаёт.
COURSE_MAX_LINKS: dict[str, str] = {
    "online-start-4w": "https://max.ru/join/dxn9bSet56xsAa0DwO8tNaNZNxDpandkBKX7tGe462E",
}
_IN_GROUP = {"member", "administrator", "creator", "restricted"}


def invite_text(title: str, url: str, max_link: str = "") -> str:
    text = (
        f"Ваша личная ссылка в группу потока «{title}»:\n{url}\n\n"
        "Ссылка одноразовая — только для вас. В группе — живые занятия, вопросы и разборы."
    )
    if max_link:
        text += (
            f"\n\nЭта же группа есть в Max — сообщения ходят в обе стороны:\n{max_link}\n"
            "Выберите, где удобнее: Telegram или Max."
        )
    return text


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
    text = invite_text(title, url, COURSE_MAX_LINKS.get(str(course_slug), ""))
    sent = await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=token)
    return "invited" if sent.get("ok") else "failed"


GROUP_NOTE = {
    "invited": " Ссылка в группу потока отправлена.",
    "member": " Покупатель уже в группе потока.",
    "failed": " ⚠️ Ссылку в группу потока отправить не удалось — добавьте вручную.",
    "no_group": "",
}
