"""WWC markets & service centers — spec §6 acceptance tests (no live Google)."""

from __future__ import annotations

import json
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.markets.constants import DEFAULT_STRUCTURE_ID, normalize_market_id, public_center_row
from app.markets.service import (
    build_catalog_prices,
    build_service_center_cities,
    build_service_centers,
    build_site_context,
    resolve_structure_id,
    run_markets_sync,
    run_scheduled_markets_sync,
)
from app.markets.sync.sources import FixtureSheetsSource
from app.markets.sync.validate import SheetBundle, validate_sheet_bundle

TENANT = "whieda"

ALPHA_MINSK = {
    "center_id": "minsk-alpha-01",
    "structure_id": "structure-alpha",
    "country_iso": "BY",
    "city": "Минск",
    "region": "Минск / Минская область",
    "title": "SC Alpha Minsk",
    "manager_name": "Manager Alpha",
    "address": "Alpha addr",
    "telegram": "alpha_minsk",
    "phone": "+375290000001",
    "working_hours": "10-19",
    "map_url_yandex": "https://yandex.ru/maps/-/alpha",
    "is_active": True,
    "priority": 100,
    "owner_id": "secret-owner-alpha",
    "tenant_id": TENANT,
}

BETA_MINSK = {
    **ALPHA_MINSK,
    "center_id": "minsk-beta-01",
    "structure_id": "structure-beta",
    "title": "SC Beta Minsk",
    "manager_name": "Manager Beta",
    "telegram": "beta_minsk",
    "phone": "+375290000002",
    "owner_id": "secret-owner-beta",
}

MOSCOW_ALPHA = {
    **ALPHA_MINSK,
    "center_id": "moscow-alpha-01",
    "structure_id": "structure-alpha",
    "country_iso": "RU",
    "city": "Москва",
    "region": "Москва",
    "title": "SC Moscow Alpha",
}

MARKETS = [
    {"market_id": "ru", "country_iso": "RU", "country_name": "Россия", "currency_code": "RUB",
     "price_visibility": "full", "is_active": True, "is_default": False},
    {"market_id": "by", "country_iso": "BY", "country_name": "Беларусь", "currency_code": "BYN",
     "price_visibility": "full", "is_active": True, "is_default": False},
    {"market_id": "global", "country_iso": "*", "country_name": "Другая страна", "currency_code": "RUB",
     "price_visibility": "full", "is_active": True, "is_default": True},
]

PRICES_RU = [
    {"sku": "M015-00", "market_id": "ru", "currency_code": "RUB", "amount": Decimal("50000"),
     "formatted": "50 000 ₽", "price_state": "active", "is_active": True,
     "updated_at": datetime(2026, 8, 9, tzinfo=timezone.utc)},
]
PRICES_BY = [
    {"sku": "M015-00", "market_id": "by", "currency_code": "BYN", "amount": Decimal("1750"),
     "formatted": "1 750 BYN", "price_state": "active", "is_active": True,
     "updated_at": datetime(2026, 8, 9, tzinfo=timezone.utc)},
]


def _forbidden_keys(obj: object, path: str = "") -> list[str]:
    bad = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            full = f"{path}.{key}" if path else key
            if key in {"owner_id", "watcher", "watchers", "tenant_id", "private_notes"}:
                bad.append(full)
            bad.extend(_forbidden_keys(value, full))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            bad.extend(_forbidden_keys(item, f"{path}[{idx}]"))
    return bad


@asynccontextmanager
async def _fake_conn(_tenant_id: str):
    yield AsyncMock()


