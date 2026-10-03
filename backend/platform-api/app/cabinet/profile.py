"""Профиль сайта из кабинета /me/: проверка полей, «было → стало», запись в public_profile.

Только чистые функции: маршруты и сервис зовут их, тесты проверяют без базы.

Поля (ключи запроса POST /me/profile и ``changes`` заявки, snake_case; для
contacts/socials принимаются и camelCase-ключи публичного контракта):

- ``display_name`` — имя на сайте, 2–80 символов, одна строка, без HTML;
- ``bio`` — «о себе», до 600 символов, абзацы сохраняются, без HTML;
- ``photo_url`` — только адрес фото, загруженного через POST /me/profile/photo;
- ``contacts`` — ``phone``, ``whatsapp``, ``viber`` (номер → E.164),
  ``max_url`` (https://max.ru/…), ``email``, ``address`` (одна строка до 200);
- ``socials`` — ``telegram_channel_url`` (t.me), ``vk_url`` (vk.com / vk.ru),
  ``instagram_url``, ``youtube_url``, ``tiktok_url``: только ``https://`` и
  только адрес своей сети.

Ключа нет — поле не меняется; пустая строка или null у bio, contacts и socials —
«убрать». Имя убрать нельзя, фото — тоже (без своего фото сайт показывает фото
из реестра сайта).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any
from urllib.parse import urlsplit

from app.site_requests.contacts import normalize_phone

DISPLAY_NAME_MIN = 2
DISPLAY_NAME_MAX = 80
BIO_MAX = 600
ADDRESS_MAX = 200
URL_MAX = 300
EMAIL_MAX = 254
PHONE_INPUT_MAX = 32

SCALAR_FIELDS: tuple[str, ...] = ("display_name", "bio", "photo_url")
CONTACT_KEYS: tuple[str, ...] = ("phone", "whatsapp", "viber", "max_url", "email", "address")
SOCIAL_KEYS: tuple[str, ...] = ("telegram_channel_url", "vk_url", "instagram_url", "youtube_url", "tiktok_url")
GROUPS: dict[str, tuple[str, ...]] = {"contacts": CONTACT_KEYS, "socials": SOCIAL_KEYS}
PHONE_KEYS = frozenset({"phone", "whatsapp", "viber"})

# camelCase ключи публичного /api/v1/public/ref → внутренние.
_ALIASES = {
    "displayName": "display_name",
    "photoUrl": "photo_url",
    "maxUrl": "max_url",
    "telegramChannelUrl": "telegram_channel_url",
    "vkUrl": "vk_url",
    "instagramUrl": "instagram_url",
    "youtubeUrl": "youtube_url",
    "tiktokUrl": "tiktok_url",
}

# Ссылка ведёт только на свою сеть: на сайте партнёра не появится чужой адрес.
URL_HOSTS: dict[str, tuple[str, ...]] = {
    "max_url": ("max.ru",),
    "telegram_channel_url": ("t.me", "telegram.me"),
    "vk_url": ("vk.com", "vk.ru"),
    "instagram_url": ("instagram.com",),
    "youtube_url": ("youtube.com", "youtu.be"),
    "tiktok_url": ("tiktok.com",),
}

FIELD_TITLES: dict[str, str] = {
    "display_name": "Имя",
    "bio": "О себе",
    "photo_url": "Фото",
    "phone": "Телефон",
    "whatsapp": "WhatsApp",
    "viber": "Viber",
    "max_url": "MAX",
    "email": "E-mail",
    "address": "Адрес",
    "telegram_channel_url": "Telegram-канал",
    "vk_url": "ВКонтакте",
    "instagram_url": "Instagram",
    "youtube_url": "YouTube",
    "tiktok_url": "TikTok",
}

_HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z!?]|&(?:[A-Za-z]{2,10}|#[0-9]{1,7}|#x[0-9A-Fa-f]{1,6});")
_PHONE_INPUT_RE = re.compile(r"\+?[0-9\s().\-]{7,30}")
_EMAIL_RE = re.compile(r"[\w.+%-]+@[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}")
_URL_FORBIDDEN = set("<>\"'`\\{}|^")
# Имя и адрес сайт вставляет в разметку (alt, innerHTML): кавычки и угловые
# скобки там — готовая XSS (ревью 02.10.2026). В имени человека они не нужны.
_LINE_FORBIDDEN = set("<>\"`")


class ProfileValidationError(ValueError):
    """A field the cabinet refuses; ``code`` and ``field`` go to the API as is."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


def _strip_controls(text: str, *, keep_newlines: bool) -> str:
    out = []
    for ch in text:
        if ch == "\n" and keep_newlines:
            out.append(ch)
        elif ch in "\t\n\r\f\v":
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            continue
        else:
            out.append(ch)
    return "".join(out)


