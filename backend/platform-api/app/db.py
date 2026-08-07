from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.settings import get_settings

_pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    global _pool
    settings = get_settings()
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.database_pool_min,
            max_size=settings.database_pool_max,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=False,
        )
        await _pool.open()
        await _pool.wait()


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool is not initialized")
    return _pool


async def check_postgres() -> bool:
    try:
        pool = get_pool()
        async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
            async with conn.cursor() as cur:
                await cur.execute("select 1")
                row = await cur.fetchone()
                return bool(row)
    except Exception:
        return False


@asynccontextmanager
async def tenant_connection(tenant_id: str) -> AsyncIterator[AsyncConnection]:
    pool = get_pool()
    async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    "select set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
            yield conn


async def fetch_one(
    conn: AsyncConnection,
    query: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(query, params or ())
        return await cur.fetchone()


async def fetch_all(
    conn: AsyncConnection,
    query: str,
    params: tuple[Any, ...] | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(query, params or ())
        rows = await cur.fetchall()
        return list(rows)
