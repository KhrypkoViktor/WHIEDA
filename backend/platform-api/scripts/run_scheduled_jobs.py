"""Run scheduled platform jobs once (local/staging cron substitute)."""

from __future__ import annotations

import asyncio

from app.db import close_pool, init_pool
from app.jobs.scheduled import process_all_tenant_jobs


async def main() -> None:
    await init_pool()
    try:
        stats = await process_all_tenant_jobs()
        print(f"scheduled_jobs: {stats}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