def _clean_line(value: Any, field: str, *, max_len: int, min_len: int = 0) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError("invalid_value", field)
    text = " ".join(_strip_controls(unicodedata.normalize("NFC", value), keep_newlines=False).split())
    if not text:
        return None
    if _HTML_RE.search(text):
        raise ProfileValidationError("html_not_allowed", field)
    if any(ch in _LINE_FORBIDDEN for ch in text):
        raise ProfileValidationError("invalid_characters", field)
    if len(text) > max_len:
        raise ProfileValidationError("too_long", field)
    if len(text) < min_len:
        raise ProfileValidationError("too_short", field)
    return text


def clean_bio(value: Any) -> str | None:
    """«О себе»: абзацы остаются (не больше одной пустой строки подряд), до 600 символов."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError("invalid_value", "bio")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_controls(text, keep_newlines=True)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not text:
        return None
    if _HTML_RE.search(text):
        raise ProfileValidationError("html_not_allowed", "bio")
    if len(text) > BIO_MAX:
        raise ProfileValidationError("too_long", "bio")
    return text


def clean_phone(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError("invalid_phone", field)
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    if len(text) > PHONE_INPUT_MAX or not _PHONE_INPUT_RE.fullmatch(text):
        raise ProfileValidationError("invalid_phone", field)
    phone = normalize_phone(text)
    if not phone or not re.fullmatch(r"\+[0-9]{10,15}", phone):
        raise ProfileValidationError("invalid_phone", field)
    return phone


def clean_email(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError("invalid_email", "email")
    text = unicodedata.normalize("NFC", value).strip()
    if not text:
        return None
    if len(text) > EMAIL_MAX or not _EMAIL_RE.fullmatch(text):
        raise ProfileValidationError("invalid_email", "email")
    local, domain = text.rsplit("@", 1)
    return f"{local}@{domain.lower()}"


def clean_url(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileValidationError("invalid_url", field)
    text = value.strip()
    if not text:
        return None
    if len(text) > URL_MAX or any(ch.isspace() or ch in _URL_FORBIDDEN for ch in text):
        raise ProfileValidationError("invalid_url", field)
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ProfileValidationError("invalid_url", field) from exc
    if parts.scheme.lower() != "https":
        raise ProfileValidationError("https_required", field)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password or port not in (None, 443):
        raise ProfileValidationError("invalid_url", field)
    allowed = URL_HOSTS[field]
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ProfileValidationError("wrong_site", field)
    if not parts.path.strip("/"):
        raise ProfileValidationError("invalid_url", field)
    return text


def _clean_group_value(key: str, value: Any) -> str | None:
    if key in PHONE_KEYS:
        return clean_phone(value, key)
    if key == "email":
        return clean_email(value)
    if key == "address":
        return _clean_line(value, key, max_len=ADDRESS_MAX)
    return clean_url(value, key)


def normalize_profile_input(raw: dict[str, Any]) -> dict[str, Any]:
    """Тело POST /me/profile → проверенные значения тех полей, что пришли.

    None у bio и в contacts/socials — «убрать»; photo_url проверяет сервис
    (это должно быть своё загруженное фото).
    """
    if not isinstance(raw, dict):
        raise ProfileValidationError("invalid_body")
    data = {_ALIASES.get(str(key), str(key)): value for key, value in raw.items()}
    unknown = sorted(set(data) - set(SCALAR_FIELDS) - set(GROUPS))
    if unknown:
        raise ProfileValidationError("unknown_field", unknown[0])
    out: dict[str, Any] = {}
    if "display_name" in data:
        name = _clean_line(data["display_name"], "display_name", max_len=DISPLAY_NAME_MAX, min_len=DISPLAY_NAME_MIN)
        if name is None:
            raise ProfileValidationError("required", "display_name")
        out["display_name"] = name
    if "bio" in data:
        out["bio"] = clean_bio(data["bio"])
    if "photo_url" in data:
        photo = data["photo_url"]
        if photo is not None and not isinstance(photo, str):
            raise ProfileValidationError("invalid_photo", "photo_url")
        if photo and photo.strip():
            out["photo_url"] = photo.strip()
    for group, keys in GROUPS.items():
        if group not in data or data[group] is None:
            continue
        if not isinstance(data[group], dict):
            raise ProfileValidationError("invalid_value", group)
        values = {_ALIASES.get(str(key), str(key)): value for key, value in data[group].items()}
        extra = sorted(set(values) - set(keys))
        if extra:
            raise ProfileValidationError("unknown_field", extra[0])
        out[group] = {key: _clean_group_value(key, values[key]) for key in keys if key in values}
    return out


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def current_profile_fields(public_profile: Any) -> dict[str, Any]:
    """Что сейчас на сайте по данным Core, в форме полей кабинета.

    Как в публичном контракте: соцсети — вложенный объект, затем старый ключ
    верхнего уровня; контакты — только вложенный ``contacts`` (ключи вроде
    ``phone`` наверху могли значить другое — не публикуем их).
    """
    profile = public_profile if isinstance(public_profile, dict) else {}
    out: dict[str, Any] = {key: _text(profile.get(key)) for key in SCALAR_FIELDS}
    for group, keys in GROUPS.items():
        nested = profile.get(group) if isinstance(profile.get(group), dict) else {}
        legacy = profile if group == "socials" else {}
        out[group] = {key: _text(nested.get(key) or legacy.get(key)) for key in keys}
    return out


def overlay_changes(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    """Прежняя заявка + новые значения: новая заявка заменяет pending, но не
    теряет то, что партнёр уже отправил и сейчас не трогал."""
    out: dict[str, Any] = {key: base[key] for key in SCALAR_FIELDS if key in base}
    out.update({key: top[key] for key in SCALAR_FIELDS if key in top})
    for group in GROUPS:
        merged = dict(base.get(group) or {})
        merged.update(top.get(group) or {})
        if merged:
            out[group] = merged
    return out


def diff_profile(current: dict[str, Any], desired: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(changes, previous): только поля, которые правда меняются."""
    changes: dict[str, Any] = {}
    previous: dict[str, Any] = {}
    for key in SCALAR_FIELDS:
        if key in desired and desired[key] != current.get(key):
            changes[key] = desired[key]
            previous[key] = current.get(key)
    for group in GROUPS:
        wanted = desired.get(group) or {}
        now = current.get(group) or {}
        delta = {key: value for key, value in wanted.items() if value != now.get(key)}
        if delta:
            changes[group] = delta
            previous[group] = {key: now.get(key) for key in delta}
    return changes, previous


