"""Разбор контактов партнёра, присланных одним сообщением.

Партнёры пишут контакты для сайта как попало, но почти всегда одним
сообщением: «тг, ватсап, макс и телефон — 8 928 …», отдельной строкой почта,
ссылка на ВКонтакте, на свой канал или группу (владелец, 24.09.2026).

Разбор — подсказка для владельца, а не источник правды: исходный текст
хранится рядом (partner_site_requests.contacts_text) и уходит владельцу целиком.
Поэтому здесь нет попытки понять всё: только то, что узнаётся уверенно.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Ответ «контактов не будет».
_NONE_RE = re.compile(r"^\s*(нет|не\s+надо|не\s+нужно|-+|—|no|none|пропустить)\s*[.!]?\s*$", re.IGNORECASE)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:t\.me|telegram\.me|vk\.com|vk\.ru|m\.vk\.com|instagram\.com|tiktok\.com|max\.ru|wa\.me|youtube\.com|youtu\.be|ok\.ru|dzen\.ru)"
    r"/[^\s,;]+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<![\w@/])\+?\d[\d\s()\-]{8,17}\d")
_HANDLE_RE = re.compile(r"(?<![\w.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")

# Метки в строке: какие каналы человек назвал рядом с номером или ником.
_LABELS = {
    "whatsapp": re.compile(r"whats\s*app|ват[сц]ап|вацап|вотсап|\bwa\b", re.IGNORECASE),
    "max": re.compile(r"\bmax\b|\bмакс\b|мессенджер\s+макс", re.IGNORECASE),
    "telegram": re.compile(r"telegram|телеграм|\bтг\b|\btg\b", re.IGNORECASE),
    "phone": re.compile(r"тел(?:ефон)?\b|phone|\bмоб", re.IGNORECASE),
    "channel": re.compile(r"канал|групп|сообществ|паблик|channel|group", re.IGNORECASE),
}


def normalize_phone(raw: str) -> str | None:
    """«8 928 672-92-88» → «+79286729288». 10–15 цифр, иначе не телефон.

    Результат — только ASCII: полноширинные «＋７ ９１６…» и другие десятичные
    цифры Unicode приводятся к 0–9 (иначе «+７…» проходил дальше и падал на
    CHECK в базе, 25.09.2026).
    """
    text = unicodedata.normalize("NFKC", str(raw or ""))
    digits = "".join(str(unicodedata.decimal(ch)) for ch in text if ch.isdecimal())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits  # российский мобильный без кода страны
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def _labels_in(line: str) -> set[str]:
    return {name for name, pattern in _LABELS.items() if pattern.search(line)}


def _add(result: dict[str, Any], key: str, value: str) -> None:
    items = result.setdefault(key, [])
    if value not in items:
        items.append(value)


def _classify_url(url: str, labels: set[str], result: dict[str, Any]) -> None:
    link = url if url.lower().startswith("http") else "https://" + url
    host = re.sub(r"^https?://(www\.|m\.)?", "", link.lower()).split("/", 1)[0]
    path = link.split(host, 1)[-1].lstrip("/") if host in link.lower() else ""
    if host in ("wa.me",):
        phone = normalize_phone(path)
        _add(result, "whatsapp", phone or link)
    elif host == "max.ru":
        _add(result, "max", link)
    elif host in ("t.me", "telegram.me"):
        # «t.me/+…» и «joinchat» — приглашение в группу; метка «канал» — канал.
        if "channel" in labels or path.startswith(("+", "joinchat")):
            _add(result, "channels", link)
        else:
            _add(result, "telegram", link)
    elif host in ("vk.com", "vk.ru"):
        if "channel" in labels or re.match(r"(club|public|event)\d", path):
            _add(result, "channels", link)
        else:
            _add(result, "vk", link)
    elif host == "instagram.com":
        _add(result, "instagram", link.split("?", 1)[0])  # без igsh/utm-хвоста
    elif host == "tiktok.com":
        _add(result, "tiktok", link.split("?", 1)[0])
    else:
        _add(result, "links", link)


def parse_contacts(text: str) -> dict[str, Any]:
    """Разложить сообщение по полям. Пусто — если человек ответил «нет»."""
    raw = str(text or "").strip()
    if not raw or _NONE_RE.match(raw):
        return {}
    result: dict[str, Any] = {}
    for line in re.split(r"[\n;]+", raw):
        line = line.strip()
        if not line:
            continue
        labels = _labels_in(line)
        rest = line

        for email in _EMAIL_RE.findall(rest):
            _add(result, "email", email.lower())
        rest = _EMAIL_RE.sub(" ", rest)

        for url in _URL_RE.findall(rest):
            _classify_url(url.rstrip(".)"), labels, result)
        rest = _URL_RE.sub(" ", rest)

        for match in _PHONE_RE.findall(rest):
            phone = normalize_phone(match)
            if not phone:
                continue
            targets = [name for name in ("whatsapp", "max", "telegram") if name in labels]
            # «Телефон/WhatsApp: 8 928 …» — один номер для всего названного.
            if "phone" in labels or not targets:
                _add(result, "phone", phone)
            for name in targets:
                _add(result, name, phone)

        for handle in _HANDLE_RE.findall(rest):
            key = "whatsapp" if "whatsapp" in labels and "telegram" not in labels else "telegram"
            _add(result, key, "@" + handle)
    return result


def contacts_summary(contacts: dict[str, Any]) -> list[str]:
    """Строки для сообщения владельцу: что бот распознал."""
    titles = [
        ("phone", "Телефон"), ("whatsapp", "WhatsApp"), ("max", "MAX"), ("telegram", "Telegram"),
        ("email", "E-mail"), ("vk", "ВКонтакте"), ("instagram", "Instagram"), ("tiktok", "TikTok"),
        ("channels", "Каналы и группы"), ("links", "Другие ссылки"),
    ]
    return [f"{title}: {', '.join(contacts[key])}" for key, title in titles if contacts.get(key)]
