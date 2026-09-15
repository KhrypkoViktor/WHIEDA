#!/usr/bin/env python3
"""Manual WWC markets Google Sheets → Postgres sync (staging/production)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db import close_pool, init_pool
from app.markets.service import run_markets_sync
from app.markets.sync.sources import service_account_email_from_file
from app.settings import get_settings


async def _main(tenant_id: str) -> int:
    await init_pool()
    try:
        settings = get_settings()
        if settings.wwc_markets_sync_mode == "google" and settings.wwc_markets_google_credentials_path:
            email = service_account_email_from_file(settings.wwc_markets_google_credentials_path)
            print(f"google_service_account: {email}")
        result = await run_markets_sync(tenant_id, manual=True)
        print(result)
        return 0 if result.get("ok") else 1
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync WWC markets data from Google Sheets")
    parser.add_argument("--tenant", default="whieda")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.tenant)))


if __name__ == "__main__":
    main()
