#!/usr/bin/env python3
"""Refresh advisor gap review queue from interaction_events (local/staging only)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from app.admin.gap_review.guard import assert_local_database_url
from app.admin.gap_review.service import run_refresh
from app.db import close_pool, init_pool
from app.settings import get_settings

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from asyncio_util import run_async  # noqa: E402


async def _main(tenant: str, dry_run: bool) -> int:
    settings = get_settings()
    assert_local_database_url(settings.database_url)
    await init_pool()
    try:
        result = await run_refresh(tenant, dry_run=dry_run)
    finally:
        await close_pool()
    print(
        f"tenant={result['tenant_id']} dry_run={result['dry_run']} "
        f"inserted={result['inserted']} updated={result['updated']} "
        f"unchanged={result['unchanged']} skipped={result['skipped']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh advisor gap review queue")
    parser.add_argument("--tenant", required=True, help="Tenant id, e.g. whieda")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.tenant.strip():
        print("FAIL: --tenant is required", file=sys.stderr)
        return 1
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL",
        "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core",
    )
    return run_async(_main(args.tenant.strip(), args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
