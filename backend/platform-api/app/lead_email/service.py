"""Почта для заявок: разбор команд бота, проверка адреса и запись ``public_profile.lead_email``.

Запись — ``jsonb_set`` по одному ключу: остальной профиль (имя, фото, контакты, тема) не затирается.
``profile_version`` не растёт: адрес на сайте не показывается, пересборка не нужна. Партнёр без
сайта (нет включённого ``referral_profiles``) адрес задать не может.

В логах нет самого адреса: только tenant, telegram_user_id, ref_code и кто менял.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.db import fetch_all, tenant_connection
from app.login_identities import service as login_service
from app.login_identities.rules import error

logger = logging.getLogger("whieda.lead_email")

MAX_EMAIL_LENGTH = 254


# ---- разбор команд --------------------------------------------------------------------------------

CLEAR_WORDS = frozenset({"убрать", "удалить"})

# «почта», «почта для заявок», «/leademail» (с @бота или без). Регистр не важен.
_COMMAND_RE = re.compile(
    r"^(?:(?P<slash>/leademail(?:@\w+)?)|почта(?P<full>\s+для\s+заявок)?)(?:\s+(?P<arg>.+))?$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Command:
    """action: show | set | clear. ``target`` заполнен только у команды владельца («<кто> <адрес>»)."""

    action: str
    value: str = ""
    target: str = ""


def _is_tail(token: str) -> bool:
    """Последнее слово команды владельца: адрес (есть «@») или «убрать»."""
    return "@" in token or token.lower() in CLEAR_WORDS


def parse_command(text: str, *, is_owner: bool) -> Command | None:
    """Команда партнёра, команда владельца за партнёра или None — это не наша команда.

    Голое «почта <слово>» без «@» и без точки — обычная фраза («почта не приходит»), её не трогаем:
    она идёт в поддержку или советнику. Явные формы («почта для заявок …», «/leademail …») — всегда наши:
    неверный адрес получает подсказку. «<кто> <адрес>» разбирает только владелец; у остальных такой текст
    не команда (как «код входа» в login_identities).
    """
    value = " ".join(str(text or "").split())
    match = _COMMAND_RE.fullmatch(value) if value else None
    if match is None:
        return None
    explicit = bool(match.group("slash") or match.group("full"))
    arg = (match.group("arg") or "").strip()
    tokens = arg.split()
    if not tokens:
        return Command("show")
    tail = tokens[-1]
    target = arg[: len(arg) - len(tail)].strip()
    # «<кто> <адрес|убрать>»; «кто» с «@» — не человек, а второй адрес («a@b.c, d@e.f»): это неверный адрес.
    if len(tokens) >= 2 and _is_tail(tail) and "@" not in target:
        if not (explicit and is_owner):
            return None
        if tail.lower() in CLEAR_WORDS:
            return Command("clear", target=target)
        return Command("set", value=tail, target=target)
    if len(tokens) == 1:
        token = tokens[0]
        if token.lower() in CLEAR_WORDS:
            return Command("clear")
        if explicit or "@" in token or "." in token:
            return Command("set", value=token)
        return None
    return Command("set", value=arg) if explicit else None


# ---- адрес ----------------------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Адрес уходит в почтовое письмо: запятая, «;», скобки и кавычки дали бы второго получателя или заголовок.
_FORBIDDEN_RE = re.compile(r'[\s,;<>()\[\]"\\\x00-\x1f]')
_WRAP_START = "<\"'«“‘"
_WRAP_END = ">\"'»”’.,;:!"


def normalize_email(raw: str) -> str | None:
    """Адрес в виде для записи или None, если не похоже на почту. Домен — в нижнем регистре."""
    value = str(raw or "").strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    value = value.lstrip(_WRAP_START).rstrip(_WRAP_END)
    if not value or len(value) > MAX_EMAIL_LENGTH:
        return None
    if _FORBIDDEN_RE.search(value) or not _EMAIL_RE.fullmatch(value):
        return None
    local, domain = value.rsplit("@", 1)
    if domain.strip(".-") != domain or ".." in domain:
        return None
    return f"{local}@{domain.lower()}"


# ---- чтение ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    ref_code: str
    lead_email: str | None


@dataclass(frozen=True)
class OwnerTarget:
    """Кого нашёл владелец: подпись для ответа, Telegram (если известен) и его включённые сайты."""

    title: str
    telegram_user_id: int | None
    sites: list[Site]


_SITES_BY_TELEGRAM_SQL = """
select rp.ref_code, nullif(rp.public_profile->>'lead_email', '') as lead_email
from referral_profiles rp
join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where rp.tenant_id = %s and rp.enabled = true and la.telegram_user_id = %s
order by rp.ref_code
"""

# Партнёры из реестра Partners_Ref заведены без telegram_user_id (он заполняется, когда человек
# первый раз пишет боту), — общий поиск «кода входа» их не видит. Для них — поиск прямо по профилю.
_SITE_BY_REF_SQL = """
select rp.ref_code, nullif(rp.public_profile->>'lead_email', '') as lead_email,
       coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name) as display_name,
       la.telegram_user_id
