"""Create persistent local Core database on staging Postgres (Docker 55432)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_POSTGRES_SCRIPTS = _SCRIPT_DIR.parents[1] / "postgres" / "scripts"
if str(_POSTGRES_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_POSTGRES_SCRIPTS))

from staging_proof_lib import (  # noqa: E402
    APPLY_ORDER,
    DOCKER_CONTAINER,
    LOCAL_STAGING_SUPERPASSWORD,
    LOCAL_STAGING_SUPERUSER,
    SEED,
    SQL_DIR,
)

LOCAL_CORE_DB = "whieda_platform_local_core"


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


def main() -> int:
    if shutil.which("docker") is None:
        print("Docker required", file=sys.stderr)
        return 1

    exists = subprocess.run(
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
    if exists.stdout.strip() != "1":
        _psql_exec("postgres", f'CREATE DATABASE "{LOCAL_CORE_DB}";')

    for name in APPLY_ORDER:
        _psql_file(LOCAL_CORE_DB, SQL_DIR / name)
    if SEED.is_file():
        _psql_file(LOCAL_CORE_DB, SEED)

    print(f"OK: {LOCAL_CORE_DB} ready ({len(APPLY_ORDER)} SQL files + seed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
