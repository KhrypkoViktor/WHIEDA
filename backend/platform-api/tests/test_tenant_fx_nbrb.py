"""NBRB USD→BYN equivalent for tenant price display (no FX stored, rate date shown)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.tenants import fx_nbrb

PAYLOAD = {
    "Cur_ID": 431,
    "Date": "2026-09-16T00:00:00",
    "Cur_Abbreviation": "USD",
    "Cur_Scale": 1,
    "Cur_Name": "Доллар США",
    "Cur_OfficialRate": 3.0313,
}


@pytest.fixture(autouse=True)
def _reset_cache():
    fx_nbrb.reset_rate_cache_for_tests()
    yield
    fx_nbrb.reset_rate_cache_for_tests()


def test_parse_payload_reads_rate_scale_and_date():
    rate = fx_nbrb.parse_nbrb_payload(PAYLOAD)
    assert rate.currency == "USD"
    assert rate.rate == Decimal("3.0313")
    assert rate.scale == 1
    assert rate.rate_date == date(2026, 9, 16)
    assert rate.per_unit == Decimal("3.0313")


def test_byn_equivalent_rounds_to_kopecks():
    rate = fx_nbrb.parse_nbrb_payload(PAYLOAD)
    assert fx_nbrb.byn_equivalent("37.13", rate) == Decimal("112.55")
    assert fx_nbrb.byn_equivalent(Decimal("40.95"), rate) == Decimal("124.13")


def test_format_mentions_nbrb_and_rate_date():
    rate = fx_nbrb.parse_nbrb_payload(PAYLOAD)
    text = fx_nbrb.format_byn_equivalent("37.13", rate)
    assert text == "≈ 112,55 BYN по курсу НБРБ на 16.09.2026"
    assert fx_nbrb.format_byn_equivalent("37.13", None) is None


def test_scaled_currency_divides_by_scale():
    rate = fx_nbrb.parse_nbrb_payload({**PAYLOAD, "Cur_Scale": 100, "Cur_OfficialRate": 303.13})
    assert fx_nbrb.byn_equivalent("1", rate) == Decimal("3.03")


def test_cache_serves_rate_within_ttl_and_keeps_last_on_failure():
    calls: list[int] = []
    clock = {"t": 1000.0}

    def fetch():
        calls.append(1)
        return fx_nbrb.parse_nbrb_payload(PAYLOAD) if len(calls) == 1 else None

    first = fx_nbrb.usd_byn_rate(fetch=fetch, now=lambda: clock["t"])
    second = fx_nbrb.usd_byn_rate(fetch=fetch, now=lambda: clock["t"] + 60)
    assert first is second
    assert len(calls) == 1

    clock["t"] += fx_nbrb.CACHE_TTL_SEC + 1
    third = fx_nbrb.usd_byn_rate(fetch=fetch, now=lambda: clock["t"])
    assert len(calls) == 2
    assert third is first  # fetch failed → last known rate is kept


def test_no_rate_without_network(monkeypatch):
    monkeypatch.setattr(fx_nbrb.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert fx_nbrb.fetch_usd_byn_rate() is None
    assert fx_nbrb.usd_byn_rate() is None
