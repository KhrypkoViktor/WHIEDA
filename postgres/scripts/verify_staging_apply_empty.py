#!/usr/bin/env python3
"""Prove full staging SQL apply order on an empty local database."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "postgres" / "sql"
APPLY_ORDER = [
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql",
]
SEED = ROOT / "postgres" / "scripts" / "staging_seed_whieda_journey_v1.sql"

REQUIRED_TABLES = [
    "tenants",
    "tenant_bot_bindings",
    "platform_session_context",
    "visitor_sessions",
    "identity_link_tokens",
    "onboarding_programs",
    "user_memory_facts",
    "pilot_daily_metrics",
    "data_retention_registry",
]


def run_psql(db: str, sql: str, *, host: str, port: int, user: str) -> None:
    env = os.environ.copy()
    cmd = ["psql", "-h", host, "-p", str(port), "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-c", sql]
    subprocess.run(cmd, check=True, env=env, capture_output=True, text=True)


def run_psql_file(db: str, path: Path, *, host: str, port: int, user: str) -> None:
    cmd = ["psql", "-h", host, "-p", str(port), "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-f", str(path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--db", default="whieda_platform_staging_verify")
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args()

    db = args.db
    print(f"=== staging apply verify on empty DB: {db} ===")

    try:
        run_psql("postgres", f"DROP DATABASE IF EXISTS {db};", host=args.host, port=args.port, user=args.user)
        run_psql("postgres", f"CREATE DATABASE {db};", host=args.host, port=args.port, user=args.user)

        for name in APPLY_ORDER:
            path = SQL_DIR / name
            if not path.exists():
                print(f"MISSING {path}")
                return 1
            print(f"  apply {name}")
            run_psql_file(db, path, host=args.host, port=args.port, user=args.user)

        if SEED.exists():
            print("  apply staging seed")
            run_psql_file(db, SEED, host=args.host, port=args.port, user=args.user)

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

        binding = subprocess.run(
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
                "SELECT count(*) FROM tenant_bot_bindings WHERE binding_id = 'whieda-advisor-bot';",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if binding.stdout.strip() != "1":
            print("FAIL: whieda-advisor-bot binding missing")
            return 1

        if missing:
            print(f"FAIL: missing tables: {missing}")
            return 1

        print(f"PASS: {len(APPLY_ORDER)} SQL files + seed; {len(REQUIRED_TABLES)} tables present")
        return 0
    finally:
        if not args.keep_db:
            try:
                run_psql("postgres", f"DROP DATABASE IF EXISTS {db};", host=args.host, port=args.port, user=args.user)
            except subprocess.CalledProcessError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
