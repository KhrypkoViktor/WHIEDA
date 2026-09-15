"""Honest tenant retail prices. No FX conversion, no partner formula."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
AMOUNT_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
RETAIL = "retail"
PARTNER = "partner"
KINDS = frozenset({RETAIL, PARTNER})
LEGACY_RETAIL_FIELDS = (
    ("retail_price_byn", "BYN", "legacy_retail_price_byn"),
    ("retail_price_rub", "RUB", "legacy_retail_price_rub"),
    ("retail_price_usd", "USD", "legacy_retail_price_usd"),
)
COUNTRY_CURRENCY = {"BY": "BYN", "RU": "RUB"}


def price_entry_sha256(entry: dict[str, str]) -> str:
    canonical = json.dumps(
        {
            "kind": entry["kind"],
            "amount": entry["amount"],
            "currency": entry["currency"],
            "source": entry["source"],
            "source_version": entry.get("source_version") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _amount_string(raw: Any) -> str | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        return None
    if not AMOUNT_RE.fullmatch(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return text


def _legacy_source(product: dict[str, Any], fallback: str) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    ref = str(source.get("ref") or "").strip()
    return ref or fallback


def _seal(entry: dict[str, str]) -> dict[str, str]:
    sealed = dict(entry)
    sealed["sha256"] = price_entry_sha256(sealed)
    return sealed


def _parse_declared_entry(raw: Any, *, sku: str) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    if not isinstance(raw, dict):
        return None, {
            "code": "price_entry_invalid",
            "message": "price entry must be an object",
            "sku": sku,
        }
    kind = str(raw.get("kind") or "").strip()
    currency = str(raw.get("currency") or "").strip()
    source = str(raw.get("source") or "").strip()
    source_version = str(raw.get("source_version") or raw.get("version") or "").strip()
    amount = _amount_string(raw.get("amount"))
    if kind not in KINDS:
        return None, {
            "code": "price_kind_invalid",
            "message": f"invalid price kind {kind!r}",
            "sku": sku,
        }
    if not CURRENCY_RE.fullmatch(currency):
        return None, {
            "code": "price_currency_invalid",
            "message": f"currency must be ISO uppercase, got {currency!r}",
            "sku": sku,
        }
    if amount is None:
        return None, {
            "code": "price_amount_invalid",
            "message": "amount must be a strictly positive decimal string",
            "sku": sku,
        }
    if kind == RETAIL and not source:
        return None, {
            "code": "price_source_missing",
            "message": "retail price source is required",
            "sku": sku,
        }
    entry = {
        "kind": kind,
        "amount": amount,
        "currency": currency,
        "source": source,
    }
    if source_version:
        entry["source_version"] = source_version
    return _seal(entry), None


def normalize_product_prices(product: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Return sealed price entries. Partner prices never create retail rows."""
    sku = str(product.get("sku") or "").strip()
    errors: list[dict[str, Any]] = []
    declared = product.get("prices")
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if declared is None:
        declared_rows: list[Any] = []
    elif not isinstance(declared, list):
        return [], [
            {
                "code": "prices_not_list",
                "message": "prices must be a list",
                "sku": sku or None,
            }
        ]
    else:
        declared_rows = declared
    for raw in declared_rows:
        entry, error = _parse_declared_entry(raw, sku=sku)
        if error:
            errors.append(error)
            continue
        assert entry is not None
        key = (entry["kind"], entry["currency"])
        if key in seen:
            errors.append(
                {
                    "code": "price_duplicate_currency",
                    "message": f"duplicate {entry['kind']} {entry['currency']}",
                    "sku": sku,
                }
            )
            continue
        seen.add(key)
        entries.append(entry)
    if errors:
        return [], errors
    for field, currency, fallback_source in LEGACY_RETAIL_FIELDS:
        if (RETAIL, currency) in seen:
            continue
        raw = product.get(field)
        if raw in (None, "", 0, 0.0):
            continue
        amount = _amount_string(raw)
        if amount is None:
            errors.append(
                {
                    "code": "price_amount_invalid",
                    "message": f"{field} is not a strictly positive decimal",
                    "sku": sku,
                }
            )
            continue
        entry = _seal(
            {
                "kind": RETAIL,
                "amount": amount,
                "currency": currency,
                "source": _legacy_source(product, fallback_source),
            }
        )
        seen.add((RETAIL, currency))
        entries.append(entry)
    if errors:
        return [], errors
    return entries, []


def retail_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in entries if item.get("kind") == RETAIL]


def partner_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in entries if item.get("kind") == PARTNER]


def has_confirmed_retail(entries: list[dict[str, str]]) -> bool:
    return bool(retail_entries(entries))


def legacy_byn_amount(entries: list[dict[str, str]]) -> Any:
    for item in retail_entries(entries):
        if item["currency"] == "BYN":
            text = item["amount"]
            return int(text) if text.isdigit() else text
    return None


def pick_display_retail(entries: list[dict[str, str]], country: str) -> dict[str, str] | None:
    retail = retail_entries(entries)
    if not retail:
        return None
    prefer = COUNTRY_CURRENCY.get(country)
    if prefer:
        for item in retail:
            if item["currency"] == prefer:
                return item
    return retail[0]


def format_retail_as_is(entry: dict[str, str] | None) -> str:
    if not entry:
        return ""
    return f"{entry['amount']} {entry['currency']}"
