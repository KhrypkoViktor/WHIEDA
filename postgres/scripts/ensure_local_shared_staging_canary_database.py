#!/usr/bin/env python3
"""Create an empty local shared-staging canary database and apply platform SQL twice."""

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
    SQL_DIR,
)

CANARY_DB = "whieda_platform_shared_staging_local"
OCCUPANT = _SCRIPT_DIR / "local_shared_staging_occupant_v1.sql"


def _run(cmd: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
    )


def _psql_cmd(db: str, *extra: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        f"PGPASSWORD={LOCAL_STAGING_SUPERPASSWORD}",
        DOCKER_CONTAINER,
        "psql",
        "-U",
        LOCAL_STAGING_SUPERUSER,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        *extra,
    ]


def _psql_exec(db: str, sql: str) -> None:
    _run(_psql_cmd(db, "-c", sql))


def _psql_file(db: str, path: Path) -> None:
    _run(_psql_cmd(db, "-f", "-"), input_text=path.read_text(encoding="utf-8"))


def recreate_database() -> None:
    _psql_exec(
        "postgres",
        f"""
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '{CANARY_DB}' AND pid <> pg_backend_pid();
""",
    )
    _psql_exec("postgres", f'DROP DATABASE IF EXISTS "{CANARY_DB}";')
    _psql_exec("postgres", f'CREATE DATABASE "{CANARY_DB}";')


def apply_schema_twice() -> None:
    for pass_no in (1, 2):
        for name in APPLY_ORDER:
            _psql_file(CANARY_DB, SQL_DIR / name)
        print(f"OK: APPLY_ORDER pass {pass_no}/2 ({len(APPLY_ORDER)} files)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure empty local shared-staging canary database")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    if args.apply or args.publish or args.dsn:
        print(
            '{"ok":false,"code":"action_refused","message":"--apply, --publish and live DSN are refused"}'
        )
        return 1
    if shutil.which("docker") is None:
        print("Docker required", file=sys.stderr)
        return 1

    recreate_database()
    apply_schema_twice()
    _psql_file(CANARY_DB, OCCUPANT)
    print(
        f"OK: {CANARY_DB} ready ({len(APPLY_ORDER)} SQL files x2 + occupant marker; "
        "not a shared-staging write target)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
