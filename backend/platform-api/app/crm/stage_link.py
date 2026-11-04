"""WWC CRM → бот: карточка перешла на этап «Партнёр» — ведущий подтверждает, что человек стал партнёром WHIEDA (04.11.2026).

Когда партнёр в WWC CRM переводит карточку на этап «Партнёр» (PATCH ``status`` или «Сделано» с таким итогом), а у карточки
есть человек в боте — заявка с сайта, встреча или личный разбор, где человек открыл бота по ссылке заявки
(``website_leads.telegram_user_id`` / ``max_user_id``, их ставит ``mark_bot_opened``), — ведущему (хозяину карточки) в бот
уходит вопрос «{Имя} стал партнёром WHIEDA — подтвердить?» с кнопками «Да» / «Нет». «Да» ставит человеку статус
``partner_whieda`` (``app/bot_stages/confirm.py``), «Нет» ничего не меняет.

Вопрос — строка ``platform_outbox`` (событие ``bot_stage_confirm_asked``), её отправляет боевой воркер; на стенде без
``PLATFORM_SCHEDULED_NOTIFY_BINDINGS`` ничего не уходит. Ключ идемпотентности — карточка: вопрос по ней задаётся один раз,
повторный перевод на «Партнёр» и повторное нажатие ничего не дублируют. Карточка без человека в боте — ничего не
отправляется: вопрос будет задан, когда человек откроет бота по ссылке партнёра (``check_partner_cards`` после
``/start lead_…`` и ``ref_…``).

Сбой здесь карточку не ломает: хук работает под своей точкой отката, а ``check_partner_cards`` проглатывает ошибки.
В логи имён не попадает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.bot_stages.service import STAGE_GUEST, resolve_stage, stages_enabled, user_ref
from app.crm.service import safe_error
from app.db import fetch_all, fetch_one, tenant_connection
from app.funnel import texts
from app.funnel.outbound import enqueue_guest_message
from app.leads.meeting_leads import notify_binding_id, plain_no_links
from app.meetings.service import utcnow

logger = logging.getLogger("whieda.bot_stages.crm")

EVENT_TYPE = "bot_stage_confirm_asked"
PARTNER_STATUS = "partner"
NAME_MAX = 80

# Карточка, хозяин карточки (аккаунт CRM — его id в Telegram) и человек в боте по заявке карточки.
_CARD_SQL = """
select c.contact_id::text as contact_id, c.name, c.status, c.deleted_at is not null as deleted,
       a.telegram_user_id as host_user_id,
       wl.telegram_user_id as person_telegram, wl.max_user_id as person_max
from crm_contacts c
join platform_accounts a on a.tenant_id = c.tenant_id and a.account_id = c.account_id
left join website_leads wl on wl.tenant_id = c.tenant_id and wl.lead_id = c.lead_id and wl.deleted_at is null
where c.tenant_id = %s and c.contact_id = %s::uuid
"""

# Карточки на этапе «Партнёр», связанные с этим человеком в боте.
_CARDS_OF_PERSON_SQL = """
select c.contact_id::text as contact_id
from website_leads wl
join crm_contacts c on c.tenant_id = wl.tenant_id and c.lead_id = wl.lead_id
where wl.tenant_id = %(tenant)s and wl.deleted_at is null
  and (case %(channel)s::text when 'telegram' then wl.telegram_user_id else wl.max_user_id end) = %(user)s
  and c.status = 'partner' and c.deleted_at is null
