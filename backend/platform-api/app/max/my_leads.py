"""«Мои заявки» в боте Max (17.10.2026): партнёр пишет слово — бот отвечает списком заявок с сайта.

Правила Max (п. 1.5) запрещают боту самому слать сервисные сообщения без договора, поэтому
здесь только ответ на действие человека: «тяни, не толкай». Никаких уведомлений и рассылок.

Что показываем — то, что партнёр и так видит в своей WWC CRM (вкладка «Люди», карточки с
сайта). Человека находим по Max ``user_id`` → ``telegram_user_id`` (как вход на сайт через Max,
``app.max.webapp``), CRM-аккаунт берётся строго по этому ``telegram_user_id``. Доступ — те же
проверки, что у слова «CRM» в Telegram-боте: функция включена, право ``crm`` у тенанта, PRO/пилот.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.crm.bot import LOCK_TEXT, _available as crm_available
from app.crm.service import lock_reason, load_viewer, recent_site_leads, safe_error
from app.db import tenant_connection
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.max.leads")

LEADS_LIMIT = 10
CRM_LINK = "wwc.best/crm/"
NAME_MAX = 40
CONTACT_MAX = 40

# «Мои заявки», «заявки», «/leads» — без учёта регистра и знаков препинания (в том числе кавычек).
_COMMANDS = frozenset({"мои заявки", "заявки", "/заявки", "leads", "/leads", "my leads", "/my_leads"})
_PUNCTUATION_RE = re.compile(r"[^\w/\s]+")

NOT_LINKED_TEXT = (
    "Пока не вижу ваш аккаунт. Войдите на сайт через Max один раз: "
    "wwc.best → «Войти» → «Не получается через Telegram?» → «Войти через Max». "
    "После этого заявки будут здесь."
)
EMPTY_TEXT = f"Заявок пока нет. Все заявки с сайта попадают в WWC CRM: {CRM_LINK}"
NO_DIARY_TEXT = (
    "Заявок пока нет. Откройте WWC CRM один раз — и все заявки с сайта начнут попадать туда: "
    f"{CRM_LINK}"
)
FOOTER = f"Все заявки и заметки: {CRM_LINK}"
FAILED_TEXT = f"Не получилось загрузить заявки. Попробуйте чуть позже или откройте {CRM_LINK}"


def is_my_leads_text(text: str) -> bool:
    cleaned = _PUNCTUATION_RE.sub(" ", str(text or "").lower().replace("ё", "е"))
    return " ".join(cleaned.split()) in _COMMANDS


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_leads(result: dict[str, Any]) -> str:
    """«17.10 · Имя · телефон/контакт · статус», новые сверху; в конце ссылка на CRM."""
    lines = []
    for item in result["items"]:
        reach = item.get("phone") or item.get("note_contact") or ""
        parts = [
            item["requested_on"].strftime("%d.%m"),
            _short(item.get("name"), NAME_MAX) or "Без имени",
            _short(reach, CONTACT_MAX),
            item.get("status_title") or "",
        ]
        lines.append(" · ".join(part for part in parts if part))
    head = f"Последние {len(lines)} заявок с сайта:" if result.get("more") else "Заявки с сайта:"
    return "\n".join([head, *lines, "", FOOTER])


async def _telegram_user_id(tenant_id: str, max_user_id: int) -> int | None:
    # Тот же поиск, что у входа на сайт через Max: lead_actor_channels(max) → lead_actors.telegram_user_id.
    from app.max.webapp import _telegram_id_for_max

    async with tenant_connection(tenant_id) as conn:
        return await _telegram_id_for_max(conn, tenant_id, max_user_id)


async def my_leads_answer(tenant: TenantContext, max_user_id: int) -> tuple[str, str]:
    """(текст ответа, статус для лога/ответа вебхука). Не бросает: сбой базы — вежливый текст."""
    if not crm_available(tenant):
        return LOCK_TEXT["crm_pilot_only"], "crm_unavailable"
    try:
        telegram_user_id = await _telegram_user_id(tenant.tenant_id, max_user_id)
        if telegram_user_id is None:
            return NOT_LINKED_TEXT, "not_linked"
        reason = lock_reason(await load_viewer(tenant.tenant_id, telegram_user_id))
        if reason is not None:
            return LOCK_TEXT[reason], reason
        result = await recent_site_leads(tenant.tenant_id, telegram_user_id, limit=LEADS_LIMIT)
    except Exception as exc:  # без ответа человек решит, что бот молчит; текст заявок в лог не пишем
        logger.warning("max_my_leads_failed", extra=safe_error(exc))
        return FAILED_TEXT, "error"
    if result is None:
        return NO_DIARY_TEXT, "no_diary"
    if not result["items"]:
        return EMPTY_TEXT, "empty"
    return format_leads(result), "ok"
