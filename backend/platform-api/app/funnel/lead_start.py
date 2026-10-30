"""Старт по ссылке заявки: ``?start=lead_<public_id>`` в Telegram и Max (срез 4 бот-воронки, 30.10.2026).

Страница «Живая встреча» после заявки показывает кнопки «Открыть в Telegram / в Max» со ссылкой
``t.me/WHIEDA_Advisor_bot?start=lead_<public_id>`` и ``max.ru/<бот>?start=lead_<public_id>``. Человек открывает
бота, и здесь:

  1. по ``public_id`` находится заявка (``kind = meeting``); нет такой — обычный старт, как у любого незнакомого параметра;
  2. ``mark_bot_opened``: мессенджер человека привязывается к заявке, в карточке CRM «бот не открыл» → «в боте»;
  3. партнёр заявки (``assigned_owner_id`` с включённым профилем; владелец «по умолчанию» не в счёт) закрепляется
     первым касанием — тем же кодом, что у ``ref_<код>``, только для ещё незнакомого боту человека;
  4. если в заявке выбрана встреча и она живая — человек записывается (``meeting_registrations``), метка
     «записан <дд.мм>» и заметка попадают в карточку;
  5. человек получает меню гостя: «Вы пришли от <партнёр>», подтверждение записи или ближайшие встречи
     и кнопки «Показ системы», «Личный разбор», «Задать вопрос».

Этот модуль — база и логика, без отправки сообщений: ``start_from_lead`` возвращает экран (``Reply``), а
отправляют его Telegram и Max в ``app/funnel/guest_bot.py``. В логи имён и телефонов не попадает.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.crm.service import safe_error
from app.db import tenant_connection
from app.funnel import texts
from app.funnel.guest import Reply, guest_menu_reply, load_guest_view, owner_card
from app.leads.meeting_leads import card_registration_event, load_lead_by_public_id, mark_bot_opened
from app.meetings import registrations
from app.meetings.service import utcnow
from app.referral_bonus.service import get_or_create_invite_code

logger = logging.getLogger("whieda.funnel.lead_start")

LEAD_START_PREFIX = "lead_"
# public_id заявки: «L-20261030-AB12CD3». Допускаем латиницу, цифры, «-» и «_»: весь параметр start — до 64 знаков.
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,58}$")

# Закрепление партнёра: код приглашения → объект со статусом (accept_referral_start / accept_channel_referral_start).
AcceptPartner = Callable[[str], Awaitable[Any]]


def parse_lead_start_token(token: str | None) -> str | None:
    """``lead_<public_id>`` → public_id; ``""`` — наш префикс, но сам id негодный; ``None`` — не наш параметр.

    Регистр id сохраняется (public_id заявки строится заглавными), префикс — без учёта регистра."""
    raw = str(token or "").strip()
    if not raw.lower().startswith(LEAD_START_PREFIX):
        return None
    public_id = raw[len(LEAD_START_PREFIX):]
    return public_id if _PUBLIC_ID_RE.fullmatch(public_id) else ""


@dataclass(frozen=True)
class LeadStartResult:
    """Итог старта по ссылке заявки. ``status``: ``registered`` — записали на встречу из заявки; ``already`` —
    человек уже был записан на неё; ``closed`` — встреча из заявки прошла, отменена или не годится; ``menu`` — в заявке
    встречи нет, показано меню гостя. ``attribution`` — итог закрепления партнёра (``attributed``,
    ``already_registered``, ``self_referral``, ``invalid``, ``error``) или None, если закреплять некого."""

    reply: Reply
    status: Literal["registered", "already", "closed", "menu"]
    attribution: str | None = None
    inviter_name: str | None = None


async def start_from_lead(
    tenant_id: str,
    *,
    channel: Literal["telegram", "max"],
    channel_user_id: int,
    public_id: str,
    accept_partner: AcceptPartner,
    now: datetime | None = None,
) -> LeadStartResult | None:
    """Делает шаги 1–5 из описания модуля. ``None`` — заявки нет (или она не на встречу): вызывающий ведёт
    обычный старт. ``accept_partner`` — закрепление партнёра в мессенджере человека (код приглашения → результат).

    Шаги идут короткими подключениями одно за другим: закрепление партнёра открывает своё подключение,
    а вложенные подключения держали бы пул."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        lead = await load_lead_by_public_id(conn, tenant_id, public_id)
    if lead is None:
        return None

    card = await owner_card(tenant_id, lead.assigned_owner_id)
    attribution: str | None = None
    if card is not None:
        try:
            invite_code = await get_or_create_invite_code(tenant_id, str(lead.assigned_owner_id))
            attribution = str(getattr(await accept_partner(invite_code), "status", "") or "") or None
        except Exception as exc:  # noqa: BLE001 — человек важнее закрепления: приветствие и запись всё равно нужны
            logger.warning("lead_start_attribution_failed", extra={"channel": channel, **safe_error(exc)})
            attribution = "error"

    # Отметка «открыл бота» — отдельной транзакцией: заметки карточки («открыл бота», затем «записался») получают
    # разное время и в ленте карточки идут по порядку.
    async with tenant_connection(tenant_id) as conn:
        opened = await mark_bot_opened(conn, tenant_id, lead.public_id, channel=channel, channel_user_id=channel_user_id)
    lead = opened or lead
    # Ссылку заявки могли переслать: запись привязываем к заявке (и к её карточке CRM) только у того,
    # кто открыл бота первым, — у остальных запись есть, а заявки в ней нет.
    recorded_user = lead.telegram_user_id if channel == "telegram" else lead.max_user_id
    is_lead_person = recorded_user == int(channel_user_id)

    outcome: registrations.RegisterOutcome | None = None
    async with tenant_connection(tenant_id) as conn:
        if lead.meeting_id is not None:
            outcome = await registrations.register(
                conn,
                tenant_id,
                meeting_id=lead.meeting_id,
                channel=channel,
                channel_user_id=channel_user_id,
                lead_id=lead.lead_id if is_lead_person else None,
                now=current,
            )
            if outcome.status == "registered" and is_lead_person and outcome.meeting is not None:
                await card_registration_event(
                    conn, tenant_id, lead.crm_card, "registered", starts_at=outcome.meeting["starts_at"], now=current
                )
        registration, open_meetings = await load_guest_view(conn, tenant_id, channel, channel_user_id, now=current)

    gone = outcome is not None and outcome.status in ("closed", "not_found")
    inviter_name = ((card.display_name if card else "") or "").strip() or None
    reply = guest_menu_reply(
        inviter_name,
        registration=registration,
        open_meetings=open_meetings,
        now=current,
        note=texts.GUEST_MEETING_GONE if gone else None,
    )
    status: Literal["registered", "already", "closed", "menu"] = "menu"
    if outcome is not None:
        status = "closed" if gone else outcome.status  # type: ignore[assignment]
    return LeadStartResult(reply=reply, status=status, attribution=attribution, inviter_name=inviter_name)
