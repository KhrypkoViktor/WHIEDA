"""Country-specific partner subscription reminder text."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.subscriptions.service import GRACE_PERIOD

MOSCOW = ZoneInfo("Europe/Moscow")
VICTOR_TELEGRAM_URL = "https://t.me/sunraysword"

_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

_PAYMENT_LINES = {
    "BY": "Беларусь: 30 WWC$ : SUNRAYSWORD",
    "RU": "Россия: 3 000 RUB по номеру +79282372677 Т-Банк",
}


def normalize_payment_country(value: str) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "BY": "BY",
        "RB": "BY",
        "РБ": "BY",
        "БЕЛАРУСЬ": "BY",
        "RU": "RU",
        "RF": "RU",
        "РФ": "RU",
        "РОССИЯ": "RU",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("country must be РБ/BY or РФ/RU") from exc


def _day_month(value: datetime) -> str:
    return f"{value.day} {_MONTHS[value.month]}"


def _midnight(value: datetime) -> bool:
    return value.hour == value.minute == value.second == value.microsecond == 0


def _access_lines(paid_until: datetime) -> tuple[str, str]:
    if paid_until.tzinfo is None or paid_until.utcoffset() is None:
        raise ValueError("paid_until must include timezone")
    boundary = paid_until.astimezone(MOSCOW)
    grace_until = (paid_until + GRACE_PERIOD).astimezone(MOSCOW)
    if _midnight(boundary) and _midnight(grace_until):
        paid_day = boundary - timedelta(days=1)
        grace_end = grace_until - timedelta(days=1)
        access = f"работает в тестовом доступе до {_day_month(paid_day)} включительно."
        if boundary.month == grace_end.month:
            grace = f"{boundary.day}-{_day_month(grace_end)} сайт продолжит работать."
        else:
            grace = (
                f"{_day_month(boundary)} - {_day_month(grace_end)} "
                "сайт продолжит работать."
            )
        suspension = f"с {_day_month(grace_until)}"
        return access, f"{grace} Если оплата не поступит, {suspension}"

    access = (
        "работает в тестовом доступе до "
        f"{_day_month(boundary)}, {boundary:%H:%M} МСК."
    )
    grace = (
        "После этого сайт продолжит работать ещё 3 дня. Если оплата не поступит, "
        f"с {_day_month(grace_until)}, {grace_until:%H:%M} МСК"
    )
    return access, grace


def build_partner_payment_reminder(
    *,
    recipient_name: str,
    hostname: str,
    country: str,
    paid_until: datetime,
) -> str:
    name = str(recipient_name or "").strip()
    host = str(hostname or "").strip().lower().removeprefix("https://").rstrip("/")
    if not name:
        raise ValueError("recipient_name is required")
    if not host:
        raise ValueError("hostname is required")
    country_code = normalize_payment_country(country)
    access, grace = _access_lines(paid_until)
    return "\n".join(
        [
            f"{name}, ваш персональный сайт {host} {access}",
            "",
            "Продление на 3 месяца стоит:",
            _PAYMENT_LINES[country_code],
            "",
            "Чтобы продлить сайт, переведите оплату удобным способом и пришлите "
            f"подтверждение Виктору: {VICTOR_TELEGRAM_URL}",
            "",
            f"{grace} адрес будет временно вести на общий сайт wwc.best. "
            "После оплаты персональный сайт снова включится.",
        ]
    )
