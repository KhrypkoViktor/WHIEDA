from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql.engine import run_structured_query
from app.advisor.sql.solution_bundles import bundle_sku_groups, match_solution_bundle
from app.tenancy import TenantContext


ENERGY_BUNDLE = {
    "bundle_id": "bundle_energy_immunity",
    "bundle_name": "Батарейка на 100% и Железный Иммунитет",
    "aliases": "энергия; усталость; туман в голове; батарейка",
    "sku_groups": "F001-02|F002-02;F028-00",
    "active": True,
}
VESSELS_BUNDLE = {
    "bundle_id": "bundle_vessels_belly",
    "bundle_name": "Лёгкий живот и чистые сосуды",
    "aliases": "сосуды; вздутие; живот; кишечник",
    "sku_groups": "F024-00;F003-02;F011-02",
    "active": True,
}


def test_bundle_match_accepts_normal_russian_endings():
    assert match_solution_bundle("мало энергии", [ENERGY_BUNDLE]) == ENERGY_BUNDLE
    assert match_solution_bundle("тяжесть и вздутие после еды", [VESSELS_BUNDLE]) == VESSELS_BUNDLE
    assert match_solution_bundle("подбор", [ENERGY_BUNDLE]) is None


def test_bundle_sku_groups_preserve_alternatives():
    assert bundle_sku_groups(ENERGY_BUNDLE) == [["F001-02", "F002-02"], ["F028-00"]]


@pytest.mark.asyncio
async def test_active_bundle_returns_products_and_remembers_context(whieda_tenant):
    products = [
        {"sku": "F001-02", "canonical_name": "Эликсир Фохоу", "retail_price_byn": 100},
        {"sku": "F028-00", "canonical_name": "Капсулы Линчжи", "retail_price_byn": 200},
    ]

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.load_active_solution_bundles",
            AsyncMock(return_value=[ENERGY_BUNDLE]),
        ):
            with patch(
                "app.advisor.sql.engine.repo.load_products_by_skus",
                AsyncMock(return_value=products),
            ):
                with patch("app.advisor.sql.engine.session_ctx.merge_session_context", AsyncMock()) as merge:
                    result = await run_structured_query(
                        whieda_tenant,
                        {"question": "мало энергии", "session": "energy-session"},
                        "bundle-energy",
                    )

    assert result["answer_mode"] == "structured_solution_bundle"
    assert "Эликсир Фохоу" in result["answer_text"]
    assert result["product"]["skus"] == ["F001-02", "F028-00"]
    assert merge.await_args.args[3]["last_solution_bundle_id"] == "bundle_energy_immunity"


@pytest.mark.asyncio
async def test_other_tenant_never_receives_whieda_bundle():
    nsp_tenant = TenantContext(
        tenant_id="nsp-maxim", status="active", display_name="NSP", entitlements={}
    )
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch("app.advisor.sql.engine.repo.load_active_solution_bundles", AsyncMock(return_value=[])) as load:
                with patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=None)):
                    with patch("app.advisor.sql.engine.repo.find_canonical_question", AsyncMock(return_value=None)):
                        with patch("app.advisor.sql.engine.session_ctx.load_session_context", AsyncMock(return_value={})):
                            with patch("app.advisor.sql.engine.repo.count_catalog_products", AsyncMock(return_value=0)):
                                result = await run_structured_query(
                                    nsp_tenant, {"question": "мало энергии"}, "nsp-bundle"
                                )

    assert result["answer_mode"] != "structured_solution_bundle"
    assert load.await_args.args[1] == "nsp-maxim"