def _patch_markets_db(
    *,
    structure_ref: dict[str, str] | None = None,
    centers_by_city: dict[tuple[str, str, str], list[dict]] | None = None,
    coverage: dict[tuple[str, str, str], dict] | None = None,
    prices: dict[str, list[dict]] | None = None,
):
    structure_ref = structure_ref or {
        "fixture-ref-alpha": "structure-alpha",
        "fixture-ref-beta": "structure-beta",
    }
    centers_by_city = centers_by_city or {}
    coverage = coverage or {}
    prices = prices or {"ru": PRICES_RU, "by": PRICES_BY, "global": []}

    async def fetch_structure_for_ref(conn, tenant_id, ref_code):
        sid = structure_ref.get(ref_code)
        return {"structure_id": sid, "ref_code": ref_code, "is_active": True} if sid else None

    async def fetch_market(conn, tenant_id, market_id):
        for row in MARKETS:
            if row["market_id"] == market_id:
                return dict(row)
        return None

    async def fetch_markets(conn, tenant_id):
        return [dict(r) for r in MARKETS]

    async def fetch_centers_for_city(conn, tenant_id, structure_id, country_iso, city):
        return centers_by_city.get((structure_id, country_iso, city.strip()), [])

    async def fetch_coverage_center(conn, tenant_id, structure_id, country_iso, city_alias):
        row = coverage.get((structure_id, country_iso, city_alias))
        if not row:
            return None
        return row

    async def fetch_cities(conn, tenant_id, structure_id, country_iso):
        seen = {}
        for (sid, iso, city), rows in centers_by_city.items():
            if sid == structure_id and iso == country_iso and rows:
                seen[city] = rows[0].get("region") or ""
        return [{"city": c, "region": r} for c, r in sorted(seen.items())]

    async def fetch_prices(conn, tenant_id, market_id, skus=None):
        rows = prices.get(market_id, [])
        if skus:
            return [r for r in rows if r["sku"] in skus]
        return list(rows)

    patches = [
        patch("app.markets.service.tenant_connection", _fake_conn),
        patch("app.markets.service.fetch_structure_for_ref", fetch_structure_for_ref),
        patch("app.markets.service.fetch_market", fetch_market),
        patch("app.markets.service.fetch_markets", fetch_markets),
        patch("app.markets.service.fetch_centers_for_city", fetch_centers_for_city),
        patch("app.markets.service.fetch_coverage_center", fetch_coverage_center),
        patch("app.markets.service.fetch_cities", fetch_cities),
        patch("app.markets.service.fetch_prices", fetch_prices),
        patch(
            "app.markets.service.fetch_referral_profile",
            AsyncMock(return_value={"enabled": True, "owner_id": "public-partner-1", "public_profile": {}}),
        ),
    ]
    stack = ExitStack()
    for item in patches:
        stack.enter_context(item)
    return stack


@pytest.mark.asyncio
async def test_alpha_and_beta_get_different_minsk_centers():
    centers = {
        ("structure-alpha", "BY", "Минск"): [ALPHA_MINSK],
        ("structure-beta", "BY", "Минск"): [BETA_MINSK],
    }
    with _patch_markets_db(centers_by_city=centers):
        alpha = await build_service_centers(
            TENANT, ref="fixture-ref-alpha", first_ref=None, country_iso="BY", city="Минск"
        )
        beta = await build_service_centers(
            TENANT, ref="fixture-ref-beta", first_ref=None, country_iso="BY", city="Минск"
        )
    assert alpha["centers"][0]["center_id"] == "minsk-alpha-01"
    assert beta["centers"][0]["center_id"] == "minsk-beta-01"


@pytest.mark.asyncio
async def test_cannot_get_alpha_center_via_beta_ref():
    centers = {("structure-alpha", "BY", "Минск"): [ALPHA_MINSK]}
    with _patch_markets_db(centers_by_city=centers):
        result = await build_service_centers(
            TENANT, ref="fixture-ref-beta", first_ref=None, country_iso="BY", city="Минск"
        )
    assert result["centers"] == []
    assert result["fallback"]["type"] == "no_registry_match"


@pytest.mark.asyncio
async def test_unknown_ref_uses_wwc_default():
    with _patch_markets_db(structure_ref={}):
        sid = await resolve_structure_id(TENANT, "unknown-ref", None)
        ctx = await build_service_centers(
            TENANT, ref="unknown-ref", first_ref=None, country_iso="BY", city="Минск"
        )
    assert sid == DEFAULT_STRUCTURE_ID
    assert ctx["structure_id"] == DEFAULT_STRUCTURE_ID


@pytest.mark.asyncio
async def test_market_currencies_ru_by_global():
    with _patch_markets_db():
        ru = await build_catalog_prices(TENANT, market_id="ru", skus=["M015-00"])
        by = await build_catalog_prices(TENANT, market_id="by", skus=["M015-00"])
        gl = await build_catalog_prices(TENANT, market_id="global", skus=["M015-00"])
    assert ru["currency_code"] == "RUB"
    assert by["currency_code"] == "BYN"
    assert gl["currency_code"] == "RUB"
    assert ru["prices"][0]["currency_code"] == "RUB"
    assert by["prices"][0]["currency_code"] == "BYN"
    assert gl["prices"][0]["currency_code"] == "RUB"


@pytest.mark.asyncio
async def test_global_price_visibility_not_consultation_only():
    with _patch_markets_db():
        result = await build_catalog_prices(TENANT, market_id="global", skus=None)
    assert result["price_visibility"] == "full"
    assert all(p.get("price_state") != "consultation" for p in result["prices"])


