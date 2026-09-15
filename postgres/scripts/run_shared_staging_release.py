#!/usr/bin/env python3
"""Shared-staging release harness. Default is --plan (no network, no writes)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from shared_staging_release_lib import (  # noqa: E402
    ReleaseGuardError,
    build_offline_plan,
    refuse_apply,
    validate_shared_staging_target,
    verify_binding_context_hash,
)
from shared_staging_release_lib import evaluate_preflight as _evaluate_preflight  # noqa: E402


def _print(report) -> int:
    print(report.to_json())
    return 0 if report.ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WHIEDA shared-staging release harness (plan/preflight only)"
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Offline plan (default). No network, no writes.",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Read-only preflight against --dsn. SELECT only.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rejected in this slice. Shared staging writes are forbidden.",
    )
    parser.add_argument("--dsn", default=None, help="postgresql://user@host:port/dbname")
    parser.add_argument(
        "--fetch-json",
        default=None,
        help="Path to a JSON snapshot for tests; never used to write.",
    )
    args = parser.parse_args(argv)

    if args.apply:
        return _print(refuse_apply())

    if args.preflight:
        try:
            target = validate_shared_staging_target(args.dsn)
            verify_binding_context_hash()
        except ReleaseGuardError as exc:
            print(
                json.dumps(
                    {
                        "mode": "preflight",
                        "ok": False,
                        "preconditions_failed": [str(exc)],
                        "would_apply": [],
                        "bindings": [],
                        "tenants_affected": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        if not args.fetch_json:
            print(
                json.dumps(
                    {
                        "mode": "preflight",
                        "ok": False,
                        "preconditions_failed": [
                            "live DSN SELECT is not enabled in Gate B2; "
                            "pass --fetch-json snapshot or wait for the owner apply gate"
                        ],
                        "target": {
                            "host": target.host,
                            "database": target.database,
                            "user": target.user,
                        },
                        "would_apply": [],
                        "bindings": [],
                        "tenants_affected": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        snapshot = json.loads(Path(args.fetch_json).read_text(encoding="utf-8"))
        from shared_staging_release_lib import BindingRow

        report = _evaluate_preflight(
            target,
            current_database=snapshot["current_database"],
            current_user=snapshot["current_user"],
            is_superuser=bool(snapshot.get("is_superuser")),
            tables_present=snapshot.get("tables_present") or {},
            binding_columns=snapshot.get("binding_columns") or [],
            bindings=[BindingRow(**item) for item in snapshot.get("bindings") or []],
            tenants=snapshot.get("tenants") or [],
        )
        return _print(report)

    # Default: --plan, even if the flag was omitted.
    return _print(build_offline_plan())


if __name__ == "__main__":
    raise SystemExit(main())
