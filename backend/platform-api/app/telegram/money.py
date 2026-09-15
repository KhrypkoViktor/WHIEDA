"""One way to show money to people in the bot.

WWC$ is the ecosystem's own currency: 1 WWC$ = 100 ₽ = 1 W$ (WHIEDA's cabinet
currency; same rate, different name so nobody compares two balances). Amounts
are stored in hundredths (``amount_minor``); the word «баллы» is not used
anywhere a person can read it — the owner's rule of 2026-09-13.
"""

from __future__ import annotations

DISPLAY_CURRENCY = "WWC$"


def format_minor(amount_minor: int) -> str:
    """``1250`` → ``12,50``; ``3000`` → ``30``; ``-600`` → ``−6``. Thousands with a space."""
    value = int(amount_minor)
    sign = "−" if value < 0 else ""
    major, minor = divmod(abs(value), 100)
    text = f"{major:,}".replace(",", " ")
    if minor:
        text += f",{minor:02d}"
    return sign + text


def wwc(amount_minor: int) -> str:
    """``600`` → ``6 WWC$``."""
    return f"{format_minor(amount_minor)} {DISPLAY_CURRENCY}"


def wwc_signed(amount_minor: int) -> str:
    """``600`` → ``+6 WWC$``, ``-600`` → ``−6 WWC$`` — for ledgers and notices."""
    return ("+" if int(amount_minor) > 0 else "") + wwc(amount_minor)


def money(amount_minor: int, currency: str) -> str:
    """Any stored currency for display: WUSD reads as WWC$, RUB as ₽."""
    code = str(currency or "").upper()
    if code == "WUSD":
        return wwc(amount_minor)
    unit = "₽" if code == "RUB" else code
    return f"{format_minor(amount_minor)} {unit}"


RUB_PER_WWC = 100


def both(amount_minor: int, currency: str) -> str:
    """The price in the payer's currency with the other one in brackets —
    Belarus pays in WWC$ and does not think in roubles, Russia the reverse.
    ``3000 RUB`` → «3 000 ₽ (30 WWC$)», ``3000 WUSD`` → «30 WWC$ (3 000 ₽)»."""
    code = str(currency or "").upper()
    if code == "WUSD":
        return f"{wwc(amount_minor)} ({format_minor(int(amount_minor) * RUB_PER_WWC)} ₽)"
    if code == "RUB":
        return f"{format_minor(amount_minor)} ₽ ({wwc(int(amount_minor) // RUB_PER_WWC)})"
    return money(amount_minor, currency)


# Payment details. The phone is wrapped in <code>: in Telegram a tap on it
# copies the number, so the partner does not retype it.
PHONE_RU = "+79282372677"
PAYMENT_RU = f"Переведите оплату по номеру <code>{PHONE_RU}</code>, Т-Банк."
PAYMENT_BY = "Переведите оплату на аккаунт <code>SUNRAYSWORD</code>."
