"""Визитка партнёра для гостя после Живой встречи (срез 5 бот-воронки, 31.10.2026).

Визитка — сообщение с именем партнёра, его контактами и кнопками «Написать в Telegram», «Написать в Max», «Сайт»;
с фото, если в профиле есть адрес фото. Данные берутся из ``referral_profiles.public_profile`` (display_name,
photo_url, public_site_url или subdomain, contacts.phone / email / max_url) и ``lead_actors.telegram_username``
(личный Telegram партнёра).

Кому какая визитка (``guest_cards``):
  * у гостя есть партнёр (``website_leads.assigned_owner_id`` — не владелец «по умолчанию») — визитка этого партнёра;
  * гость без партнёра (реклама: владелец заявки пуст или ``PLATFORM_ORGANIC_OWNER_ID``; запись без заявки) —
    две визитки подряд: владельца WWC и партнёров из ``PLATFORM_MEETING_AD_CARD_REFS`` (по умолчанию ``onlineelena``);
  * профиль партнёра не нашёлся — как гость без партнёра.

Ссылки в кнопках проверяются: только https, без пробелов и кавычек, иначе кнопки нет (Telegram отклонил бы всё
сообщение). Имена и контакты в логи не пишутся.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.db import fetch_one
from app.funnel import texts
from app.funnel.guest import Rows
from app.leads.meeting_leads import plain_no_links
from app.ref.service import PUBLIC_SITE_DOMAIN, resolve_photo_url
from app.settings import get_settings

NAME_MAX = 80
URL_MAX = 300
_TELEGRAM_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
_SUBDOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_EMAIL_RE = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[^\s@<>\"'.]{2,}")
_URL_FORBIDDEN = frozenset("<>\"'`\\{}|^")

_PROFILE_BY_OWNER_SQL = """
select rp.ref_code, rp.owner_id, rp.public_profile, la.display_name as actor_name, la.telegram_username
from referral_profiles rp
left join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where rp.tenant_id = %s and rp.enabled and rp.owner_id = %s
order by (rp.public_profile ->> 'public_site_url') is null, rp.ref_code
limit 1
"""

_PROFILE_BY_REF_SQL = """
select rp.ref_code, rp.owner_id, rp.public_profile, la.display_name as actor_name, la.telegram_username
from referral_profiles rp
left join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where rp.tenant_id = %s and rp.enabled and rp.ref_code = %s
limit 1
"""

_ACTOR_SQL = """
select display_name as actor_name, telegram_username
from lead_actors
where tenant_id = %s and actor_id = %s
limit 1
"""


@dataclass(frozen=True)
class OwnerCard:
    """Визитка партнёра: уже очищенные поля (пустое — значит, нет)."""

    owner_id: str | None
    ref_code: str | None
    name: str
    photo_url: str | None = None
    site_url: str | None = None
    max_url: str | None = None
    phone: str | None = None
    email: str | None = None
    telegram_username: str | None = None

    @property
    def identity(self) -> str:
        return f"owner:{self.owner_id}" if self.owner_id else f"ref:{self.ref_code}"


# ---- очистка полей (чистые функции) --------------------------------------------------------------


def safe_https_url(value: Any, *, host_suffixes: tuple[str, ...] = ()) -> str | None:
    """https-адрес без пробелов, кавычек и скобок до 300 знаков; ``host_suffixes`` — хост должен заканчиваться
    одним из них (``max.ru`` пропустит ``www.max.ru``, но не ``max.ru.evil.example``)."""
    text = str(value or "").strip()
    if not text or len(text) > URL_MAX or any(ch.isspace() or ch in _URL_FORBIDDEN for ch in text):
        return None
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return None
    if host_suffixes and not any(host == suffix or host.endswith("." + suffix) for suffix in host_suffixes):
        return None
    return text


def telegram_username(value: Any) -> str | None:
    """«@name» или «name» → «name»; не имя пользователя Telegram — None."""
    text = str(value or "").strip().removeprefix("@")
    return text if _TELEGRAM_USERNAME_RE.fullmatch(text) else None


def _text(value: Any, limit: int) -> str | None:
    cleaned = plain_no_links(value, limit)
    return cleaned or None


def site_url(profile: dict[str, Any]) -> str | None:
    """Сайт партнёра: ``public_site_url`` (https), иначе ``https://<subdomain>.wwc.best`` — только выбранный партнёром
    адрес из профиля, никаких догадок."""
    explicit = safe_https_url(profile.get("public_site_url"))
    if explicit:
        return explicit
    subdomain = str(profile.get("subdomain") or "").strip().lower()
    return f"https://{subdomain}.{PUBLIC_SITE_DOMAIN}" if _SUBDOMAIN_RE.fullmatch(subdomain) else None


def build_card(row: dict[str, Any]) -> OwnerCard | None:
    """Строка профиля (и актёра) → визитка. Нет ни имени, ни кода — None."""
    profile = row.get("public_profile")
    profile = profile if isinstance(profile, dict) else {}
    contacts = profile.get("contacts")
    contacts = contacts if isinstance(contacts, dict) else {}
    name = (
        _text(profile.get("display_name"), NAME_MAX)
        or _text(row.get("actor_name"), NAME_MAX)
        or _text(row.get("ref_code"), NAME_MAX)
    )
    if not name:
        return None
    email = str(contacts.get("email") or "").strip()
    photo = resolve_photo_url(profile.get("photo_url"), get_settings().tenant_media_base_url)
    return OwnerCard(
        owner_id=row.get("owner_id"),
        ref_code=row.get("ref_code"),
        name=name,
        photo_url=safe_https_url(photo),
        site_url=site_url(profile),
        max_url=safe_https_url(contacts.get("max_url"), host_suffixes=("max.ru",)),
        phone=_text(contacts.get("phone"), 32),
        email=email if _EMAIL_RE.fullmatch(email) and len(email) <= 120 else None,
        telegram_username=telegram_username(row.get("telegram_username")),
    )


def card_text(card: OwnerCard, intro: str) -> str:
    """``intro`` (с подставленным именем) и строки контактов."""
    lines = [intro]
    if card.phone:
        lines.append(texts.CARD_PHONE_LINE.format(phone=card.phone))
    if card.email:
        lines.append(texts.CARD_EMAIL_LINE.format(email=card.email))
    return "\n".join(lines)


def card_rows(card: OwnerCard) -> Rows:
    """Кнопки-ссылки (каждая в своём ряду): Telegram, Max, сайт. Нет ссылки — нет кнопки."""
    rows: Rows = []
    if card.telegram_username:
        rows.append([(texts.CARD_TELEGRAM_BUTTON, f"https://t.me/{card.telegram_username}")])
    if card.max_url:
        rows.append([(texts.CARD_MAX_BUTTON, card.max_url)])
    if card.site_url:
        rows.append([(texts.CARD_SITE_BUTTON, card.site_url)])
    return rows


def card_intro(card: OwnerCard, *, position: int, partner: bool) -> str:
    """Первая строка визитки: партнёру гостя, первая и вторая визитка гостю с рекламы."""
    if partner:
        return texts.AFTER_CARD_PARTNER.format(name=card.name)
    template = texts.AFTER_CARD_AD_FIRST if position == 0 else texts.AFTER_CARD_AD_NEXT
    return template.format(name=card.name)


# ---- база ------------------------------------------------------------------------------------------


async def load_card(
    conn: Any, tenant_id: str, *, owner_id: str | None = None, ref_code: str | None = None
) -> OwnerCard | None:
    """Визитка по владельцу (``owner_id``) или коду партнёра (``ref_code``). Нет включённого профиля, но есть актёр
    (например, владелец WWC) — визитка из имени и личного Telegram. Нет ничего — None."""
    if ref_code:
        row = await fetch_one(conn, _PROFILE_BY_REF_SQL, (tenant_id, str(ref_code).strip().lower()))
        return build_card(row) if row else None
    if not owner_id:
        return None
    row = await fetch_one(conn, _PROFILE_BY_OWNER_SQL, (tenant_id, owner_id))
    if row:
        return build_card(row)
    actor = await fetch_one(conn, _ACTOR_SQL, (tenant_id, owner_id))
    if not actor:
        return None
    return build_card({"owner_id": owner_id, "ref_code": None, **actor})


def is_ad_guest(owner_id: str | None) -> bool:
    """Гость без партнёра: владельца нет или это владелец «по умолчанию» (реклама)."""
    return not owner_id or owner_id == get_settings().platform_organic_owner_id


async def guest_cards(conn: Any, tenant_id: str, owner_id: str | None) -> tuple[list[OwnerCard], bool]:
    """``(визитки, у_гостя_есть_партнёр)``: визитка партнёра гостя либо пара «владелец WWC + onlineelena» гостю с рекламы
    (и когда профиль партнёра не нашёлся). Пустой список — визиток нет совсем (в базе нет ни профиля, ни актёра)."""
    if not is_ad_guest(owner_id):
        card = await load_card(conn, tenant_id, owner_id=owner_id)
        if card is not None:
            return [card], True
    settings = get_settings()
    cards: list[OwnerCard] = []
    seen: set[str] = set()
    candidates = [await load_card(conn, tenant_id, owner_id=settings.platform_organic_owner_id)]
    for ref in settings.parsed_meeting_ad_card_refs():
        candidates.append(await load_card(conn, tenant_id, ref_code=ref))
    for card in candidates:
        if card is not None and card.identity not in seen:
            seen.add(card.identity)
            cards.append(card)
    return cards, False
