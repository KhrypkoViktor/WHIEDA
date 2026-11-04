"""Статус человека в боте WWC: гость, партнёр WHIEDA, партнёр WWC (V49, 04.11.2026).

Три экрана бота вместо профиля ``PLATFORM_TELEGRAM_UI_PROFILE``: что человек видит на «/start», решает его статус.

  ``guest``           пришёл с рекламы, по голому «/start», по ссылке заявки или партнёра, пока его никто не повысил;
  ``partner_whieda``  ведущий подтвердил его в WWC CRM (``app/crm/stage_link.py``) или владелец написал «партнёр @ник»;
  ``partner_wwc``     у него есть профиль сайта ``referral_profiles`` (включённый или нет) либо оплаченная заявка на сайт,
                      которая ещё не активирована, — меню кабинета с бонусами и ссылкой-приглашением.

Хранится в ``lead_actors.bot_stage`` (V49). Но статус партнёра WWC бот берёт и из самих профилей и заявок — не ждёт, пока
поле догонит их. Профиль — любой, не только включённый: заявка на сайт создаёт его выключенным (``enabled = false``) до
активации, профиль из синхронизации Partners_Ref тоже бывает выключен, а бонусы и ссылку человек терять не должен.
Заявка — в статусе ``pending_confirmation`` (чек отправлен) или ``pending_provisioning`` (оплата подтверждена); анкета,
не дошедшая до оплаты (``awaiting_*``), отклонённая и отменённая заявки человека гостем не делают партнёром.
Поле ``bot_stage`` подтягивается к ``partner_wwc`` только по включённому профилю и вниз не пишется: выключенный профиль
или заявка в очереди статус показывают, но в поле не оседают (отклонят заявку — человек снова гость).
Владелец и админы бота — как ``partner_wwc``. Статус никогда не понижается сам: подтверждение ведущего не отбирает
у партнёра WWC его кабинет; снять статус «партнёр WHIEDA» может только владелец словом «партнёр снять @ник».

Человек в мессенджере — один или несколько actor (Telegram — колонки ``lead_actors``, Max — ``lead_actor_channels``,
привязанная учётка — ``account_identities``): статус — высший из них. Выключатель — ``PLATFORM_BOT_STAGES`` (False — всё
как прежде, с профилем env) и только для арендатора ``whieda``: боты других арендаторов этого модуля не видят.

База старее кода (миграции V49 ещё нет): чтение статуса не падает, а даёт статус по профилю сайта — партнёр WWC или гость.
В логи имён и ников не попадает: только последние четыре цифры id.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger("whieda.bot_stages")

STAGES_TENANT_ID = "whieda"
STAGE_GUEST = "guest"
STAGE_PARTNER_WHIEDA = "partner_whieda"
STAGE_PARTNER_WWC = "partner_wwc"
STAGES = (STAGE_GUEST, STAGE_PARTNER_WHIEDA, STAGE_PARTNER_WWC)
_RANK = {STAGE_GUEST: 1, STAGE_PARTNER_WHIEDA: 2, STAGE_PARTNER_WWC: 3}
CHANNELS = ("telegram", "max")


def stages_enabled(tenant_id: str | None = None) -> bool:
    """Три меню включены (флаг ``PLATFORM_BOT_STAGES``) и это бот WWC. ``tenant_id=None`` — только флаг."""
    if tenant_id is not None and tenant_id != STAGES_TENANT_ID:
        return False
    return bool(get_settings().platform_bot_stages)


def user_ref(user_id: int | str) -> str:
    return str(user_id)[-4:]


def is_owner_or_admin(telegram_user_id: int | None) -> bool:
    """Владелец бота (его id в ``PLATFORM_BILLING_OWNER_TELEGRAM_ID``) и суперадмины: у них меню партнёра WWC."""
    if telegram_user_id is None:
        return False
    settings = get_settings()
    owner = settings.platform_billing_owner_telegram_id
    if owner is not None and int(telegram_user_id) == int(owner):
        return True
    return int(telegram_user_id) in settings.parsed_super_admin_telegram_ids()


# Человек в мессенджере → его actor. Те же три пути, что у guest._PARTNER_SQL: id в Telegram, канал (Max), привязанная
# учётка (Max или Google, привязанные к Telegram партнёра).
_PERSON_CTE = """
with person as (
  select actor_id from lead_actors
  where tenant_id = %(tenant)s and %(channel)s = 'telegram' and telegram_user_id = %(user)s
  union
  select actor_id from lead_actor_channels
  where tenant_id = %(tenant)s and channel = %(channel)s and channel_user_id = %(user_text)s
  union
  select la.actor_id from account_identities ai
  join lead_actors la on la.tenant_id = ai.tenant_id and la.telegram_user_id = ai.telegram_user_id
  where ai.tenant_id = %(tenant)s and ai.provider = %(channel)s and ai.external_id = %(user_text)s
    and ai.revoked_at is null
)
"""

# Включённый профиль сайта — единственное, что подтягивает поле bot_stage к partner_wwc.
_HAS_ENABLED_PROFILE = """
exists (
  select 1 from referral_profiles rp
  where rp.tenant_id = %(tenant)s and rp.enabled and rp.owner_id in (select actor_id from person)
)
"""

# Партнёр WWC для бота: любой профиль сайта (включённый или нет) либо оплаченная, но не активированная заявка.
_HAS_PROFILE = """
(
  exists (
    select 1 from referral_profiles rp
    where rp.tenant_id = %(tenant)s and rp.owner_id in (select actor_id from person)
  )
  or exists (
    select 1 from partner_site_requests sr
    where sr.tenant_id = %(tenant)s and sr.actor_id in (select actor_id from person)
      and sr.status in ('pending_confirmation', 'pending_provisioning')
  )
)
"""

_STAGE_SQL = (
    _PERSON_CTE
    + f"""