order by c.updated_at desc
limit 5
"""


@dataclass(frozen=True)
class CardInfo:
    """Карточка для вопроса и подтверждения: ``persons`` — человек (или два) в мессенджерах, привязанный к ней заявкой."""

    contact_id: str
    name: str
    status: str
    host_user_id: int | None
    persons: tuple[tuple[str, int], ...]


def card_token(contact_id: str) -> str:
    """Карточка в данных кнопки: uuid без дефисов (32 знака; ``stg:y:`` + 32 влезает в 64 байта callback_data)."""
    return str(contact_id).replace("-", "")


def card_id_from_token(token: str) -> str:
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def _card_from_row(row: dict[str, Any]) -> CardInfo | None:
    if row.get("deleted"):
        return None
    persons: list[tuple[str, int]] = []
    if row.get("person_telegram") is not None:
        persons.append(("telegram", int(row["person_telegram"])))
    if row.get("person_max") is not None:
        persons.append(("max", int(row["person_max"])))
    host = row.get("host_user_id")
    return CardInfo(
        contact_id=str(row["contact_id"]),
        name=plain_no_links(row.get("name"), NAME_MAX),
        status=str(row.get("status") or ""),
        host_user_id=int(host) if host is not None else None,
        persons=tuple(persons),
    )


async def load_card(conn: Any, tenant_id: str, contact_id: str) -> CardInfo | None:
    row = await fetch_one(conn, _CARD_SQL, (tenant_id, contact_id))
    return _card_from_row(row) if row else None


async def load_card_for_confirm(tenant_id: str, contact_id: str) -> CardInfo | None:
    """Карточка по кнопке «Да» / «Нет». Нет такой, удалена или id негодный — None."""
    try:
        async with tenant_connection(tenant_id) as conn:
            return await load_card(conn, tenant_id, contact_id)
    except Exception as exc:  # noqa: BLE001 — устаревшая или кривая кнопка не должна падать с ошибкой
        logger.warning("bot_stage_card_load_failed", extra=safe_error(exc))
        return None


async def ask_host(conn: Any, tenant_id: str, contact_id: str) -> str:
    """Вопрос ведущему про эту карточку: ``queued`` — поставлен впервые; ``duplicate`` — по карточке уже спрашивали;
    ``gone`` — карточки нет или она уже не на этапе «Партнёр»; ``no_bot`` — человека карточки нет в боте;
    ``already`` — все такие люди уже партнёры; ``no_host`` — у карточки нет хозяина в Telegram;
    ``no_binding`` — в процессе нет бота плановых сообщений (стенд)."""
    card = await load_card(conn, tenant_id, contact_id)
    if card is None or card.status != PARTNER_STATUS:
        return "gone"
    if not card.persons:
        return "no_bot"
    pending = []
    for channel, user_id in card.persons:
        if await resolve_stage(conn, tenant_id, channel, user_id) == STAGE_GUEST:
            pending.append((channel, user_id))
    if not pending:
        return "already"
    if card.host_user_id is None:
        return "no_host"
    binding_id = await notify_binding_id(conn, tenant_id)
    if not binding_id:
        return "no_binding"
    token = card_token(card.contact_id)
    row = await enqueue_guest_message(
        conn,
        tenant_id=tenant_id,
        binding_id=binding_id,
        channel="telegram",
        user_id=int(card.host_user_id),
        idempotency_key=f"bot_stage_ask:{card.contact_id}",
        event_type=EVENT_TYPE,
        text=texts.STAGE_CONFIRM_ASK.format(name=card.name or "Человек"),
        rows=[[
            (texts.STAGE_CONFIRM_YES_BUTTON, f"{texts.STAGE_CONFIRM_CALLBACK_PREFIX}{token}"),
            (texts.STAGE_CONFIRM_NO_BUTTON, f"{texts.STAGE_DECLINE_CALLBACK_PREFIX}{token}"),
        ]],
        due_at=utcnow(),
    )
    logger.info("bot_stage_confirm_asked", extra={"host_ref": user_ref(card.host_user_id), "created": bool(row and row.get("created"))})
    return "queued" if row and row.get("created") else "duplicate"


async def on_card_partner(conn: Any, tenant_id: str, contact_id: str) -> str:
    """Хук ``update_contact`` / ``done_contact``: карточка только что перешла на этап «Партнёр». Своя точка отката:
    любой сбой — в лог и ``error``, изменение карточки уже сделано и не отменяется."""
    if not stages_enabled(tenant_id):
        return "off"
    try:
        async with conn.transaction():
            return await ask_host(conn, tenant_id, contact_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_stage_card_hook_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        return "error"


async def check_partner_cards(tenant_id: str, channel: str, user_id: int) -> int:
    """Человек только что открыл бота по ссылке партнёра (``/start lead_…`` и ``ref_…``): карточки на этапе «Партнёр»,
    привязанные к нему, были переведены раньше, когда спрашивать было некого, — ведущему уходит вопрос. Возвращает,
    сколько вопросов поставлено; сбой ничего не ломает."""
    if not stages_enabled(tenant_id) or channel not in ("telegram", "max"):
        return 0
    try:
        async with tenant_connection(tenant_id) as conn:
            rows = await fetch_all(conn, _CARDS_OF_PERSON_SQL, {"tenant": tenant_id, "channel": channel, "user": int(user_id)})
            asked = 0
            for row in rows:
                async with conn.transaction():
                    if await ask_host(conn, tenant_id, row["contact_id"]) == "queued":
                        asked += 1
        return asked
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_stage_card_check_failed", extra={"channel": channel, **safe_error(exc)})
        return 0