from referral_profiles rp
join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where rp.tenant_id = %s and rp.enabled = true and rp.ref_code = %s
limit 1
"""

_SITES_BY_NAME_SQL = """
select rp.ref_code, nullif(rp.public_profile->>'lead_email', '') as lead_email,
       coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name) as display_name,
       la.telegram_user_id
from referral_profiles rp
join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where rp.tenant_id = %s and rp.enabled = true
  and position(lower(%s) in lower(coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name))) > 0
order by rp.ref_code
limit 10
"""


def _sites(rows: list[dict[str, Any]]) -> list[Site]:
    return [
        Site(ref_code=str(r["ref_code"]), lead_email=(str(r["lead_email"]) if r.get("lead_email") else None))
        for r in rows
    ]


async def _sites_of_telegram_on(conn: Any, tenant_id: str, telegram_user_id: int) -> list[Site]:
    rows = await fetch_all(conn, _SITES_BY_TELEGRAM_SQL, (tenant_id, int(telegram_user_id)))
    return _sites(rows)


async def sites_of_telegram(tenant_id: str, telegram_user_id: int) -> list[Site]:
    """Включённые сайты человека. Пусто — у него нет сайта, почту для заявок задать нельзя."""
    async with tenant_connection(tenant_id) as conn:
        return await _sites_of_telegram_on(conn, tenant_id, telegram_user_id)


async def lead_email_of(tenant_id: str, telegram_user_id: int) -> str | None:
    """Для кабинета /me: заданный адрес или None."""
    sites = await sites_of_telegram(tenant_id, telegram_user_id)
    return next((site.lead_email for site in sites if site.lead_email), None)


async def resolve_owner_target(tenant_id: str, target: str) -> OwnerTarget:
    """«Кто» из команды владельца. Сначала общий поиск «кода входа» (ref → id → имя), и только если он
    никого не нашёл — профили без telegram_user_id по ref или имени. partner_not_found / partner_ambiguous."""
    async with tenant_connection(tenant_id) as conn:
        people = await login_service.find_people(conn, tenant_id, target)
        if len(people) > 1:
            raise error("partner_ambiguous", target=target, candidates=people)
        if people:
            person = people[0]
            telegram_user_id = int(person["telegram_user_id"])
            sites = await _sites_of_telegram_on(conn, tenant_id, telegram_user_id)
            return OwnerTarget(login_service.person_title(person), telegram_user_id, sites)
        rows = await fetch_all(conn, _SITE_BY_REF_SQL, (tenant_id, str(target).strip().lower()))
        if not rows:
            rows = await fetch_all(conn, _SITES_BY_NAME_SQL, (tenant_id, str(target).strip()))
    if not rows:
        raise error("partner_not_found", target=target)
    if len(rows) > 1:
        raise error("partner_ambiguous", target=target, candidates=[dict(r) for r in rows])
    row = rows[0]
    telegram_user_id = int(row["telegram_user_id"]) if row.get("telegram_user_id") is not None else None
    return OwnerTarget(login_service.person_title(row), telegram_user_id, _sites(rows))


# ---- запись ---------------------------------------------------------------------------------------

_SET_SQL = """
update referral_profiles
   set public_profile = jsonb_set(
         case when jsonb_typeof(public_profile) = 'object' then public_profile else '{}'::jsonb end,
         '{lead_email}', to_jsonb(%s::text), true),
       updated_at = now()
 where tenant_id = %s and enabled = true and ref_code = any(%s)
returning ref_code
"""

_CLEAR_SQL = """
update referral_profiles
   set public_profile = public_profile - 'lead_email', updated_at = now()
 where tenant_id = %s and enabled = true and ref_code = any(%s)
   and jsonb_typeof(public_profile) = 'object' and public_profile ? 'lead_email'
returning ref_code
"""


async def set_lead_email(
    tenant_id: str, ref_codes: list[str], email: str, *, telegram_user_id: int | None, by: str
) -> list[str]:
    """Записать адрес во все перечисленные сайты. Возвращает ref_code, где записано."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(conn, _SET_SQL, (email, tenant_id, list(ref_codes)))
    changed = [str(r["ref_code"]) for r in rows]
    if changed:
        _log("lead_email_set", tenant_id, telegram_user_id, changed, by)
    return changed


async def clear_lead_email(
    tenant_id: str, ref_codes: list[str], *, telegram_user_id: int | None, by: str
) -> list[str]:
    """Убрать адрес. Возвращает ref_code, где он был и удалён; где адреса не было — не трогаем."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(conn, _CLEAR_SQL, (tenant_id, list(ref_codes)))
    changed = [str(r["ref_code"]) for r in rows]
    if changed:
        _log("lead_email_cleared", tenant_id, telegram_user_id, changed, by)
    return changed


def _log(event: str, tenant_id: str, telegram_user_id: int | None, ref_codes: list[str], by: str) -> None:
    logger.info(
        event,
        extra={"tenant_id": tenant_id, "telegram_user_id": telegram_user_id, "ref_codes": ref_codes, "by": by},
    )
