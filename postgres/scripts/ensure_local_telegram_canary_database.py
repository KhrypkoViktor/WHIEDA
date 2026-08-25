#!/usr/bin/env python3
"""Create an empty local Telegram canary database and apply platform SQL twice."""

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

CANARY_DB = "whieda_platform_telegram_canary"
LOCAL_CORE_API_ROLE = "whieda_platform_api_local"
LOCAL_CORE_API_PASSWORD = "local_core_api_only"
LOCAL_ADVISOR_SCHEMA = SQL_DIR / "platform_advisor_structured_local_v1.sql"
OVERLAY = _SCRIPT_DIR / "local_telegram_canary_overlay_v1.sql"


def _run(cmd: list[str], *, input_text: str | None = None) -> None:
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
    )


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


def _recreate_database() -> None:
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


def _apply_schema_twice() -> None:
    for pass_no in (1, 2):
        for name in APPLY_ORDER:
            _psql_file(CANARY_DB, SQL_DIR / name)
        print(f"OK: APPLY_ORDER pass {pass_no}/{2} ({len(APPLY_ORDER)} files)")


def _ensure_api_role() -> None:
    _psql_exec(
        "postgres",
        f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{LOCAL_CORE_API_ROLE}') THEN
    CREATE ROLE {LOCAL_CORE_API_ROLE} WITH LOGIN PASSWORD '{LOCAL_CORE_API_PASSWORD}'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END $$;
""",
    )
    _psql_exec(
        CANARY_DB,
        f"""
GRANT CONNECT ON DATABASE "{CANARY_DB}" TO {LOCAL_CORE_API_ROLE};
GRANT USAGE ON SCHEMA public TO {LOCAL_CORE_API_ROLE};
GRANT EXECUTE ON FUNCTION platform_set_tenant_context(text) TO {LOCAL_CORE_API_ROLE};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {LOCAL_CORE_API_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {LOCAL_CORE_API_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {LOCAL_CORE_API_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {LOCAL_CORE_API_ROLE};
""",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure empty local Telegram canary database")
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

    _recreate_database()
    _apply_schema_twice()
    _psql_file(CANARY_DB, LOCAL_ADVISOR_SCHEMA)
    _psql_file(CANARY_DB, OVERLAY)
    _ensure_api_role()
    print(
        f"OK: {CANARY_DB} ready ({len(APPLY_ORDER)} SQL files x2 + generic overlay; "
        f"API role {LOCAL_CORE_API_ROLE} is NOBYPASSRLS)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
