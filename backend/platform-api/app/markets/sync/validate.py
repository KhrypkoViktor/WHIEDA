from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from app.markets.constants import ALLOWED_MARKET_IDS


@dataclass
class SheetBundle:
    markets: list[dict[str, Any]]
    ref_structures: list[dict[str, Any]]
    service_centers: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    product_prices: list[dict[str, Any]]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "да"}


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", ".").replace(" ", ""))
    except InvalidOperation as exc:
        raise ValueError("invalid amount") from exc
    if amount < 0:
        raise ValueError("negative amount")
    return amount


def _valid_url(value: str | None) -> bool:
    if not value:
        return True
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _valid_telegram(value: str | None) -> bool:
    if not value:
        return True
    handle = str(value).strip().lstrip("@")
    return bool(re.fullmatch(r"[\w\d_]{3,32}", handle))


def validate_sheet_bundle(bundle: SheetBundle) -> list[str]:
    errors: list[str] = []

    market_ids = set()
    for idx, row in enumerate(bundle.markets, start=2):
        market_id = str(row.get("market_id") or "").strip().lower()
        if market_id not in ALLOWED_MARKET_IDS:
            errors.append(f"markets row {idx}: invalid market_id {market_id!r}")
            continue
        market_ids.add(market_id)
        currency = str(row.get("currency_code") or "").strip().upper()
        if market_id == "by" and currency != "BYN":
            errors.append(f"markets row {idx}: BY must use BYN")
        if market_id in {"ru", "global"} and currency != "RUB":
            errors.append(f"markets row {idx}: {market_id} must use RUB")

    active_price_keys: set[tuple[str, str]] = set()
    for idx, row in enumerate(bundle.product_prices, start=2):
        sku = str(row.get("sku") or "").strip()
        market_id = str(row.get("market_id") or "").strip().lower()
        if not sku or market_id not in ALLOWED_MARKET_IDS:
            errors.append(f"product_prices row {idx}: sku/market_id required")
            continue
        if _bool(row.get("is_active", True)):
            key = (sku, market_id)
            if key in active_price_keys:
                errors.append(f"product_prices row {idx}: duplicate active {sku}+{market_id}")
            active_price_keys.add(key)
        currency = str(row.get("currency_code") or "").strip().upper()
        if market_id == "by" and currency != "BYN":
            errors.append(f"product_prices row {idx}: BY price must be BYN")
        if market_id in {"ru", "global"} and currency != "RUB":
            errors.append(f"product_prices row {idx}: RUB market must use RUB")
        try:
            _parse_amount(row.get("amount"))
        except ValueError:
            errors.append(f"product_prices row {idx}: invalid amount")

    center_ids = {str(r.get("center_id") or "").strip() for r in bundle.service_centers}
    for idx, row in enumerate(bundle.service_centers, start=2):
        center_id = str(row.get("center_id") or "").strip()
        if not center_id:
            errors.append(f"service_centers row {idx}: center_id required")
            continue
        for field in ("structure_id", "country_iso", "city", "title", "manager_name", "address"):
            if not str(row.get(field) or "").strip():
                errors.append(f"service_centers row {idx}: missing {field}")
        if not _valid_url(row.get("photo_url")):
            errors.append(f"service_centers row {idx}: invalid photo_url")
        if not _valid_url(row.get("map_url_yandex")):
            errors.append(f"service_centers row {idx}: invalid map_url_yandex")
        if not _valid_url(row.get("map_url_google")):
            errors.append(f"service_centers row {idx}: invalid map_url_google")
        if not _valid_telegram(row.get("telegram")):
            errors.append(f"service_centers row {idx}: invalid telegram")

    for idx, row in enumerate(bundle.coverage, start=2):
        center_id = str(row.get("center_id") or "").strip()
        if center_id and center_id not in center_ids:
            errors.append(f"service_center_coverage row {idx}: unknown center_id {center_id}")

    for idx, row in enumerate(bundle.ref_structures, start=2):
        if not str(row.get("ref_code") or "").strip():
            errors.append(f"ref_structures row {idx}: ref_code required")
        if not str(row.get("structure_id") or "").strip():
            errors.append(f"ref_structures row {idx}: structure_id required")

    return errors
