"""«Своя сеть»: кому бот WWC предлагает живые встречи и «Личный разбор» (владелец, 11.11.2026).

Платформа общая для всех структур WHIEDA, но наши встречи (среда 19:00 МСК, ведут Виктор Хрипко и Елена Дацкевич) и
разбор с ними — только для гостей своей сети. Гость чужого партнёра встреч и разбора не видит: бот говорит, что партнёр
свяжется с ним, и оставляет «Задать вопрос».

Признак — в одном месте, здесь:
  * ``referral_profiles.public_profile->>'own_network' = 'true'`` (булево в JSON; сайт пишет его штатным
    ``partner_profile_to_core``) — у любого включённого профиля владельца ``owner_id`` или кода ``ref_code``;
  * запасной список ``PLATFORM_OWN_NETWORK_REFS`` (через запятую: ref_code или owner_id), по умолчанию пустой;
  * гость без партнёра (главная wwc.best, голый /start, метка ``src_`` без партнёра, владелец «по умолчанию»
    ``PLATFORM_ORGANIC_OWNER_ID``, нет включённого профиля) — своя сеть: это наш основной вход.

Сбой базы при проверке гостя на кнопках записи и разбора — «закрыто» (гостю говорим «партнёр свяжется»): лучше не показать
встречу, чем предложить её чужому гостю.
"""

from __future__ import annotations

import logging
from typing import Any

from app.bot_stages.service import STAGE_GUEST, STAGE_PARTNER_WHIEDA, inviter_actor_id, resolve_stage, stages_enabled
from app.crm.service import safe_error
from app.db import fetch_one, tenant_connection
from app.funnel import texts
from app.funnel.guest import Reply, resolve_guest
from app.settings import get_settings

logger = logging.getLogger("whieda.funnel.network")

_PROFILE_SQL = """
select bool_or(lower(coalesce(public_profile->>'own_network', '')) = 'true') as own_network,
       count(*) as profiles
from referral_profiles
where tenant_id = %(tenant)s and enabled
  and (owner_id = %(owner)s or lower(ref_code) = lower(%(ref)s))
"""


async def is_own_network(
    conn: Any, tenant_id: str, *, ref_code: str | None = None, owner_id: str | None = None
) -> bool:
    """Партнёр (по ``ref_code`` или ``owner_id``) из своей сети? Ни кода, ни владельца — это гость без партнёра: своя сеть.

    Нет включённого профиля у такого кода/владельца — партнёра для гостя нет (экран «Вы в WWC»): тоже своя сеть.
    Включённый профиль без ``own_network`` — чужой партнёр. Сбой запроса — исключение: вызывающий решает сам."""
    ref = str(ref_code or "").strip()
    owner = str(owner_id or "").strip()
    if not ref and not owner:
        return True
    settings = get_settings()
    if owner and owner == str(settings.platform_organic_owner_id or ""):
        return True
    listed = settings.parsed_own_network_refs()
    if (ref and ref.lower() in listed) or (owner and owner.lower() in listed):
        return True
    row = await fetch_one(conn, _PROFILE_SQL, {"tenant": tenant_id, "owner": owner or None, "ref": ref or None})
    if not row or not int(row.get("profiles") or 0):
        return True
    return bool(row.get("own_network"))


async def guest_inviter_owner(conn: Any, tenant_id: str, channel: str, channel_user_id: int, lead_owner: str | None) -> str | None:
    """Партнёр гостя: владелец его заявки, иначе тот, чья ссылка закрепила человека в боте."""
    return lead_owner or await inviter_actor_id(conn, tenant_id, channel, channel_user_id)


async def offers_blocked_for(tenant_id: str, channel: str, channel_user_id: int) -> bool:
    """Одна проверка для гостей и партнёров WHIEDA (11.11.2026): гостю и партнёру WHIEDA, пригласивший которого не из
    своей сети, встречи и «Личный разбор» не предлагаются (кнопки, ответы на старые кнопки). Нет пригласившего — своя сеть.
    Партнёры WWC (свой кабинет) и все остальные — ``False``: их меню не меняется. Сбой базы — ``True`` (см. модуль)."""
    try:
        async with tenant_connection(tenant_id) as conn:
            stage: str | None = None
            if stages_enabled(tenant_id):
                stage = await resolve_stage(conn, tenant_id, channel, channel_user_id)
                if stage == STAGE_PARTNER_WHIEDA:
                    owner = await inviter_actor_id(conn, tenant_id, channel, channel_user_id)
                    return not await is_own_network(conn, tenant_id, owner_id=owner)
                if stage != STAGE_GUEST:
                    return False
            guest = await resolve_guest(conn, tenant_id, channel, channel_user_id, stage=stage)
            if guest is None:
                return False
            owner = await guest_inviter_owner(conn, tenant_id, channel, channel_user_id, guest.owner_actor_id)
            return not await is_own_network(conn, tenant_id, owner_id=owner)
    except Exception as exc:  # noqa: BLE001
        logger.warning("own_network_check_failed", extra={"channel": channel, **safe_error(exc)})
        return True


guest_offers_blocked = offers_blocked_for  # прежнее имя: им пользуются обработчики гостя


def partner_contact_reply() -> Reply:
    """Ответ гостю чужого партнёра на кнопку встречи или разбора (в том числе старую): партнёр свяжется, есть «Задать вопрос»."""
    ask = texts.STAGE_ASK_BUTTON if _stages() else texts.GUEST_ASK_BUTTON
    return Reply(texts.GUEST_PARTNER_WILL_CONTACT, [[(ask, texts.FUNNEL_ASK_CALLBACK)]])


def _stages() -> bool:
    from app.bot_stages.service import stages_enabled

    return stages_enabled()
