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
