"""Consents in the bot: privacy notice on /start (152-ФЗ) and the marketing opt-in (38-ФЗ).

Privacy notice: shown once per policy version and the fact is stored in
``telegram_consents``; a storage failure only skips the notice for this turn and
never blocks the reply.

Marketing opt-in (ФЗ «О рекламе» ст. 18, owner's decision 26.09.2026): a separate,
optional question — «Хочу получать новости и предложения» — asked on /start until
the person answers once (yes or no, both are recorded in
``telegram_marketing_consents``), plus ``/news``, ``/news_on``, ``/news_off``.
Broadcasts (n8n «WHIEDA Broadcast Delivery Worker») read ``opted_in`` only; a
consultant's personal reply to a lead is not a broadcast and needs no opt-in.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.db import fetch_one, tenant_connection
from app.tenancy import TenantContext
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.log_safe import chat_ref
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage

logger = logging.getLogger("platform.telegram.consent")

CONSENT_POLICY_VERSION = "2026-09-18"
MARKETING_CONSENT_VERSION = "2026-09-26"

# Tenants without a published policy get no notice, no marketing question, no record.
PRIVACY_POLICY_URLS: dict[str, str] = {
    "whieda": "https://wwc.best/privacy-policy/",
}

MARKETING_CALLBACK_PREFIX = "consent:news:"
MARKETING_YES = MARKETING_CALLBACK_PREFIX + "yes"
MARKETING_NO = MARKETING_CALLBACK_PREFIX + "no"

MARKETING_PROMPT = (
    "Хотите получать новости и предложения клуба в этом чате?\n\n"
    "Это необязательно: без согласия бот отвечает на вопросы как обычно, "
    "а консультант ответит на вашу заявку лично.\n"
    "Отписаться можно в любой момент: /news_off."
)
MARKETING_ON_TEXT = "✅ Спасибо! Будем присылать новости и предложения. Отписаться: /news_off."
MARKETING_OFF_TEXT = "Хорошо, без рассылки. Передумаете — /news_on."
MARKETING_STORAGE_FAILED_TEXT = "Не получилось сохранить ответ. Попробуйте ещё раз чуть позже."

_START_RE = re.compile(r"^/start(?:@\w+)?(?:\s|$)", re.I)
_NEWS_RE = re.compile(r"^/news(?:_(on|off))?(?:@\w+)?\s*$", re.I)
_NEWS_WORD_RE = re.compile(r"^(?:рассылка|рассылки|новости)\s*[.!?]*$", re.I)

_RECORD_SQL = """
insert into telegram_consents (
    tenant_id, telegram_user_id, telegram_chat_id, policy_version
) values (
    %(tenant_id)s, %(user_id)s, %(chat_id)s, %(version)s
)
on conflict (tenant_id, telegram_user_id, policy_version) do nothing
returning consented_at
"""

_MARKETING_STATE_SQL = """
select opted_in, opted_in_at, opted_out_at, consent_version
from telegram_marketing_consents
where tenant_id = %(tenant_id)s and telegram_user_id = %(user_id)s
"""

# One row per person; «no» is recorded too — the question is asked only until
# the first answer, and the dates prove who said what and when.
_MARKETING_RECORD_SQL = """
insert into telegram_marketing_consents (
    tenant_id, telegram_user_id, telegram_chat_id, opted_in, consent_version, source,
    opted_in_at, opted_out_at
) values (
    %(tenant_id)s, %(user_id)s, %(chat_id)s, %(opted_in)s, %(version)s, %(source)s,
    case when %(opted_in)s then now() else null end,
    case when %(opted_in)s then null else now() end
)
on conflict (tenant_id, telegram_user_id) do update set
    telegram_chat_id = excluded.telegram_chat_id,
    opted_in = excluded.opted_in,
    consent_version = excluded.consent_version,
    source = excluded.source,
    opted_in_at = case when excluded.opted_in then now()
                       else telegram_marketing_consents.opted_in_at end,
    opted_out_at = case when excluded.opted_in then telegram_marketing_consents.opted_out_at
                        else now() end,
    updated_at = now()