select {_HAS_PROFILE} as has_profile,
  {_HAS_ENABLED_PROFILE} as has_enabled_profile,
  coalesce((
    select la.bot_stage from lead_actors la
    where la.tenant_id = %(tenant)s and la.actor_id in (select actor_id from person)
    order by case la.bot_stage when 'partner_wwc' then 3 when 'partner_whieda' then 2 else 1 end desc
    limit 1
  ), 'guest') as bot_stage
"""
)

# Без колонки bot_stage: база, на которую миграция V49 ещё не накатана.
_PROFILE_ONLY_SQL = _PERSON_CTE + f"select {_HAS_PROFILE} as has_profile"

# Подтянуть поле к профилю сайта: у владельца включённого профиля статус partner_wwc.
_SYNC_PROFILE_SQL = (
    _PERSON_CTE
    + """
update lead_actors la set bot_stage = 'partner_wwc', updated_at = now()
where la.tenant_id = %(tenant)s and la.bot_stage <> 'partner_wwc'
  and la.actor_id in (
    select rp.owner_id from referral_profiles rp
    where rp.tenant_id = %(tenant)s and rp.enabled and rp.owner_id in (select actor_id from person)
  )
"""
)


def _params(tenant_id: str, channel: str, user_id: int) -> dict[str, Any]:
    if channel not in CHANNELS:
        raise ValueError("channel must be telegram or max")
    return {"tenant": tenant_id, "channel": channel, "user": int(user_id), "user_text": str(int(user_id))}


async def resolve_stage(conn: Any, tenant_id: str, channel: str, user_id: int) -> str:
    """Статус человека в боте. ``conn`` — открытое ``tenant_connection`` того же арендатора. Ошибка запроса —
    исключение: вызывающий не должен ломать из-за неё обычный ответ бота (см. ``person_stage``)."""
    if channel == "telegram" and is_owner_or_admin(user_id):
        return STAGE_PARTNER_WWC
    params = _params(tenant_id, channel, user_id)
    row = await fetch_one(conn, _STAGE_SQL, params)
    stored = str((row or {}).get("bot_stage") or STAGE_GUEST)
    if bool((row or {}).get("has_profile")):
        if bool((row or {}).get("has_enabled_profile")) and stored != STAGE_PARTNER_WWC:
            async with conn.cursor() as cur:
                await cur.execute(_SYNC_PROFILE_SQL, params)
        return STAGE_PARTNER_WWC
    return stored if stored in STAGES else STAGE_GUEST


async def person_stage(tenant_id: str, channel: str, user_id: int) -> str:
    """Статус человека своим подключением. Миграции V49 ещё нет — статус по профилю сайта и заявке в очереди (партнёр
    WWC или гость)."""
    if channel == "telegram" and is_owner_or_admin(user_id):
        return STAGE_PARTNER_WWC
    try:
        async with tenant_connection(tenant_id) as conn:
            return await resolve_stage(conn, tenant_id, channel, user_id)
    except Exception as exc:  # noqa: BLE001 — до миграции V49 колонки нет; всё остальное поднимет вторая попытка
        logger.warning("bot_stage_read_failed", extra={"channel": channel, "error_class": type(exc).__name__})
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _PROFILE_ONLY_SQL, _params(tenant_id, channel, user_id))
    return STAGE_PARTNER_WWC if (row or {}).get("has_profile") else STAGE_GUEST


# ---- изменение статуса -----------------------------------------------------------------------------


async def person_actor_ids(conn: Any, tenant_id: str, channel: str, user_id: int) -> list[str]:
    rows = await fetch_all(conn, _PERSON_CTE + "select actor_id from person order by actor_id", _params(tenant_id, channel, user_id))
    return [str(row["actor_id"]) for row in rows]


async def _ensure_actor(tenant_id: str, channel: str, user_id: int) -> str:
    """Actor человека: есть — вернуть, нет — завести (без приглашения), как это делает его первый /start."""
    if channel == "telegram":
        from app.referral_bonus.service import ensure_telegram_actor

        return await ensure_telegram_actor(
            tenant_id, telegram_user_id=int(user_id), telegram_chat_id=int(user_id), raw_update={}
        )
    from app.leads.channels import ensure_channel_actor

    return await ensure_channel_actor(
        tenant_id, channel=channel, channel_user_id=int(user_id), channel_chat_id=None, username=None,
        display_name=f"{channel} user {int(user_id)}",
    )


_PROMOTE_SQL = """
update lead_actors set bot_stage = %(stage)s::text, updated_at = now()
where tenant_id = %(tenant)s and actor_id = %(actor)s and bot_stage <> %(stage)s::text
  and (case bot_stage when 'partner_wwc' then 3 when 'partner_whieda' then 2 else 1 end)
      < (case %(stage)s::text when 'partner_wwc' then 3 when 'partner_whieda' then 2 else 1 end)
