#!/usr/bin/env python3
"""Read-only shared-staging binding post-check. No writes, no apply."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from shared_staging_release_lib import (  # noqa: E402
    BindingRow,
    ReleaseGuardError,
    evaluate_postcheck,
    refuse_apply,
    validate_shared_staging_target,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only shared-staging binding post-check")
    parser.add_argument("--dsn", default=None)
    parser.add_argument(
        "--fetch-json",
        default=None,
        help="JSON snapshot of tenant_bot_bindings rows for tests/CI.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.apply:
        report = refuse_apply()
        print(report.to_json())
        return 2

    if args.fetch_json:
        payload = json.loads(Path(args.fetch_json).read_text(encoding="utf-8"))
        bindings = [BindingRow(**item) for item in payload.get("bindings") or payload]
        report = evaluate_postcheck(bindings)
        print(report.to_json())
        return 0 if report.ok else 2

    try:
        target = validate_shared_staging_target(args.dsn)
    except ReleaseGuardError as exc:
        print(
            json.dumps(
                {
                    "mode": "postcheck",
                    "ok": False,
                    "preconditions_failed": [str(exc)],
                    "bindings": [],
                    "tenants_affected": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "mode": "postcheck",
                "ok": False,
                "preconditions_failed": [
                    "live DSN SELECT is not enabled in Gate B2; pass --fetch-json"
                ],
                "target": {
                    "host": target.host,
                    "database": target.database,
                    "user": target.user,
                },
                "bindings": [],
                "tenants_affected": [],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