@pytest.mark.asyncio
async def test_missing_sku_returns_unavailable_ok_true():
    with _patch_markets_db():
        result = await build_catalog_prices(TENANT, market_id="ru", skus=["MISSING-SKU"])
    assert result["ok"] is True
    assert result["prices"][0]["price_state"] == "unavailable"
    assert result["prices"][0]["amount"] is None


@pytest.mark.asyncio
async def test_novosibirsk_without_coverage_gets_no_random_center():
    with _patch_markets_db(centers_by_city={("structure-alpha", "RU", "Москва"): [MOSCOW_ALPHA]}):
        result = await build_service_centers(
            TENANT, ref="fixture-ref-alpha", first_ref=None, country_iso="RU", city="Новосибирск"
        )
    assert result["centers"] == []
    assert result["fallback"]["type"] == "no_registry_match"


@pytest.mark.asyncio
async def test_explicit_coverage_returns_assigned_center():
    coverage = {("structure-alpha", "RU", "новосибирск"): MOSCOW_ALPHA}
    with _patch_markets_db(coverage=coverage):
        result = await build_service_centers(
            TENANT, ref="fixture-ref-alpha", first_ref=None, country_iso="RU", city="Новосибирск"
        )
    assert result["city_match"]["type"] == "coverage"
    assert result["centers"][0]["center_id"] == "moscow-alpha-01"


@pytest.mark.asyncio
async def test_invalid_market_and_city_do_not_500():
    with _patch_markets_db():
        ctx = await build_site_context(
            TENANT, ref="!!!", first_ref=None, market_id="xx", country_hint=None, city=None, page=None, sku=None
        )
        centers = await build_service_centers(
            TENANT, ref="!!!", first_ref=None, country_iso="BY", city="   "
        )
    assert ctx["ok"] is True
    assert ctx["market"]["id"] == "global"
    assert centers["ok"] is True


@pytest.mark.asyncio
async def test_failed_sync_does_not_publish_bundle():
    bad_bundle = SheetBundle(
        markets=[{"market_id": "ru", "currency_code": "EUR"}],
        ref_structures=[], service_centers=[], coverage=[], product_prices=[],
    )
    source = FixtureSheetsSource(bad_bundle)
    publish = AsyncMock()
    with (
        patch("app.markets.service.build_sheets_source", return_value=source),
        patch("app.markets.service.tenant_connection", _fake_conn),
        patch("app.markets.service.mark_sync_status", AsyncMock()),
        patch("app.markets.service.publish_bundle", publish),
    ):
        result = await run_markets_sync(TENANT, manual=True)
    assert result["ok"] is False
    publish.assert_not_called()


def test_validate_rejects_invalid_currency():
    bundle = SheetBundle(
        markets=[
            {"market_id": "by", "country_iso": "BY", "country_name": "BY", "currency_code": "RUB",
             "price_visibility": "full", "is_active": True, "is_default": False},
        ],
        ref_structures=[], service_centers=[], coverage=[], product_prices=[],
    )
    errors = validate_sheet_bundle(bundle)
    assert any("BYN" in e for e in errors)


def test_public_center_row_strips_internal_fields():
    public = public_center_row(ALPHA_MINSK)
    assert "owner_id" not in public
    assert "tenant_id" not in public
    assert public["center_id"] == "minsk-alpha-01"


@pytest.mark.asyncio
async def test_responses_have_no_owner_or_tenant_leaks():
    centers_map = {("structure-alpha", "BY", "Минск"): [ALPHA_MINSK]}
    with _patch_markets_db(centers_by_city=centers_map):
        ctx = await build_site_context(
            TENANT, ref="fixture-ref-alpha", first_ref=None, market_id="by",
            country_hint=None, city="Минск", page=None, sku=None,
        )
        centers = await build_service_centers(
            TENANT, ref="fixture-ref-alpha", first_ref=None, country_iso="BY", city="Минск"
        )
        cities = await build_service_center_cities(
            TENANT, ref="fixture-ref-alpha", first_ref=None, country_iso="BY"
        )
        prices = await build_catalog_prices(TENANT, market_id="by", skus=["M015-00"])
    for payload in (ctx, centers, cities, prices):
        assert _forbidden_keys(payload) == []