returning actor_id
"""


async def promote(tenant_id: str, channel: str, user_id: int, stage: str) -> str:
    """Поднять человека до ``stage`` (``partner_whieda`` или ``partner_wwc``). Возвращает ``changed`` — статус
    поднят; ``already`` — у него уже этот статус или выше (повторное подтверждение ничего не меняет и не
    пишет человеку второй раз). Выше не пускает: партнёра WWC до партнёра WHIEDA не понижает."""
    if stage not in (STAGE_PARTNER_WHIEDA, STAGE_PARTNER_WWC):
        raise ValueError("a person is promoted to partner_whieda or partner_wwc")
    actor_id = await _ensure_actor(tenant_id, channel, user_id)
    async with tenant_connection(tenant_id) as conn:
        current = await resolve_stage(conn, tenant_id, channel, user_id)
        if _RANK[current] >= _RANK[stage]:
            return "already"
        row = await fetch_one(conn, _PROMOTE_SQL, {"tenant": tenant_id, "actor": actor_id, "stage": stage})
    logger.info("bot_stage_promoted", extra={"channel": channel, "stage": stage, "user_ref": user_ref(user_id)})
    return "changed" if row else "already"


_DEMOTE_SQL = """
update lead_actors set bot_stage = 'guest', updated_at = now()
where tenant_id = %(tenant)s and actor_id = any(%(actors)s) and bot_stage = 'partner_whieda'
returning actor_id
"""


async def demote_to_guest(tenant_id: str, channel: str, user_id: int) -> str:
    """Слово владельца «партнёр снять»: ``partner_whieda`` → ``guest``. Возвращает ``changed``; ``already`` — человек и
    так гость; ``has_site`` — у него сайт WWC (включённый профиль), статус партнёра WWC словом не снимается."""
    async with tenant_connection(tenant_id) as conn:
        current = await resolve_stage(conn, tenant_id, channel, user_id)
        if current == STAGE_PARTNER_WWC:
            return "has_site"
        if current == STAGE_GUEST:
            return "already"
        actors = await person_actor_ids(conn, tenant_id, channel, user_id)
        rows = await fetch_all(conn, _DEMOTE_SQL, {"tenant": tenant_id, "actors": actors})
    logger.info("bot_stage_demoted", extra={"channel": channel, "user_ref": user_ref(user_id)})
    return "changed" if rows else "already"


# ---- кто пригласил: для «Вы пришли от …» и «Пример сайта партнёра» ---------------------------------------

_INVITER_SQL = (
    _PERSON_CTE
    + """
