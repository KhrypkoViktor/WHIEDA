#!/usr/bin/env python3
"""Prove full staging SQL apply order on an empty local database."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from staging_proof_lib import (  # noqa: E402
    APPLY_ORDER,
    LOCAL_STAGING_PORT,
    SEED,
    SQL_DIR,
    validate_proof_db_name,
    validate_proof_host,
)

REQUIRED_TABLES = [
    "tenants",
    "tenant_bot_bindings",
    "website_leads",
    "referral_profiles",
    "platform_session_context",
    "visitor_sessions",
    "identity_link_tokens",
    "onboarding_programs",
    "user_memory_facts",
    "pilot_daily_metrics",
    "data_retention_registry",
    "telegram_update_inbox",
    "telegram_delivery_outbox",
    "tenant_advisor_profile",
]


def run_psql(db: str, sql: str, *, host: str, port: int, user: str) -> None:
    cmd = ["psql", "-h", host, "-p", str(port), "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-c", sql]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def run_psql_file(db: str, path: Path, *, host: str, port: int, user: str) -> None:
    cmd = ["psql", "-h", host, "-p", str(port), "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-f", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=LOCAL_STAGING_PORT)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--db", default="whieda_platform_staging_verify_manual")
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args()

    try:
        validate_proof_host(args.host)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    db = args.db
    try:
        validate_proof_db_name(db)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"=== staging apply verify on empty DB: {db} ===")

    try:
        run_psql("postgres", f'DROP DATABASE IF EXISTS "{db}";', host=args.host, port=args.port, user=args.user)
        run_psql("postgres", f'CREATE DATABASE "{db}";', host=args.host, port=args.port, user=args.user)

        for name in APPLY_ORDER:
            path = SQL_DIR / name
            if not path.exists():
                print(f"MISSING {path}")
                return 1
            print(f"  apply {name}")
            run_psql_file(db, path, host=args.host, port=args.port, user=args.user)

        if SEED.exists():
            print(
                "  skip staging_seed_whieda_journey_v1.sql "
                "(stale vs platform_onboarding_v1.sql; Gate B1 does not invent replacement seed)"
            )

        missing = []
        for table in REQUIRED_TABLES:
            out = subprocess.run(
                [
                    "psql",
                    "-h",
                    args.host,
                    "-p",
                    str(args.port),
                    "-U",
                    args.user,
                    "-d",
                    db,
                    "-tAc",
                    f"SELECT to_regclass('public.{table}') IS NOT NULL;",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            if out.stdout.strip() != "t":
                missing.append(table)

        if missing:
            print(f"FAIL: missing tables: {missing}")
            return 1

        columns = subprocess.run(
            [
                "psql",
                "-h",
                args.host,
                "-p",
                str(args.port),
                "-U",
                args.user,
                "-d",
                db,
                "-tAc",
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='tenant_bot_bindings' "
                "ORDER BY column_name;",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        present = {line.strip() for line in columns.stdout.splitlines() if line.strip()}
        required_cols = {
            "binding_id",
            "tenant_id",
            "webhook_secret_ref",
            "bot_token_ref",
            "bot_username",
            "processing_mode",
            "status",
        }
        missing_cols = sorted(required_cols - present)
        if missing_cols:
            print(f"FAIL: tenant_bot_bindings missing columns: {missing_cols}")
            return 1

        nsp = subprocess.run(
            [
                "psql",
                "-h",
                args.host,
                "-p",
                str(args.port),
                "-U",
                args.user,
                "-d",
                db,
                "-tAc",
                "SELECT count(*) FROM tenants WHERE tenant_id = 'nsp-maxim';",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if nsp.stdout.strip() != "0":
            print("FAIL: Core apply created nsp-maxim tenant")
            return 1

        print(f"PASS: {len(APPLY_ORDER)} SQL files; {len(REQUIRED_TABLES)} tables present")
        return 0
    finally:
        if not args.keep_db:
            try:
                run_psql("postgres", f'DROP DATABASE IF EXISTS "{db}";', host=args.host, port=args.port, user=args.user)
            except subprocess.CalledProcessError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
