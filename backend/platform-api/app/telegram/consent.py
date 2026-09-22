"""Privacy-policy notice on the first /start, recorded per user (152-ФЗ, ст. 9).

The notice is shown once per policy version and the fact is stored in
``telegram_consents``; a storage failure only skips the notice for this turn and
never blocks the reply.
"""

from __future__ import annotations

import logging
import re

from app.db import fetch_one, tenant_connection
from app.telegram.log_safe import chat_ref

logger = logging.getLogger("platform.telegram.consent")

CONSENT_POLICY_VERSION = "2026-09-18"

# Tenants without a published policy get no notice and no record.
PRIVACY_POLICY_URLS: dict[str, str] = {
    "whieda": "https://wwc.best/privacy-policy/",
}

_START_RE = re.compile(r"^/start(?:@\w+)?(?:\s|$)", re.I)

_RECORD_SQL = """
insert into telegram_consents (
    tenant_id, telegram_user_id, telegram_chat_id, policy_version
) values (
    %(tenant_id)s, %(user_id)s, %(chat_id)s, %(version)s
)
on conflict (tenant_id, telegram_user_id, policy_version) do nothing
returning consented_at
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
