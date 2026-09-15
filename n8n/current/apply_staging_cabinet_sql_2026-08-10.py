"""Apply Owner Cabinet staging SQL (admin tables + staging bot binding)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from whieda_runtime_env import pg_env

ROOT = Path(__file__).resolve().parents[2]
SQL_FILES = [
    ROOT / "postgres" / "sql" / "platform_admin_cabinet_v1.sql",
    ROOT / "postgres" / "sql" / "platform_staging_cabinet_bot_binding_v1.sql",
]
PREREQ_MIGRATIONS = [
    ROOT / "postgres" / "sql" / "platform_tenant_registry_v1.sql",
]
BOT_BINDING_CONTEXT_MIGRATION = (
    ROOT / "postgres" / "sql" / "platform_bot_binding_context_v1.sql"
)
WHIEDA_BINDING_MIGRATION = (
    ROOT / "postgres" / "sql" / "platform_whieda_telegram_binding_v1.sql"
)


def run_sql_file(path: Path, *, dry_run: bool) -> None:
    if not path.exists():
        raise FileNotFoundError(path)
    print(f"{'[dry-run] ' if dry_run else ''}apply {path.name}")
    if dry_run:
        return
    psql = shutil.which("psql")
    if not psql:
        raise RuntimeError("psql not found in PATH")
    completed = subprocess.run(
        [psql, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        env=pg_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"Failed {path.name}:\n{completed.stderr}\n{completed.stdout}")


def table_exists(table: str, *, dry_run: bool) -> bool:
    if dry_run:
        return False
    psql = shutil.which("psql")
    if not psql:
        raise RuntimeError("psql not found in PATH")
    completed = subprocess.run(
        [
            psql,
            "-tAc",
            f"select to_regclass('public.{table}') is not null;",
        ],
        env=pg_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed.stdout.strip().lower() == "t"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--with-base",
        action="store_true",
        help="Also apply tenant registry + production telegram binding migrations if missing",
    )
    args = parser.parse_args()

    if args.with_base:
        for path in PREREQ_MIGRATIONS:
            run_sql_file(path, dry_run=args.dry_run)
    elif not args.dry_run and not table_exists("tenant_bot_bindings", dry_run=False):
        raise RuntimeError("tenant_bot_bindings missing — re-run with --with-base")

    run_sql_file(BOT_BINDING_CONTEXT_MIGRATION, dry_run=args.dry_run)
    if args.with_base:
        run_sql_file(WHIEDA_BINDING_MIGRATION, dry_run=args.dry_run)

    for path in SQL_FILES:
        run_sql_file(path, dry_run=args.dry_run)

    print("OK: staging cabinet SQL applied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
