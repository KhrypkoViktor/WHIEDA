"""Regression: the bundle product lookup must build its SQL (the column list
constant was lost in the 2026-09-02 restore and every bundle question crashed)."""

from __future__ import annotations

import pytest

from app.advisor.sql import repository as repo


class _Cursor:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params):
        self.log.append((query, params))

    async def fetchall(self):
        return []

    @property
    def description(self):
        return []


class _Conn:
    def __init__(self):
        self.log = []

    def cursor(self, *args, **kwargs):
        return _Cursor(self.log)


@pytest.mark.asyncio
async def test_load_products_by_skus_renders_sql_and_filters_by_client():
    conn = _Conn()
    rows = await repo.load_products_by_skus(conn, "whieda", ["ACT-001", "ACT-PRO"])
    assert rows == []
    query, params = conn.log[0]
    assert "select sku, canonical_name, retail_price_byn" in query
    assert "from advisor_structured_products" in query
    assert params[1] == ["ACT-001", "ACT-PRO"]


@pytest.mark.asyncio
async def test_load_products_by_skus_with_no_skus_skips_the_query():
    conn = _Conn()
    assert await repo.load_products_by_skus(conn, "whieda", []) == []
    assert conn.log == []


@pytest.mark.asyncio
async def test_plain_product_question_prefers_the_product_over_a_bundle_that_lists_it(whieda_tenant):
    """«сколько стоит активатор клеток» must not answer with a pet-care bundle
    just because the bundle's alias list contains the product name (8f08a74)."""
    from unittest.mock import AsyncMock, patch

    from app.advisor.sql.engine import run_structured_query

    bundle = {"bundle_id": "pets", "bundle_name": "Общая поддержка животного", "aliases": "активатор клеток", "active": True, "sku_groups": "ACT-001"}
    product = {"sku": "ACT-001", "canonical_name": "Активатор клеток", "retail_price_byn": 1750, "partner_price_byn": 1050, "pv": 300}
    format_price = AsyncMock(return_value={"answer_mode": "structured_price", "answer_text": "Активатор клеток: 1750 BYN"})
    with patch("app.advisor.sql.engine.tenant_connection") as tc, patch(
        "app.advisor.sql.engine.repo.load_active_solution_bundles", AsyncMock(return_value=[bundle])
    ), patch("app.advisor.sql.engine._resolve_product", AsyncMock(return_value=product)) as resolve, patch(
        "app.advisor.sql.engine.repo.load_products_by_skus", AsyncMock(return_value=[product])
    ) as load_bundle_products:
        tc.return_value.__aenter__.return_value = object()
        tc.return_value.__aexit__.return_value = False
        try:
            await run_structured_query(whieda_tenant, {"question": "сколько стоит активатор клеток", "session": "t", "country": "BY"}, "t")
        except Exception:  # noqa: BLE001 — the rest of the pipeline is not stubbed here
            pass
    resolve.assert_awaited()
    load_bundle_products.assert_not_awaited()


def test_scenario_wording_still_opens_the_bundle():
    from app.advisor.sql.solution_bundles import has_specific_bundle_context

    assert has_specific_bundle_context("стельки при плоскостопии")
    assert not has_specific_bundle_context("сколько стоит активатор клеток")
