"""Partner products, personal prices and the owner's multi-line payment command.

Products (owner, 14–15.09.2026):
  PRO (сайт)            platform_subscription   3/6/12 мес   30/54/96 WWC$
  CLUB                  club_subscription       3 мес        120 WWC$ (40/мес)
  настройка сайта       site_setup              разово       20 WWC$
  пакет PRO + клуб      bundle_pro_club         3 мес        105 WWC$ = PRO 30 + клуб 75 (акция)

One received transfer may pay several lines. The owner writes:

    оплата ref:olga-samtsova
    PRO 15 WWC$ 3
    клуб 75 WWC$ 3 акция
    настройка 0 WWC$ акция
    получено 90 WWC$

or the old one-liner ``оплата ref:code 30 WWC$ 3`` (= one PRO line), or a
bundle line ``пакет 105 WWC$`` which expands into PRO 30 + клуб 75 (promo).
A line whose amount differs from the price (personal price included) must
say «акция», and «получено» must equal the sum of lines — otherwise nothing
is recorded and the bot explains what does not add up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from app.db import fetch_one
from app.subscriptions.service import SubscriptionError

PRODUCT_LABELS = {
    "platform_subscription": "PRO (сайт)",
    "club_subscription": "CLUB",
    "site_setup": "настройка сайта",
}
RECURRING = {"platform_subscription", "club_subscription"}
_PRODUCT_WORDS = {
    "pro": "platform_subscription", "платформа": "platform_subscription", "сайт": "platform_subscription",
    "клуб": "club_subscription", "club": "club_subscription",
    "настройка": "site_setup", "настройка сайта": "site_setup", "setup": "site_setup",
    "пакет": "bundle_pro_club", "bundle": "bundle_pro_club",
}
BUNDLE_LINES = (("platform_subscription", 3000), ("club_subscription", 7500))
RUB_PER_WWC = 100

_IDENTIFIER = r"(ref:[A-Za-z0-9][A-Za-z0-9_-]{0,62}|@[A-Za-z0-9_]{3,64})"
_AMOUNT = r"([0-9]+(?:[.,][0-9]{1,2})?)"
_CURRENCY = r"(RUB|₽|WUSD|WWC\$|W\$)"
_HEAD_RE = re.compile(rf"^(?:оплата|/pay)\s+{_IDENTIFIER}(?:\s+{_AMOUNT}\s+{_CURRENCY}(?:\s+(3|6|12))?)?\s*$", re.IGNORECASE)
_LINE_RE = re.compile(
    rf"^(?P<product>pro|платформа|сайт|клуб|club|настройка(?:\s+сайта)?|setup|пакет|bundle)\s+"
    rf"{_AMOUNT}\s+{_CURRENCY}(?:\s+(?P<months>3|6|12))?(?:\s+(?P<note>.+))?$",
    re.IGNORECASE,
)
_RECEIVED_RE = re.compile(rf"^получено\s+{_AMOUNT}\s+{_CURRENCY}\s*$", re.IGNORECASE)
# Part of the sum paid with the partner's own bonus points (always WWC$):
# «бонусами 5 WWC$» → «получено» is short of the lines by exactly that.
_BONUS_RE = re.compile(rf"^бонусами\s+{_AMOUNT}\s+(WUSD|WWC\$|W\$)\s*$", re.IGNORECASE)
# Marker item stored next to the lines in the intent; never a ledger row.
BONUS_OFFSET = "bonus_offset"

USAGE = (
    "Формат: оплата ref:code 30 WWC$ [3|6|12]\n"
    "или несколько строк:\n"
    "оплата ref:code\n"
    "PRO 30 WWC$ 3\n"
    "клуб 120 WWC$ 3\n"
    "настройка 20 WWC$\n"
    "получено 170 WWC$\n"
    "Пакет PRO+клуб: строка «пакет 105 WWC$». Цена не по тарифу — добавьте слово «акция».\n"
    "Часть суммы бонусами партнёра: строка «бонусами 5 WWC$» — тогда «получено» меньше строк ровно на неё."
)


@dataclass(frozen=True)
class PaymentLine:
    product_code: str
    amount_minor: int
    currency: str
    access_months: int
    promo: bool
    note: str


@dataclass(frozen=True)
class ParsedPayment:
    identifier: str
    lines: list[PaymentLine]
    received_minor: int | None
    currency: str
    bonus_minor: int = 0  # WWC$ minor units taken from the partner's bonus balance


def bonus_in_currency(bonus_minor: int, currency: str) -> int:
    """Bonus points are WWC$; against rouble lines they count at RUB_PER_WWC."""
    return int(bonus_minor) * (RUB_PER_WWC if currency == "RUB" else 1)


def _minor(amount: str) -> int:
    return int(round(float(amount.replace(",", ".")) * 100))


def _currency(raw: str) -> str:
    return "RUB" if raw.upper() in {"RUB", "₽"} else "WUSD"


def _product(word: str) -> str:
    return _PRODUCT_WORDS[re.sub(r"\s+", " ", word.strip().lower())]


def parse_payment_command(text: str) -> ParsedPayment:
    """Owner's text → identifier and lines. Raises SubscriptionError with USAGE."""
    raw_lines = [line.strip() for line in str(text or "").strip().splitlines() if line.strip()]
    if not raw_lines:
        raise SubscriptionError(USAGE)
    head = _HEAD_RE.fullmatch(raw_lines[0])
    if not head:
        raise SubscriptionError(USAGE)
    identifier = head.group(1)
    lines: list[PaymentLine] = []
    received: int | None = None
    bonus: int = 0
    currency: str | None = None

    def add(line: PaymentLine) -> None:
        nonlocal currency
        if currency is None:
            currency = line.currency
        elif currency != line.currency:
            raise SubscriptionError("Все строки одной оплаты должны быть в одной валюте.")
        lines.append(line)

    if head.group(2):
        add(PaymentLine("platform_subscription", _minor(head.group(2)), _currency(head.group(3)), int(head.group(4) or 3), False, ""))
    for raw in raw_lines[1:]:
        received_match = _RECEIVED_RE.fullmatch(raw)
        if received_match:
            received = _minor(received_match.group(1))
            if currency is not None and _currency(received_match.group(2)) != currency:
                raise SubscriptionError("«получено» должно быть в той же валюте, что и строки.")
            continue
        bonus_match = _BONUS_RE.fullmatch(raw)
        if bonus_match:
            bonus = _minor(bonus_match.group(1))
            if bonus <= 0:
                raise SubscriptionError("«бонусами» — сумма больше нуля, в WWC$.")
            continue
        m = _LINE_RE.fullmatch(raw)
        if not m:
            raise SubscriptionError(f"Не понял строку: «{raw}».\n{USAGE}")
        product = _product(m.group("product"))
        amount = _minor(m.group(2))
        cur = _currency(m.group(3))
        months = int(m.group("months") or (3 if product in RECURRING or product == "bundle_pro_club" else 0))
        note = (m.group("note") or "").strip()
        promo = "акци" in note.lower()
        if product == "bundle_pro_club":
            # The bundle is priced as a whole; its lines are PRO at list price and
            # CLUB at the promotional remainder, so the club line carries the promo.
            scale = RUB_PER_WWC if cur == "RUB" else 1
            pro_minor, club_minor = BUNDLE_LINES[0][1] * scale, amount - BUNDLE_LINES[0][1] * scale
            if club_minor <= 0:
                raise SubscriptionError("Пакет меньше цены PRO — так не бывает.")
            add(PaymentLine("platform_subscription", pro_minor, cur, months, False, note))
            add(PaymentLine("club_subscription", club_minor, cur, months, True, note or "пакет PRO + клуб"))
            continue
        if product == "site_setup":
            months = 0
        add(PaymentLine(product, amount, cur, months, promo, note))
    if not lines:
        raise SubscriptionError(USAGE)
    return ParsedPayment(identifier=identifier, lines=lines, received_minor=received, currency=currency or "WUSD", bonus_minor=bonus)


