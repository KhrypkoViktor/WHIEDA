#!/usr/bin/env python3
"""Export advisor gap review queue for human operators (local/staging only)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from app.admin.gap_review.guard import assert_local_database_url
from app.admin.gap_review.service import build_export
from app.db import close_pool, init_pool
from app.settings import get_settings

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from asyncio_util import run_async  # noqa: E402


async def _main(tenant: str, fmt: str, output: Path | None) -> int:
    settings = get_settings()
    assert_local_database_url(settings.database_url)
    await init_pool()
    try:
        payload = await build_export(tenant, fmt=fmt)
    finally:
        await close_pool()
    content = str(payload.get("content") or "")
    if output:
        output.write_text(content, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(content)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export advisor gap review queue")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--format", choices=("md", "csv"), default="md")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ.setdefault(
        "PLATFORM_DATABASE_URL",
        "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core",
    )
    return run_async(_main(args.tenant.strip(), args.format, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
