from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from app.db import close_pool, init_pool
from app.subscriptions.service import apply_initial_access_seed, preview_initial_access_seed


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--paid-until must include a UTC offset")
    return parsed


def _write_manifest(path: str | None, manifest: dict) -> None:
    body = json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n"
    if path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")
    print(body, end="")


async def _run(args: argparse.Namespace) -> int:
    await init_pool()
    try:
        if args.apply:
            if not args.expected_manifest_sha:
                raise SystemExit("--apply requires --expected-manifest-sha")
            manifest = await apply_initial_access_seed(
                args.tenant,
                paid_until=args.paid_until,
                expected_manifest_sha=args.expected_manifest_sha,
            )
        else:
            manifest = await preview_initial_access_seed(
                args.tenant,
                paid_until=args.paid_until,
            )
        _write_manifest(args.output, manifest)
        return 0
    finally:
        await close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed one-time access for existing partner sites")
    parser.add_argument("--tenant", default="whieda")
    parser.add_argument("--paid-until", type=_aware_datetime, required=True)
    parser.add_argument("--output")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-manifest-sha")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