returning opted_in, opted_in_at, opted_out_at
"""


def is_start_command(text: str | None) -> bool:
    return bool(_START_RE.match(str(text or "").strip()))


def consent_notice(tenant_id: str) -> str | None:
    url = PRIVACY_POLICY_URLS.get(tenant_id)
    if not url:
        return None
    return (
        "Продолжая общение с ботом, вы соглашаетесь с политикой обработки "
        f"персональных данных: {url}"
    )


async def record_telegram_consent(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    policy_version: str = CONSENT_POLICY_VERSION,
) -> bool:
    """Return True when this is the first record for the user and policy version."""
    params = {
        "tenant_id": tenant_id,
        "user_id": int(telegram_user_id),
        "chat_id": int(telegram_chat_id),
        "version": policy_version,
    }
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _RECORD_SQL, params)
    return row is not None


async def first_start_consent_notice(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    trace_id: str = "",
) -> str | None:
    """Notice text to send on this /start, or None (already shown, no policy, or storage failed)."""
    notice = consent_notice(tenant_id)
    if not notice:
        return None
    try:
        is_new = await record_telegram_consent(
            tenant_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
        )
    except Exception:
        logger.warning(
            "telegram_consent_record_failed",
            extra={"trace_id": trace_id, "chat_id": chat_ref(telegram_chat_id)},
            exc_info=True,
        )
        return None
    return notice if is_new else None


# --- marketing opt-in --------------------------------------------------------


def marketing_consent_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "✅ Хочу получать новости и предложения", "callback_data": MARKETING_YES}],
            [{"text": "Не сейчас", "callback_data": MARKETING_NO}],
        ]
    }


def marketing_command(text: str | None) -> str | None:
    """``/news`` or the word «рассылка» → 'prompt'; ``/news_on`` → 'on'; ``/news_off`` → 'off'."""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _NEWS_RE.match(raw)
    if match:
        return match.group(1).lower() if match.group(1) else "prompt"
    if _NEWS_WORD_RE.match(raw):
        return "prompt"
    return None


async def marketing_consent_state(tenant_id: str, telegram_user_id: int) -> bool | None:
    """True/False — the person's current answer; None — never asked or never answered."""
    params = {"tenant_id": tenant_id, "user_id": int(telegram_user_id)}
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _MARKETING_STATE_SQL, params)
    if row is None:
        return None
    return bool(row["opted_in"])


async def record_marketing_consent(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    opted_in: bool,
    source: str = "bot",
    consent_version: str = MARKETING_CONSENT_VERSION,
) -> dict[str, Any] | None:
    params = {
        "tenant_id": tenant_id,
        "user_id": int(telegram_user_id),
        "chat_id": int(telegram_chat_id),
        "opted_in": bool(opted_in),
        "version": consent_version,
        "source": source,
    }
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(conn, _MARKETING_RECORD_SQL, params)


async def _send(chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    binding = current_bot_binding()
    await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=binding.bot_token, reply_markup=reply_markup
    )


async def send_marketing_prompt(chat_id: int) -> None:
    await _send(chat_id, MARKETING_PROMPT, marketing_consent_keyboard())


async def offer_marketing_consent(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    trace_id: str = "",
) -> bool:
    """On /start: ask once — until the person has answered (yes or no). Never blocks."""
    if not PRIVACY_POLICY_URLS.get(tenant_id):
        return False
    try:
        state = await marketing_consent_state(tenant_id, telegram_user_id)
        if state is not None:
            return False
        await send_marketing_prompt(telegram_chat_id)
    except Exception:
        logger.warning(
            "telegram_marketing_consent_offer_failed",
            extra={"trace_id": trace_id, "chat_id": chat_ref(telegram_chat_id)},
            exc_info=True,
        )
        return False
    return True


async def _apply_marketing_answer(
    tenant_id: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    opted_in: bool,
    trace_id: str,
) -> dict[str, Any]:
    try:
        await record_marketing_consent(
            tenant_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            opted_in=opted_in,
        )
    except Exception:
        logger.warning(
            "telegram_marketing_consent_record_failed",
            extra={"trace_id": trace_id, "chat_id": chat_ref(telegram_chat_id)},
            exc_info=True,
        )
        await _send(telegram_chat_id, MARKETING_STORAGE_FAILED_TEXT)
        return {"ok": False, "route": "marketing_consent", "status": "storage_failed", "trace_id": trace_id}
    await _send(telegram_chat_id, MARKETING_ON_TEXT if opted_in else MARKETING_OFF_TEXT)
    return {
        "ok": True,
        "route": "marketing_consent",
        "status": "opted_in" if opted_in else "opted_out",
        "trace_id": trace_id,
    }


async def try_handle_marketing_consent_message(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    command = marketing_command(msg.text)
    if command is None:
        return None
    if msg.chat_type != "private":
        return {"ok": True, "route": "marketing_consent", "status": "private_chat_required", "trace_id": trace_id}
    if command == "prompt":
        await send_marketing_prompt(msg.chat_id)
        return {"ok": True, "route": "marketing_consent", "status": "prompted", "trace_id": trace_id}
    return await _apply_marketing_answer(
        tenant.tenant_id,
        telegram_user_id=msg.user_id,
        telegram_chat_id=msg.chat_id,
        opted_in=command == "on",
        trace_id=trace_id,
    )


async def try_handle_marketing_consent_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    if callback.data not in (MARKETING_YES, MARKETING_NO):
        return None
    binding = current_bot_binding()
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=binding.bot_token)
    if callback.chat_type != "private":
        return {"ok": True, "route": "marketing_consent", "status": "private_chat_required", "trace_id": trace_id}
    return await _apply_marketing_answer(
        tenant.tenant_id,
        telegram_user_id=callback.user_id,
        telegram_chat_id=callback.chat_id,
        opted_in=callback.data == MARKETING_YES,
        trace_id=trace_id,
    )
