"""The diary in the bot: the words «ежедневник», «crm», «мои контакты» and the
cabinet button. The diary itself lives on the site (/crm/); the bot only opens
it with a link that signs the person in on arrival (#wwc-login).

Nothing here breaks the bot: a lookup failure means «no button» / «not mine»,
and the text falls through to the usual handlers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.crm.service import crm_feature_enabled, crm_url, load_viewer, lock_reason, safe_error
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

CRM_BUTTON_LABEL = "📒 Ежедневник"
OPEN_BUTTON_LABEL = "📒 Открыть ежедневник"

_TEXT_RE = re.compile(
    r"^/?(?:crm|срм|ежедневник|мой\s+ежедневник|открыть\s+ежедневник|мои\s+контакты)\s*[.!]?\s*$",
    re.IGNORECASE,
)

OPEN_TEXT = (
    "📒 Ежедневник партнёра: кому позвонить сегодня, статусы и заметки по каждому человеку.\n\n"
    "Утром в 09:00 бот напомнит, с кем связаться, если на сегодня есть дела."
)
LOCK_TEXT = {
    "pro_required": "Ежедневник входит в PRO — платформу вашего сайта. Продлите PRO, и он откроется сразу.",
    "crm_pilot_only": (
        "Ежедневник сейчас проверяют несколько партнёров. "
        "После проверки откроем его всем с PRO."
    ),
}


def is_crm_text(text: str) -> bool:
    return bool(_TEXT_RE.match(str(text or "").strip()))


def _available(tenant: TenantContext) -> bool:
    return crm_feature_enabled() and bool(tenant.entitlements.get("crm"))


async def crm_button_rows(tenant: TenantContext, telegram_user_id: int | None) -> list[list[dict[str, Any]]]:
    """The cabinet row «📒 Ежедневник» — only for those who can open it."""
    if telegram_user_id is None or not _available(tenant):
        return []
    try:
        viewer = await load_viewer(tenant.tenant_id, int(telegram_user_id))
        if lock_reason(viewer) is not None:
            return []
        url = await with_site_login(crm_url(viewer), tenant_id=tenant.tenant_id, telegram_user_id=int(telegram_user_id))
    except Exception as exc:  # the cabinet must open even if the diary lookup fails
        logger.warning("crm_button_unavailable", extra=safe_error(exc))
        return []
    return [[{"text": CRM_BUTTON_LABEL, "url": url}]]


async def try_handle_crm_text(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    if msg.chat_type != "private" or not is_crm_text(msg.text or "") or not _available(tenant):
        return None
    try:
        viewer = await load_viewer(tenant.tenant_id, msg.user_id)
    except Exception as exc:
        logger.warning("crm_viewer_unavailable", extra=safe_error(exc))
        return None
    binding = current_bot_binding()
    reason = lock_reason(viewer)
    if reason is not None:
        keyboard = None
        if reason == "pro_required":
            keyboard = {"inline_keyboard": [[{"text": "Продлить платформу", "callback_data": "renew:start"}]]}
        await send_telegram_text(
            chat_id=str(msg.chat_id), text=LOCK_TEXT[reason], bot_token=binding.bot_token, reply_markup=keyboard
        )
        return {"ok": True, "route": "crm_text", "status": reason, "trace_id": trace_id}
    url = await with_site_login(crm_url(viewer), tenant_id=tenant.tenant_id, telegram_user_id=msg.user_id)
    await send_telegram_text(
        chat_id=str(msg.chat_id),
        text=OPEN_TEXT,
        bot_token=binding.bot_token,
        reply_markup={"inline_keyboard": [[{"text": OPEN_BUTTON_LABEL, "url": url}]]},
    )
    return {"ok": True, "route": "crm_text", "status": "open", "trace_id": trace_id}
