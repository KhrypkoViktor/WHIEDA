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