select a.inviter_actor_id
from partner_referral_attributions a
where a.tenant_id = %(tenant)s and a.invitee_actor_id in (select actor_id from person)
order by a.created_at, a.inviter_actor_id
limit 1
"""
)


async def inviter_actor_id(conn: Any, tenant_id: str, channel: str, user_id: int) -> str | None:
    """Партнёр, чья ссылка закрепила человека в боте (первое касание); нет — None."""
    row = await fetch_one(conn, _INVITER_SQL, _params(tenant_id, channel, user_id))
    return str(row["inviter_actor_id"]) if row else None


# ---- поиск человека по слову владельца -------------------------------------------------------------

_HANDLE_SQL = """
select actor_id, display_name, telegram_user_id, telegram_chat_id
from lead_actors
where tenant_id = %(tenant)s and active
  and (
    lower(ltrim(coalesce(telegram_username, ''), '@')) = %(handle)s
    or lower(display_name) = '@' || %(handle)s
  )
order by (telegram_user_id is null), actor_id
limit 3
"""
_USER_ID_SQL = """
select actor_id, display_name, telegram_user_id, telegram_chat_id
from lead_actors
where tenant_id = %(tenant)s and active and telegram_user_id = %(user)s
limit 1
"""


async def find_telegram_person(tenant_id: str, who: str) -> list[dict[str, Any]]:
    """Человек в боте по «@ник» или по числовому id Telegram. Пусто — не нашли; больше одного — неоднозначно.
    У ника в базе два места: ``telegram_username`` (партнёры, заведённые владельцем) и ``display_name`` вида «@ник»
    (обычные люди из бота). Нужен telegram_user_id: без него человеку нельзя ни написать, ни поставить статус."""
    value = str(who or "").strip()
    async with tenant_connection(tenant_id) as conn:
        if value.isdigit():
            row = await fetch_one(conn, _USER_ID_SQL, {"tenant": tenant_id, "user": int(value)})
            rows = [row] if row else []
        else:
            rows = await fetch_all(conn, _HANDLE_SQL, {"tenant": tenant_id, "handle": value.lstrip("@").lower()})
    return [dict(row) for row in rows if row.get("telegram_user_id") is not None]


# ---- собственный сайт партнёра: для меню партнёра WWC в Max ---------------------------------------------

_OWN_SITE_SQL = (
    _PERSON_CTE
    + """
select coalesce(rp.public_profile->>'public_site_url', '') as site_url
from referral_profiles rp
where rp.tenant_id = %(tenant)s and rp.enabled and rp.owner_id in (select actor_id from person)
order by (rp.public_profile->>'public_site_url') is null, rp.ref_code
limit 1
"""
)


async def own_site_url(conn: Any, tenant_id: str, channel: str, user_id: int) -> str | None:
    """Адрес сайта самого партнёра (включённый профиль с ``public_site_url``); нет сайта — None."""
    row = await fetch_one(conn, _OWN_SITE_SQL, _params(tenant_id, channel, user_id))
    return (str(row["site_url"]) if row else "") or None


# ---- доступ к Академии: «🎓 Академия» в меню гостя ------------------------------------------------------

_ACADEMY_ACCESS_SQL = """
with who as (
  select %(user)s::bigint as telegram_user_id where %(channel)s = 'telegram'
  union
  select ai.telegram_user_id from account_identities ai
  where ai.tenant_id = %(tenant)s and ai.provider = %(channel)s and ai.external_id = %(user_text)s
    and ai.revoked_at is null
)
select
  exists (
    select 1 from academy_access a join who w on w.telegram_user_id = a.telegram_user_id
    where a.tenant_id = %(tenant)s and a.revoked_at is null
  )
  or exists (
    select 1 from academy_progress p join who w on w.telegram_user_id = p.telegram_user_id
    where p.tenant_id = %(tenant)s
  ) as has_access
"""


async def has_academy_access(conn: Any, tenant_id: str, channel: str, user_id: int) -> bool:
    """У человека есть любой доступ в Академии: купленный курс, подарок, ключ или доступ от владельца (строка
    ``academy_access`` без отзыва) либо уже пройденный урок. Один запрос. Max-пользователь ищется по привязанной
    учётке (``account_identities``): доступ в Академии записан на Telegram-id.

    Клуб отдельно не проверяется: клубный доступ даёт включённый профиль сайта, а такой человек — партнёр WWC,
    не гость, и Академия у него в кабинете."""
    row = await fetch_one(conn, _ACADEMY_ACCESS_SQL, _params(tenant_id, channel, user_id))
    return bool((row or {}).get("has_access"))

