"""Create persistent local Core database on staging Postgres (Docker 55432)."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from staging_proof_lib import (  # noqa: E402
    APPLY_ORDER,
    DOCKER_CONTAINER,
    LOCAL_STAGING_SUPERPASSWORD,
    LOCAL_STAGING_SUPERUSER,
    SEED,
    SQL_DIR,
)

LOCAL_CORE_DB = "whieda_platform_local_core"
INIT_MARKER_TABLE = "platform_tenants"


def _run(cmd: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True, input=input_text)


def _psql_cmd(db: str, user: str, password: str, *extra: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        f"PGPASSWORD={password}",
        DOCKER_CONTAINER,
        "psql",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        *extra,
    ]


def _psql_exec(db: str, sql: str) -> None:
    _run(_psql_cmd(db, LOCAL_STAGING_SUPERUSER, LOCAL_STAGING_SUPERPASSWORD, "-c", sql))


def _psql_file(db: str, path: Path) -> None:
    _run(
        _psql_cmd(db, LOCAL_STAGING_SUPERUSER, LOCAL_STAGING_SUPERPASSWORD, "-f", "-"),
        input_text=path.read_text(encoding="utf-8"),
    )


def _database_exists() -> bool:
    proc = subprocess.run(
        _psql_cmd(
            "postgres",
            LOCAL_STAGING_SUPERUSER,
            LOCAL_STAGING_SUPERPASSWORD,
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{LOCAL_CORE_DB}';",
        ),
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() == "1"


def _schema_initialized() -> bool:
    if not _database_exists():
        return False
    proc = subprocess.run(
        _psql_cmd(
            LOCAL_CORE_DB,
            LOCAL_STAGING_SUPERUSER,
            LOCAL_STAGING_SUPERPASSWORD,
            "-tAc",
            "SELECT 1 FROM information_schema.tables "
            f"WHERE table_schema = 'public' AND table_name = '{INIT_MARKER_TABLE}';",
        ),
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() == "1"


def _apply_schema() -> None:
    for name in APPLY_ORDER:
        _psql_file(LOCAL_CORE_DB, SQL_DIR / name)
    if SEED.is_file():
        _psql_file(LOCAL_CORE_DB, SEED)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure local Core database on staging Postgres")
    parser.add_argument(
        "--force-reapply",
        action="store_true",
        help="Re-run all SQL even if schema already present",
    )
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("Docker required", file=sys.stderr)
        return 1

    if _schema_initialized() and not args.force_reapply:
        print(f"OK: {LOCAL_CORE_DB} already initialized (use --force-reapply to rebuild)")
        return 0

    if not _database_exists():
        _psql_exec("postgres", f'CREATE DATABASE "{LOCAL_CORE_DB}";')

    _apply_schema()

    print(f"OK: {LOCAL_CORE_DB} ready ({len(APPLY_ORDER)} SQL files + seed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
