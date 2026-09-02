from __future__ import annotations

from typing import Any, Protocol

from app.markets.sync.validate import SheetBundle


class SheetsSource(Protocol):
    def fetch(self) -> SheetBundle: ...


class FixtureSheetsSource:
    """Local/staging bundle — no Google dependency."""

    def __init__(self, bundle: SheetBundle | None = None) -> None:
        self._bundle = bundle or _default_fixture_bundle()

    def fetch(self) -> SheetBundle:
        return self._bundle


def _default_fixture_bundle() -> SheetBundle:
    return SheetBundle(
        markets=[
            {"market_id": "ru", "country_iso": "RU", "country_name": "Россия", "currency_code": "RUB",
             "price_visibility": "full", "is_active": True, "is_default": False},
            {"market_id": "by", "country_iso": "BY", "country_name": "Беларусь", "currency_code": "BYN",
             "price_visibility": "full", "is_active": True, "is_default": False},
            {"market_id": "global", "country_iso": "*", "country_name": "Другая страна", "currency_code": "RUB",
             "price_visibility": "full", "is_active": True, "is_default": True},
        ],
        ref_structures=[
            {"ref_code": "fixture-ref-alpha", "structure_id": "structure-alpha", "is_active": True},
            {"ref_code": "fixture-ref-beta", "structure_id": "structure-beta", "is_active": True},
        ],
        service_centers=[
            {
                "center_id": "minsk-alpha-01", "structure_id": "structure-alpha", "country_iso": "BY",
                "city": "Минск", "region": "Минск / Минская область",
                "title": "SC Alpha Minsk", "manager_name": "Manager Alpha", "address": "Alpha addr",
                "telegram": "@alpha_minsk", "phone": "+375290000001", "working_hours": "10-19",
                "map_url_yandex": "https://yandex.ru/maps/-/alpha", "is_active": True, "priority": 100,
            },
            {
                "center_id": "minsk-beta-01", "structure_id": "structure-beta", "country_iso": "BY",
                "city": "Минск", "region": "Минск / Минская область",
                "title": "SC Beta Minsk", "manager_name": "Manager Beta", "address": "Beta addr",
                "telegram": "beta_minsk", "phone": "+375290000002", "working_hours": "11-18",
                "map_url_yandex": "https://yandex.ru/maps/-/beta", "is_active": True, "priority": 100,
            },
        ],
        coverage=[
            {
                "structure_id": "structure-alpha", "country_iso": "RU", "city_alias": "новосибирск",
                "center_id": "moscow-alpha-01", "is_active": True, "priority": 50,
            },
        ],
        product_prices=[
            {"sku": "M015-00", "market_id": "ru", "currency_code": "RUB", "amount": 50000,
             "price_state": "active", "is_active": True},
            {"sku": "M015-00", "market_id": "by", "currency_code": "BYN", "amount": 1750,
             "price_state": "active", "is_active": True},
        ],
    )


class GoogleSheetsSource:
    """Pull tabs via service account (credentials path from env only)."""

    TAB_MAP = {
        "markets": "markets",
        "ref_structures": "ref_structures",
        "service_centers": "service_centers",
        "service_center_coverage": "service_center_coverage",
        "product_prices": "product_prices",
    }

    def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path

    def fetch(self) -> SheetBundle:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError(
                "Google Sheets sync requires google-api-python-client and google-auth "
                "(install optional deps: pip install whieda-platform-api[sheets])"
            ) from exc

        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=scopes
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheets = service.spreadsheets()

        def read_tab(tab: str) -> list[dict[str, Any]]:
            result = (
                sheets.values()
                .get(spreadsheetId=self.spreadsheet_id, range=f"{tab}!A:Z")
                .execute()
            )
            values = result.get("values") or []
            if not values:
                return []
            headers = [str(h).strip() for h in values[0]]
            rows: list[dict[str, Any]] = []
            for raw in values[1:]:
                if not any(str(cell).strip() for cell in raw):
                    continue
                row = {headers[i]: raw[i] if i < len(raw) else "" for i in range(len(headers))}
                rows.append(row)
            return rows

        return SheetBundle(
            markets=read_tab(self.TAB_MAP["markets"]),
            ref_structures=read_tab(self.TAB_MAP["ref_structures"]),
            service_centers=read_tab(self.TAB_MAP["service_centers"]),
            coverage=read_tab(self.TAB_MAP["service_center_coverage"]),
            product_prices=read_tab(self.TAB_MAP["product_prices"]),
        )


def service_account_email_from_file(credentials_path: str) -> str:
    import json
    from pathlib import Path

    data = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    return str(data.get("client_email") or "")
