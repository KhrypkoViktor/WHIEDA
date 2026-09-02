"""Repository catalog browse SQL helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.advisor.sql import repository as repo


@pytest.mark.asyncio
async def test_count_catalog_products():
    conn = object()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(repo, "fetch_one", AsyncMock(return_value={"total": 14}))
        total = await repo.count_catalog_products(conn, "whieda")
    assert total == 14


@pytest.mark.asyncio
async def test_list_catalog_products_clamps_page_size():
    conn = object()
    captured: dict = {}

    async def fake_fetch_all(_conn, sql, params):
        captured["params"] = params
        return [{"sku": "A", "canonical_name": "Alpha"}]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(repo, "fetch_all", fake_fetch_all)
        rows = await repo.list_catalog_products(conn, "whieda", page=2, page_size=99)
    assert rows[0]["sku"] == "A"
    assert captured["params"] == ("whieda", 8, 8)


@pytest.mark.asyncio
async def test_resolve_catalog_product_by_sku():
    conn = object()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            repo,
            "fetch_one",
            AsyncMock(return_value={"sku": "LOCAL-ACT", "canonical_name": "Активатор клеток"}),
        )
        row = await repo.resolve_catalog_product_by_sku(conn, "whieda", "LOCAL-ACT")
    assert row["sku"] == "LOCAL-ACT"