async def effective_price_minor(
    conn: Any, *, tenant_id: str, ref_code: str, product_code: str, access_months: int, currency: str
) -> tuple[int | None, str | None]:
    """List price for the line, or the partner's personal price. (price, reason)."""
    override = await fetch_one(
        conn,
        """
        select price_wusd_minor, reason from partner_price_overrides
        where tenant_id = %s and ref_code = %s and product_code = %s and revoked_at is null
        limit 1
        """,
        (tenant_id, ref_code, product_code),
    )
    if override:
        price = int(override["price_wusd_minor"])
        return (price * RUB_PER_WWC if currency == "RUB" else price), str(override["reason"])
    plan = await fetch_one(
        conn,
        """
        select price_wusd_minor, price_rub_minor from partner_subscription_plans
        where tenant_id = %s and product_code = %s and access_months = %s
          and active = true and valid_from <= now() and (valid_until is null or valid_until > now())
        limit 1
        """,
        (tenant_id, product_code, int(access_months)),
    )
    if not plan:
        return None, None
    return int(plan["price_rub_minor"] if currency == "RUB" else plan["price_wusd_minor"]), None


def validate_lines(
    lines: list[PaymentLine], prices: dict[str, int | None], received_minor: int | None, bonus_minor: int = 0
) -> list[str]:
    """Human-readable problems; empty list means the payment adds up:
    lines == received + bonus (bonus is WWC$, counted at RUB_PER_WWC against rouble lines)."""
    from app.telegram.money import money, wwc

    problems: list[str] = []
    for line in lines:
        label = PRODUCT_LABELS.get(line.product_code, line.product_code)
        price = prices.get(line.product_code)
        if price is None:
            problems.append(f"{label}: тариф на {line.access_months} мес. не найден.")
            continue
        if not line.promo and line.amount_minor != price:
            problems.append(
                f"{label}: {money(line.amount_minor, line.currency)} вместо {money(price, line.currency)}. "
                "Добавьте слово «акция» или исправьте сумму."
            )
    total = sum(line.amount_minor for line in lines)
    if lines and received_minor is not None:
        covered = received_minor + bonus_in_currency(bonus_minor, lines[0].currency)
        if covered != total:
            tail = f", бонусами {wwc(bonus_minor)}" if bonus_minor else ""
            problems.append(
                f"Сумма не сходится: строки {money(total, lines[0].currency)}, получено {money(received_minor, lines[0].currency)}{tail}."
            )
    return problems


