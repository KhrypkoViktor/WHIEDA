"""Метка источника в ссылке в бота (V42, 30.10.2026).

Чат продвижения раздаёт ссылки по площадкам:

  Telegram  https://t.me/WHIEDA_Advisor_bot?start=src_<метка>[_<код партнёра>]
  Max       https://max.ru/<бот>?start=src_<метка>[_<код партнёра>]

Метка — строчные латинские буквы, цифры, дефис, до 32 знаков (``vk``, ``ok``, ``dzen``, ``max``,
``youtube``, ``instagram``). Подчёркивание в метке нельзя: первое «_» после ``src_`` отделяет код
партнёра, как в ``shop_<код>_<ref>`` на сайте (``startWithRef``, src/lib/bot-link.js). Весь параметр
start укладывается в 64 знака Telegram ``[A-Za-z0-9_-]``.

Старт с меткой — обычный /start плюс строка в ``bot_start_sources``. Незнакомый параметр без
``src_`` — тоже обычный /start со строкой ``tag = 'unknown'`` (само значение не хранится): ссылка
с опечаткой не должна заканчиваться «Не удалось обработать сообщение».

Код партнёра после метки понимается двумя способами: как код приглашения (то же, что ``ref_<код>``
в ссылках сайта) и как ``ref_code`` партнёра (то же, что в ``shop_<код>_<ref>``). Дальше — первое
касание партнёра как у ``ref_<код>``: пригласивший человеку, которого бот уже знал, не меняется.

Запись метки и атрибуция — «по возможности»: сбой любой из них пишется в журнал и не мешает /start.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.db import fetch_one, tenant_connection
from app.referral_bonus.service import _INVITE_CODE_RE, get_or_create_invite_code

logger = logging.getLogger("whieda.leads.start_sources")

SOURCE_START_PREFIX = "src_"
UNKNOWN_TAG = "unknown"
# До 32 знаков: вместе с «src_» и «_<код партнёра>» параметр start остаётся в пределах 64 знаков Telegram.
_TAG_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_PARTNER_PART_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

CHANNELS = ("telegram", "max")


@dataclass(frozen=True)
class SourceStart:
    tag: str
    partner_part: str = ""


@dataclass(frozen=True)
class SourcePartner:
    invite_code: str
    ref_code: str | None


@dataclass(frozen=True)
class StartSourceResult:
    tag: str
    first_seen: bool
    ref_code: str | None = None
    attribution: str | None = None
    recorded: bool = False


# Приглашение по коду: (код приглашения) -> объект со статусом (accept_referral_start / accept_channel_referral_start).
AcceptPartner = Callable[[str], Awaitable[Any]]
EnsureActor = Callable[[], Awaitable[Any]]


def unknown_source_start() -> SourceStart:
    """Параметр /start, которого бот не знает: пишем только факт, без значения."""
    return SourceStart(tag=UNKNOWN_TAG)


def parse_source_start_token(token: str) -> SourceStart | None:
    """``src_<метка>[_<код>]`` → SourceStart; None — не наш токен.

    Заглавные в метке приводятся к строчным. Кривая метка (пустая, длиннее 32 знаков, не латиница/цифры/дефис) —
    это тоже старт с меткой, но tag = 'unknown' и без кода партнёра: человек всё равно проходит /start.
    """
    raw = str(token or "").strip()
    if not raw.lower().startswith(SOURCE_START_PREFIX):
        return None
    tag_raw, _, rest = raw[len(SOURCE_START_PREFIX):].partition("_")
    tag = tag_raw.lower()
    if not _TAG_RE.fullmatch(tag):
        return unknown_source_start()
    partner_part = rest if _PARTNER_PART_RE.fullmatch(rest) else ""
    return SourceStart(tag=tag, partner_part=partner_part)


async def resolve_source_partner(tenant_id: str, partner_part: str) -> SourcePartner | None:
    """Хвост ссылки → партнёр: код приглашения (как в ref_<код>) или ref_code партнёра (как в shop_<код>_<ref>)."""
    part = str(partner_part or "")
    if not _PARTNER_PART_RE.fullmatch(part):
        return None
    async with tenant_connection(tenant_id) as conn:
        if _INVITE_CODE_RE.fullmatch(part):
            row = await fetch_one(
                conn,
                """
                select i.invite_code, rp.ref_code
                from referral_invite_codes i
                left join referral_profiles rp
                  on rp.tenant_id = i.tenant_id and rp.owner_id = i.inviter_actor_id and rp.enabled
                where i.tenant_id = %s and i.invite_code = %s and i.active = true
                order by (rp.public_profile->>'public_site_url') is null, rp.ref_code
                limit 1
                """,
                (tenant_id, part),
            )
            if row:
                return SourcePartner(invite_code=str(row["invite_code"]), ref_code=row["ref_code"])
        profile = await fetch_one(
            conn,
            """
            select ref_code, owner_id from referral_profiles
            where tenant_id = %s and lower(ref_code) = %s and enabled = true
            limit 1
            """,
            (tenant_id, part.lower()),
        )
        if not profile:
            return None
        invite_code = await get_or_create_invite_code(tenant_id, str(profile["owner_id"]), conn=conn)
        return SourcePartner(invite_code=invite_code, ref_code=str(profile["ref_code"]))


async def _find_actor_id(conn: Any, tenant_id: str, channel: str, channel_user_id: str) -> str | None:
    if channel == "telegram" and channel_user_id.isdigit():
        row = await fetch_one(
            conn,
            "select actor_id from lead_actors where tenant_id = %s and telegram_user_id = %s limit 1",
            (tenant_id, int(channel_user_id)),
        )
        if row:
            return str(row["actor_id"])
    row = await fetch_one(
        conn,
        """
        select actor_id from lead_actor_channels
        where tenant_id = %s and channel = %s and channel_user_id = %s
        limit 1
        """,
        (tenant_id, channel, channel_user_id),
    )
    return str(row["actor_id"]) if row else None


async def _known_before(tenant_id: str, channel: str, channel_user_id: str) -> bool:
    """Бот уже знал этого человека: есть actor или прежняя строка этой таблицы."""
    async with tenant_connection(tenant_id) as conn:
        if await _find_actor_id(conn, tenant_id, channel, channel_user_id):
            return True
        prior = await fetch_one(
            conn,
            """
            select 1 as seen from bot_start_sources
            where tenant_id = %s and channel = %s and channel_user_id = %s
            limit 1
            """,
            (tenant_id, channel, channel_user_id),
        )
    return prior is not None


async def register_start_source(
    tenant_id: str,
    *,
    channel: str,
    channel_user_id: int | str,
    source: SourceStart,
    accept_partner: AcceptPartner | None = None,
    ensure_actor: EnsureActor | None = None,
) -> StartSourceResult:
    """Записать старт с меткой; при годном коде партнёра — первое касание партнёра.

    Не бросает исключений: старт человека важнее записи. Порядок важен: «знал ли бот человека» —
    до атрибуции и до ensure_actor, которые его создают.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unsupported channel: {channel}")
    user = str(channel_user_id)

    try:
        known = await _known_before(tenant_id, channel, user)
    except Exception:
        # Не смогли проверить — не считаем человека новым: лучше недосчитать, чем завысить метку.
        logger.warning("start_source_known_check_failed", extra={"channel": channel}, exc_info=True)
        known = True

    ref_code: str | None = None
    attribution: str | None = None
    if source.partner_part and accept_partner is not None:
        try:
            partner = await resolve_source_partner(tenant_id, source.partner_part)
            if partner is not None:
                ref_code = partner.ref_code
                outcome = await accept_partner(partner.invite_code)
                attribution = str(getattr(outcome, "status", "") or "") or None
        except Exception:
            logger.warning("start_source_partner_failed", extra={"channel": channel, "tag": source.tag}, exc_info=True)

    if ensure_actor is not None:
        try:
            await ensure_actor()
        except Exception:
            logger.warning("start_source_ensure_actor_failed", extra={"channel": channel}, exc_info=True)

    first_seen = not known
    try:
        async with tenant_connection(tenant_id) as conn:
            actor_id = await _find_actor_id(conn, tenant_id, channel, user)
            await fetch_one(
                conn,
                """
                insert into bot_start_sources (tenant_id, channel, channel_user_id, actor_id, tag, ref_code, first_seen)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning start_id
                """,
                (tenant_id, channel, user, actor_id, source.tag, ref_code, first_seen),
            )
    except Exception:
        logger.warning("start_source_record_failed", extra={"channel": channel, "tag": source.tag}, exc_info=True)
        return StartSourceResult(tag=source.tag, first_seen=first_seen, ref_code=ref_code, attribution=attribution)
    logger.info(
        "start_source_recorded",
        extra={"channel": channel, "tag": source.tag, "first_seen": first_seen, "with_ref": bool(ref_code)},
    )
    return StartSourceResult(
        tag=source.tag, first_seen=first_seen, ref_code=ref_code, attribution=attribution, recorded=True
    )
