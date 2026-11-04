"""«Добавить из Telegram» in the bot (WWC CRM, 04.11.2026): people chosen in Telegram's own picker and a
contact forwarded to the bot become cards with the name and the Telegram address — the partner can write to them
from the card at once.

Ways in: ``/start crm_add_tg`` (the link on the site), the words «добавить из телеграм / telegram» and the
second button under «Открыть WWC CRM» (``crm:addtg``). The answer is a keyboard under the input field whose
button opens Telegram's picker (``request_users``, up to 10 people); what is chosen arrives as a message with no
text (``users_shared``), a forwarded contact as ``contact`` — ``app.telegram.update_parser.parse_shared_message``.

Works the same with the three bot menus on or off (PLATFORM_BOT_STAGES): nothing here depends on the stage.
Only a private chat, only for whom the CRM is open (``_available``, ``load_viewer``, ``lock_reason``). A closed
CRM answers the entry with the lock text; a forwarded contact of a person with a closed CRM is not touched —
it goes on as before (ignored). A database failure never breaks the update: it is logged without the person's
data and the partner gets one short line.

The durable inbox (opt-in per binding, ``PLATFORM_TELEGRAM_DURABLE_INBOX_BINDINGS``) keeps people from the picker but
never a phone number, so a forwarded contact reaches this module only on the in-process path (the default).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.crm.bot import (
    ADD_TG_CALLBACK,
    LOCK_TEXT,
    OPEN_BUTTON_LABEL,
    _available,
    lock_markup,
)
from app.crm.rules import CrmRuleError, parse_telegram
from app.crm.service import (
    add_telegram_contacts,
    crm_url,
    get_or_create_account,
    load_viewer,
    lock_reason,
    safe_error,
)
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.navigation import main_menu_reply_keyboard
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import SharedUser, TelegramCallbackQuery, TelegramMessage, parse_start_token
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

ROUTE = "crm_add_tg"
START_TOKEN = "crm_add_tg"
# A permanent number: the answer carries it back and only an answer to OUR button is taken.
REQUEST_ID = 4721
MAX_PEOPLE = 10
SOURCE = "Telegram"

PICK_LABEL = "👥 Выбрать людей"
CANCEL_LABEL = "✖️ Не добавлять"

PROMPT_TEXT = (
    "Кого добавить в WWC CRM? Нажмите кнопку под полем ввода и выберите людей в Telegram, до 10 за раз.\n\n"
    "В карточке сохранятся имя и адрес Telegram, оттуда можно сразу написать. "
    "Номер Telegram не передаёт: его можно вписать в карточке или переслать мне контакт человека."
)
CANCEL_TEXT = "Хорошо, никого не добавляю."
OPEN_CARDS_TEXT = "Откройте карточки, чтобы написать людям:"
FAIL_TEXT = "Не получилось добавить, попробуйте ещё раз."
EXISTS_TEXT = "Этот человек уже в WWC CRM."
EXISTS_UPDATED_TEXT = "Этот человек уже в WWC CRM. Карточка обновлена."

REQUEST_KEYBOARD: dict[str, Any] = {
    "keyboard": [
        [{
            "text": PICK_LABEL,
            "request_users": {
                "request_id": REQUEST_ID,
                "user_is_bot": False,
                "max_quantity": MAX_PEOPLE,
                "request_name": True,
                "request_username": True,
            },
        }],
        [{"text": CANCEL_LABEL}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}
# What the picker's keyboard is replaced with: the bot's own reply keyboard — today it has none, so this is
# «remove_keyboard» (the bot navigates by Telegram's command menu); if a profile gets a keyboard it comes back.
REMOVE_KEYBOARD: dict[str, Any] = main_menu_reply_keyboard()

_ENTRY_RE = re.compile(r"^/?добавить\s+из\s+(?:телеграм\w*|telegram)\s*[.!]?\s*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^\s*✖️?\s*не\s+добавлять\s*$", re.IGNORECASE)


def is_entry_text(text: str) -> bool:
    """«/start crm_add_tg» from the site or the words «добавить из телеграм» / «добавить из telegram»."""
    value = str(text or "").strip()
    if _ENTRY_RE.match(value):
        return True
    token = parse_start_token(value)
    return token is not None and token.strip().lower() == START_TOKEN


def is_cancel_text(text: str) -> bool:
    return bool(_CANCEL_RE.match(str(text or "")))


# ---- small helpers ------------------------------------------------------------------------


async def _say(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token, reply_markup=reply_markup
    )


def _plain(text: Any) -> str:
    """A name from Telegram goes into a message that is sent as HTML: a stray < or > would break the whole
    message («can't parse entities»), so they are dropped from the shown name (the card keeps the original)."""
    return " ".join(str(text or "").replace("<", "").replace(">", "").split())


def _person_name(user: SharedUser) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
    return name


def _person(user: SharedUser) -> dict[str, Any]:
    username = None
    if user.username:
        try:
            username = parse_telegram(user.username)
        except CrmRuleError:
            username = None
    name = _person_name(user) or (f"@{username}" if username else "")
    return {"name": name, "telegram_username": username, "telegram_user_id": user.user_id}


def _label(card: dict[str, Any]) -> str:
    """«Татьяна Козлова (@tanya_k)»; a name that already is the @address stands alone."""
    name = _plain(card.get("name")) or "Контакт Telegram"
    username = card.get("telegram_username")
    if username and name.lower() != f"@{username}".lower():
        return f"{name} (@{username})"
    return name


def pretty_phone(card: dict[str, Any]) -> str:
    """«+7 900 000-00-00» for a Russian number, the number as it is for the rest."""
    e164 = str(card.get("phone_e164") or "")
    digits = e164[1:]
    if e164.startswith("+7") and len(digits) == 11 and digits.isdigit():
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return e164 or _plain(card.get("phone_raw"))


def summary_text(results: list[dict[str, Any]]) -> str:
    created = [item["card"] for item in results if item["status"] == "created"]
    existing = [_plain(item["card"].get("name")) or "Контакт Telegram" for item in results if item["status"] != "created"]
    lines: list[str] = []
    if created:
        lines.append(f"Добавлено в WWC CRM: {len(created)}")
        lines.extend(f"• {_label(card)}" for card in created)
        if existing:
            lines.append("")
            lines.append("Уже были: " + ", ".join(existing))
    elif existing:
        lines.append("Уже были в WWC CRM: " + ", ".join(existing))
    return "\n".join(lines)


async def _open_markup(tenant: TenantContext, viewer: Any, telegram_user_id: int) -> dict[str, Any]:
    """The «📒 Открыть WWC CRM» button with a sign-in link, as in ``try_handle_crm_text``."""
    base = crm_url(viewer)
    try:
        url = await with_site_login(base, tenant_id=tenant.tenant_id, telegram_user_id=telegram_user_id)
    except Exception as exc:  # the cards are already saved: the link without a sign-in still opens the app
        logger.warning("crm_add_tg_login_link_failed", extra=safe_error(exc))
        url = base
    return {"inline_keyboard": [[{"text": OPEN_BUTTON_LABEL, "url": url}]]}


async def _fail(chat_id: int, trace_id: str, status: str = "error") -> dict[str, Any]:
    await _say(chat_id, FAIL_TEXT, REMOVE_KEYBOARD)
    return {"ok": False, "route": ROUTE, "status": status, "trace_id": trace_id}


# ---- the way in: link, words, button ------------------------------------------------------


async def _begin(tenant: TenantContext, *, chat_id: int, user_id: int, trace_id: str) -> dict[str, Any]:
    try:
        viewer = await load_viewer(tenant.tenant_id, user_id)
    except Exception as exc:
        logger.warning("crm_add_tg_viewer_unavailable", extra=safe_error(exc))
        return await _fail(chat_id, trace_id)
    reason = lock_reason(viewer)
    if reason is not None:
        await _say(chat_id, LOCK_TEXT[reason], lock_markup(reason))
        return {"ok": True, "route": ROUTE, "status": reason, "trace_id": trace_id}
    await _say(chat_id, PROMPT_TEXT, REQUEST_KEYBOARD)
    return {"ok": True, "route": ROUTE, "status": "prompt", "trace_id": trace_id}


async def try_handle_crm_add_telegram(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """The entry (link or words) and «✖️ Не добавлять». Anything else — None, the text goes on."""
    if msg.chat_type != "private" or not _available(tenant):
        return None
    if is_cancel_text(msg.text):
        await _say(msg.chat_id, CANCEL_TEXT, REMOVE_KEYBOARD)
        return {"ok": True, "route": ROUTE, "status": "cancelled", "trace_id": trace_id}
    if not is_entry_text(msg.text):
        return None
    return await _begin(tenant, chat_id=msg.chat_id, user_id=msg.user_id, trace_id=trace_id)


async def try_handle_crm_add_telegram_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    """The button «➕ Добавить из Telegram» under «Открыть WWC CRM»."""
    if callback.data != ADD_TG_CALLBACK:
        return None
    await answer_callback_query(
        callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token
    )
    if callback.chat_type != "private" or not _available(tenant):
        return {"ok": True, "route": ROUTE, "status": "unavailable", "trace_id": trace_id}
    return await _begin(tenant, chat_id=callback.chat_id, user_id=callback.user_id, trace_id=trace_id)


# ---- the answer: people from the picker, a forwarded contact -------------------------------


async def try_handle_crm_shared(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """A message with no text from ``parse_shared_message``. None — not for the CRM, the router ignores it."""
    if msg.chat_type != "private" or not _available(tenant):
        return None
    if msg.users_shared is not None:
        if msg.users_shared.request_id != REQUEST_ID:
            return None  # an answer to somebody else's button
        return await _people_from_picker(tenant, msg, trace_id=trace_id)
    if msg.shared_contact is not None:
        return await _forwarded_contact(tenant, msg, trace_id=trace_id)
    return None


async def _people_from_picker(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any]:
    try:
        viewer = await load_viewer(tenant.tenant_id, msg.user_id)
    except Exception as exc:
        logger.warning("crm_add_tg_viewer_unavailable", extra=safe_error(exc))
        return await _fail(msg.chat_id, trace_id)
    reason = lock_reason(viewer)
    if reason is not None:
        await _say(msg.chat_id, LOCK_TEXT[reason], lock_markup(reason))
        return {"ok": True, "route": ROUTE, "status": reason, "trace_id": trace_id}
    people = [_person(user) for user in msg.users_shared.users[:MAX_PEOPLE]]
    try:
        account = await get_or_create_account(tenant.tenant_id, msg.user_id)
        results = await add_telegram_contacts(tenant.tenant_id, account, people, source=SOURCE)
    except Exception as exc:
        logger.warning("crm_add_tg_failed", extra=safe_error(exc))
        return await _fail(msg.chat_id, trace_id)
    # The first message takes the keyboard away, the second brings the partner to the cards.
    await _say(msg.chat_id, summary_text(results), REMOVE_KEYBOARD)
    await _say(msg.chat_id, OPEN_CARDS_TEXT, await _open_markup(tenant, viewer, msg.user_id))
    created = sum(1 for item in results if item["status"] == "created")
    return {
        "ok": True, "route": ROUTE, "status": "added", "created": created, "existing": len(results) - created,
        "trace_id": trace_id,
    }


async def _forwarded_contact(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    contact = msg.shared_contact
    if contact.user_id is not None and contact.user_id == msg.user_id:
        return None  # the sender's own number: not a person to add, goes on as before
    try:
        viewer = await load_viewer(tenant.tenant_id, msg.user_id)
    except Exception as exc:
        logger.warning("crm_add_tg_viewer_unavailable", extra=safe_error(exc))
        return None  # nothing is known about this person's CRM: leave the message alone
    if lock_reason(viewer) is not None:
        return None  # a closed CRM intercepts nothing
    number = contact.phone_number
    person = {
        "name": " ".join(part for part in (contact.first_name, contact.last_name) if part).strip(),
        "phone": "+" + number if number[:1].isdigit() else number,  # Telegram sends digits without the plus
        "telegram_user_id": contact.user_id,
        "telegram_username": None,
    }
    try:
        account = await get_or_create_account(tenant.tenant_id, msg.user_id)
        results = await add_telegram_contacts(tenant.tenant_id, account, [person], source=SOURCE)
    except Exception as exc:
        logger.warning("crm_add_tg_failed", extra=safe_error(exc))
        return await _fail(msg.chat_id, trace_id)
    item = results[0]
    if item["status"] == "created":
        card = item["card"]
        shown = ", ".join(part for part in (_plain(card.get("name")), pretty_phone(card)) if part)
        text = f"Добавлено в WWC CRM: {shown}"
    else:
        text = EXISTS_UPDATED_TEXT if item["updated"] else EXISTS_TEXT
    await _say(msg.chat_id, text, await _open_markup(tenant, viewer, msg.user_id))
    return {"ok": True, "route": ROUTE, "status": item["status"], "updated": item["updated"], "trace_id": trace_id}