def with_list_prices(lines: list[PaymentLine], prices: dict[str, int | None], bonus_minor: int = 0) -> list[dict[str, Any]]:
    """Serialisable lines with the list price attached (stored in the intent).
    A bonus offset rides along as a marker item so the intent table needs no new column."""
    items = [
        {
            "product_code": line.product_code,
            "amount_minor": line.amount_minor,
            "currency": line.currency,
            "access_months": line.access_months,
            "promo": line.promo,
            "note": line.note,
            "list_price_minor": prices.get(line.product_code),
        }
        for line in lines
    ]
    if bonus_minor:
        items.append({
            "product_code": BONUS_OFFSET, "amount_minor": int(bonus_minor), "currency": "WUSD",
            "access_months": 0, "promo": False, "note": "", "list_price_minor": None,
        })
    return items


def lines_from_json(raw: list[dict[str, Any]]) -> list[PaymentLine]:
    return [
        PaymentLine(
            product_code=str(item["product_code"]), amount_minor=int(item["amount_minor"]), currency=str(item["currency"]),
            access_months=int(item.get("access_months") or 0), promo=bool(item.get("promo")), note=str(item.get("note") or ""),
        )
        for item in raw
        if str(item.get("product_code")) != BONUS_OFFSET
    ]


def bonus_from_json(raw: list[dict[str, Any]]) -> int:
    return sum(int(item.get("amount_minor") or 0) for item in raw if str(item.get("product_code")) == BONUS_OFFSET)


def describe_lines(lines: list[PaymentLine], received_minor: int | None, bonus_minor: int = 0) -> list[str]:
    from app.telegram.money import money, wwc

    out = []
    for line in lines:
        label = PRODUCT_LABELS.get(line.product_code, line.product_code)
        term = f", {line.access_months} мес." if line.access_months else ""
        promo = " — акция" if line.promo else ""
        out.append(f"{label}: {money(line.amount_minor, line.currency)}{term}{promo}")
    if received_minor is not None:
        out.append(f"Получено: {money(received_minor, lines[0].currency)}")
    if bonus_minor:
        out.append(f"Бонусами: {wwc(bonus_minor)}")
    return out


def rescale(line: PaymentLine, **changes: Any) -> PaymentLine:
    return replace(line, **changes)