def apply_profile_changes(public_profile: Any, changes: dict[str, Any]) -> dict[str, Any]:
    """Новый public_profile после «Применить». Остальные ключи не трогаются.

    Контакт или соцсеть пишутся во вложенный объект. Для соцсети старый ключ
    верхнего уровня с тем же именем убирается, иначе убранная соцсеть вернулась
    бы из него; ключи верхнего уровня у контактов не трогаются.
    """
    profile = dict(public_profile) if isinstance(public_profile, dict) else {}
    for key in SCALAR_FIELDS:
        if key in changes:
            if changes[key]:
                profile[key] = changes[key]
            else:
                profile.pop(key, None)
    for group in GROUPS:
        if group not in changes:
            continue
        nested = dict(profile.get(group)) if isinstance(profile.get(group), dict) else {}
        for key, value in (changes.get(group) or {}).items():
            if group == "socials":
                profile.pop(key, None)
            if value:
                nested[key] = value
            else:
                nested.pop(key, None)
        if nested:
            profile[group] = nested
        else:
            profile.pop(group, None)
    return profile


def changed_fields(changes: dict[str, Any]) -> list[str]:
    """Ключи изменённых полей в порядке формы (для сообщений)."""
    keys = [key for key in SCALAR_FIELDS if key in changes]
    for group, group_keys in GROUPS.items():
        keys.extend(key for key in group_keys if key in (changes.get(group) or {}))
    return keys


def field_value(fields: dict[str, Any], key: str) -> Any:
    if key in SCALAR_FIELDS:
        return fields.get(key)
    for group, group_keys in GROUPS.items():
        if key in group_keys:
            return (fields.get(group) or {}).get(key)
    return None


def profile_complete(fields: dict[str, Any]) -> bool:
    """Шаг «заполнить профиль»: фото, «о себе» и хотя бы один контакт."""
    contacts = fields.get("contacts") or {}
    return bool(fields.get("photo_url") and fields.get("bio") and any(contacts.values()))


# ---- фото из кабинета ----------------------------------------------------------

MEDIA_PATH = "/api/v1/content-access/partner-media/"


def media_public_url(base: str, media_id: str) -> str:
    return f"{str(base).rstrip('/')}{MEDIA_PATH}{media_id}.jpg"


def media_id_in_url(url: Any) -> str | None:
    """id фото из кабинета в адресе с любым origin — для «что не удалять»:
    фото на сайте не должно пропасть, если base когда-то был другим."""
    text = str(url or "").strip().split("?", 1)[0]
    marker = text.rfind(MEDIA_PATH)
    if marker < 0 or not text.endswith(".jpg"):
        return None
    candidate = text[marker + len(MEDIA_PATH):-len(".jpg")]
    try:
        return str(uuid.UUID(candidate)) if len(candidate) == 36 else None
    except ValueError:
        return None


def media_id_from_url(url: Any, base: str) -> str | None:
    """id фото, если адрес — наше фото из кабинета (по этому base), иначе None."""
    text = str(url or "").strip()
    prefix = f"{str(base).rstrip('/')}{MEDIA_PATH}"
    if not text.startswith(prefix) or not text.endswith(".jpg"):
        return None
    candidate = text[len(prefix):-len(".jpg")]
    try:
        return str(uuid.UUID(candidate)) if len(candidate) == 36 else None
    except ValueError:
        return None