@pytest.mark.asyncio
async def test_http_site_context_alias(client):
    with patch(
        "app.markets.routes.build_site_context",
        AsyncMock(return_value={"ok": True, "market": {"id": "ru"}}),
    ):
        response = await client.get(
            "/api/site-context",
            headers={"Host": "wwc.best"},
            params={"market_id": "ru"},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_http_catalog_prices_alias(client):
    with patch(
        "app.markets.routes.build_catalog_prices",
        AsyncMock(return_value={"ok": True, "market_id": "global", "prices": []}),
    ):
        response = await client.get(
            "/api/catalog-prices",
            headers={"Host": "wwc.best"},
            params={"market_id": "global"},
        )
    assert response.status_code == 200


def test_normalize_market_id_fallback():
    assert normalize_market_id("invalid") == "global"
    assert normalize_market_id("by") == "by"


def test_fixture_source_has_alpha_beta_structures():
    bundle = FixtureSheetsSource().fetch()
    refs = {r["ref_code"]: r["structure_id"] for r in bundle.ref_structures}
    assert refs["fixture-ref-alpha"] == "structure-alpha"
    assert refs["fixture-ref-beta"] == "structure-beta"


def test_markets_seed_json_serializable_for_docs():
    """Guard: public payloads must json-encode (staging curl examples)."""
    public = public_center_row(ALPHA_MINSK)
    json.dumps(public, default=str)


# --- spec §6 items 12–16 ---


def test_lead_country_iso_by_maps_to_country_code_by():
    from app.leads.service import parse_lead_body

    lead = parse_lead_body(
        {
            "name": "Test",
            "contact": "+375000000",
            "product": "Spirulina",
            "idempotency_key": "by-iso-proof-1",
            "country_iso": "BY",
            "market_id": "by",
            "ref": "fixture-ref-alpha",
        },
        tenant_id="whieda",
    )
    assert lead.country_code == "BY"
    assert lead.metadata["country_iso"] == "BY"
    assert lead.active_ref_code == "fixture-ref-alpha"


def test_lead_country_code_takes_precedence_over_country_iso():
    from app.leads.service import parse_lead_body

    lead = parse_lead_body(
        {
            "name": "Test",
            "contact": "+375000000",
            "product": "Spirulina",
            "country_code": "BY",
            "country_iso": "RU",
            "idempotency_key": "by-iso-proof-2",
        },
        tenant_id="whieda",
    )
    assert lead.country_code == "BY"


@pytest.mark.asyncio
async def test_disabled_ref_does_not_resolve_from_ref_structures_only():
    structure_ref = {"orphan-structure-ref": "structure-orphan"}

    async def fetch_structure_for_ref(conn, tenant_id, ref_code):
        sid = structure_ref.get(ref_code)
        return {"structure_id": sid, "ref_code": ref_code, "is_active": True} if sid else None

    with ExitStack() as stack:
        stack.enter_context(patch("app.markets.service.tenant_connection", _fake_conn))
        stack.enter_context(patch("app.markets.service.fetch_structure_for_ref", fetch_structure_for_ref))
        stack.enter_context(
            patch("app.markets.service.fetch_referral_profile", AsyncMock(return_value=None))
        )
        sid = await resolve_structure_id(TENANT, "orphan-structure-ref", None)
    assert sid == DEFAULT_STRUCTURE_ID


@pytest.mark.asyncio
async def test_scheduler_iterates_active_tenants_not_hardcoded_whieda():
    from contextlib import asynccontextmanager

    run_sync = AsyncMock(side_effect=[{"ok": True}, {"ok": False}])

    @asynccontextmanager
    async def fake_pool_conn(**kwargs):
        yield AsyncMock()

    fake_pool = AsyncMock()
    fake_pool.connection = fake_pool_conn

    with (
        patch("app.db.get_pool", return_value=fake_pool),
        patch("app.markets.service.fetch_scheduled_sync_tenants", AsyncMock(return_value=["whieda", "test-acme"])),
        patch("app.markets.service.run_markets_sync", run_sync),
    ):
        result = await run_scheduled_markets_sync()

    assert result == {"tenants": 2, "synced": 1, "errors": 1}
    assert [call.args[0] for call in run_sync.await_args_list] == ["whieda", "test-acme"]


@pytest.mark.integration
def test_wwc_markets_rls_cross_tenant_postgres():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    proof = root / "postgres" / "scripts" / "run_local_staging_proof.py"
    if not proof.is_file():
        pytest.skip("staging proof script missing")

    proc = subprocess.run(
        [sys.executable, str(proof)],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=600,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode != 0 and "Docker required" in combined:
        pytest.skip("Docker not available")
    assert proc.returncode == 0, combined
    assert "wwc_markets: whieda visible=" in proc.stdout
    assert "wwc_markets: cross-tenant INSERT rejected" in proc.stdout

