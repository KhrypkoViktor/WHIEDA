"""USD→BYN reference rate of the National Bank of Belarus for price display.

Prices stay in USD in the data plane (Gate E1: as-is, no FX). A tenant that
shows a BYN equivalent renders it at answer time with the NBRB rate and the
rate date, never stores it. Network failures return None so the USD price is
still shown without an equivalent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

NBRB_USD_URL = "https://api.nbrb.by/exrates/rates/431?periodicity=0"
CACHE_TTL_SEC = 6 * 60 * 60


@dataclass(frozen=True)
class NbrbRate:
    currency: str
    rate: Decimal
    scale: int
    rate_date: date

    @property
    def per_unit(self) -> Decimal:
        return self.rate / Decimal(self.scale or 1)


def parse_nbrb_payload(payload: dict[str, Any]) -> NbrbRate:
    raw_date = str(payload.get("Date") or "")
    rate_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
    return NbrbRate(
        currency=str(payload.get("Cur_Abbreviation") or "USD"),
        rate=Decimal(str(payload["Cur_OfficialRate"])),
        scale=int(payload.get("Cur_Scale") or 1),
        rate_date=rate_date,
    )


def fetch_usd_byn_rate(*, timeout_sec: float = 5.0) -> NbrbRate | None:
    try:
        response = httpx.get(NBRB_USD_URL, timeout=timeout_sec)
        response.raise_for_status()
        return parse_nbrb_payload(response.json())
    except Exception:
        logger.warning("nbrb_rate_unavailable", exc_info=True)
        return None


_cache: dict[str, Any] = {"rate": None, "fetched_at": 0.0}


def usd_byn_rate(
    *,
    fetch: Callable[[], NbrbRate | None] = fetch_usd_byn_rate,
    now: Callable[[], float] = time.monotonic,
) -> NbrbRate | None:
    if _cache["rate"] is not None and now() - _cache["fetched_at"] < CACHE_TTL_SEC:
        return _cache["rate"]
    rate = fetch()
    if rate is not None:
        _cache["rate"] = rate
        _cache["fetched_at"] = now()
        return rate
    return _cache["rate"]


def reset_rate_cache_for_tests() -> None:
    _cache["rate"] = None
    _cache["fetched_at"] = 0.0


def byn_equivalent(usd_amount: Decimal | str | float, rate: NbrbRate) -> Decimal:
    amount = Decimal(str(usd_amount))
    return (amount * rate.per_unit).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_byn_equivalent(usd_amount: Decimal | str | float, rate: NbrbRate | None) -> str | None:
    """'≈ 112,54 BYN по курсу НБРБ на 16.09.2026' or None when no rate is known."""
    if rate is None:
        return None
    value = byn_equivalent(usd_amount, rate)
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"≈ {text} BYN по курсу НБРБ на {rate.rate_date.strftime('%d.%m.%Y')}"
