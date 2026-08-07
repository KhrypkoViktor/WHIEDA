#!/usr/bin/env python3
"""Local Docker Postgres staging proof: apply SQL twice + RLS under API role."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from staging_proof_lib import (  # noqa: E402
    API_PROOF_PASSWORD,
    API_PROOF_ROLE,
    APPLY_ORDER,
    COMPOSE_FILE,
    DOCKER_CONTAINER,
    LOCAL_STAGING_HOST,
    LOCAL_STAGING_PORT,
    LOCAL_STAGING_SUPERPASSWORD,
    LOCAL_STAGING_SUPERUSER,
    RLS_PROOF_TABLES,
    SEED,
    SQL_DIR,
    validate_proof_db_name,
    validate_proof_host,
    validate_proof_port,
)

COMPOSE_DIR = COMPOSE_FILE.parent


def _env(password: str, *, user: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    if user:
        env["PGUSER"] = user
    return env


def run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    label = " ".join(cmd[:6])
    print(f"  $ {label}{'...' if len(cmd) > 6 else ''}")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        cwd=str(cwd) if cwd else None,
        input=input_text,
    )


def require_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError(
            "Docker not found in PATH. Install Docker Desktop, then run this script again."
        )


def docker_compose_up() -> None:
    print("=== docker compose up (local staging Postgres 16) ===")
    proc = run(
        ["docker", "compose", "-f", "docker-compose.local-staging.yml", "up", "-d"],
        cwd=COMPOSE_DIR,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "docker compose failed")


def wait_postgres(timeout_sec: int = 60) -> None:
    print(f"=== waiting for Postgres in {DOCKER_CONTAINER} ===")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        proc = run(
            ["docker", "exec", DOCKER_CONTAINER, "pg_isready", "-U", LOCAL_STAGING_SUPERUSER, "-d", "postgres"],
            check=False,
        )
        if proc.returncode == 0:
            print("  Postgres ready")
            return
        time.sleep(2)
    raise TimeoutError(f"Postgres not ready after {timeout_sec}s")


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


def psql_exec(db: str, sql: str, *, user: str, password: str) -> None:
    run(_psql_cmd(db, user, password, "-c", sql))


def psql_scalar(db: str, sql: str, *, user: str, password: str) -> str:
    proc = run(_psql_cmd(db, user, password, "-tAc", sql))
    return (proc.stdout or "").strip()


def psql_file(db: str, path: Path, *, user: str, password: str) -> None:
    run(_psql_cmd(db, user, password, "-f", "-"), input_text=path.read_text(encoding="utf-8"))


def apply_all_migrations(db: str, *, pass_label: str) -> None:
    print(f"=== SQL apply {pass_label} ===")
    for name in APPLY_ORDER:
        path = SQL_DIR / name
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"  -> {name}")
        psql_file(db, path, user=LOCAL_STAGING_SUPERUSER, password=LOCAL_STAGING_SUPERPASSWORD)
    if SEED.is_file():
        print("  -> staging_seed_whieda_journey_v1.sql")
        psql_file(db, SEED, user=LOCAL_STAGING_SUPERUSER, password=LOCAL_STAGING_SUPERPASSWORD)


def create_api_role(db: str) -> None:
    print(f"=== create API proof role {API_PROOF_ROLE} ===")
    psql_exec(
        "postgres",
        f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{API_PROOF_ROLE}') THEN
    REASSIGN OWNED BY {API_PROOF_ROLE} TO {LOCAL_STAGING_SUPERUSER};
    DROP OWNED BY {API_PROOF_ROLE};
    DROP ROLE {API_PROOF_ROLE};
  END IF;
END $$;
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    psql_exec(
        "postgres",
        f"""
CREATE ROLE {API_PROOF_ROLE} WITH LOGIN PASSWORD '{API_PROOF_PASSWORD}'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    psql_exec(
        db,
        f"""
GRANT CONNECT ON DATABASE "{db}" TO {API_PROOF_ROLE};
GRANT USAGE ON SCHEMA public TO {API_PROOF_ROLE};
GRANT EXECUTE ON FUNCTION platform_set_tenant_context(text) TO {API_PROOF_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {API_PROOF_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {API_PROOF_ROLE};
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )


def seed_rls_fixtures(db: str) -> None:
    print("=== seed RLS fixtures (superuser) ===")
    psql_exec(
        db,
        """
INSERT INTO lead_actors (actor_id, tenant_id, display_name)
VALUES ('acme-owner', 'test-acme', 'Acme Owner')
ON CONFLICT (actor_id) DO UPDATE SET tenant_id = EXCLUDED.tenant_id;

INSERT INTO referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled)
VALUES ('acme-ref', 'test-acme', 'acme-owner', 'named', true)
ON CONFLICT (ref_code) DO UPDATE SET tenant_id = EXCLUDED.tenant_id;

INSERT INTO website_leads (
  tenant_id, name, contact, product_name, assigned_owner_id, idempotency_key
) VALUES
  ('whieda', 'Whieda Lead', '+375000000001', 'Spirulina', 'ladnaya', 'proof-whieda-lead-1'),
  ('test-acme', 'Acme Lead', '+10000000001', 'Demo', 'acme-owner', 'proof-acme-lead-1')
ON CONFLICT (tenant_id, idempotency_key) DO NOTHING;

INSERT INTO website_events (tenant_id, event_type, ref_code)
VALUES
  ('whieda', 'visit', 'ladnaya'),
  ('test-acme', 'visit', 'acme-ref');

INSERT INTO referral_agreements (tenant_id, ref_code, owner_id, status)
VALUES
  ('whieda', 'ladnaya', 'ladnaya', 'draft'),
  ('test-acme', 'acme-ref', 'acme-owner', 'draft');
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )

    lead_whieda = psql_scalar(
        db,
        "SELECT lead_id::text FROM website_leads WHERE tenant_id='whieda' AND idempotency_key='proof-whieda-lead-1' LIMIT 1;",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    lead_acme = psql_scalar(
        db,
        "SELECT lead_id::text FROM website_leads WHERE tenant_id='test-acme' AND idempotency_key='proof-acme-lead-1' LIMIT 1;",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    psql_exec(
        db,
        f"""
INSERT INTO website_lead_watchers (lead_id, tenant_id, watcher_actor_id)
VALUES
  ('{lead_whieda}', 'whieda', 'ladnaya'),
  ('{lead_acme}', 'test-acme', 'acme-owner')
ON CONFLICT (lead_id, watcher_actor_id) DO NOTHING;
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )


def api_count(db: str, tenant: str, table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    sql = f"SELECT platform_set_tenant_context('{tenant}'); SELECT count(*) FROM {table}{clause};"
    out = psql_scalar(db, sql, user=API_PROOF_ROLE, password=API_PROOF_PASSWORD)
    return int(out.splitlines()[-1].strip())


def api_expect_fail(db: str, sql: str) -> None:
    proc = run(_psql_cmd(db, API_PROOF_ROLE, API_PROOF_PASSWORD, "-c", sql), check=False)
    if proc.returncode == 0:
        raise AssertionError(f"Expected failure but succeeded:\n{sql}\n{proc.stdout}")
    combined = (proc.stderr or "") + (proc.stdout or "")
    if "violates row-level security" not in combined.lower() and "permission denied" not in combined.lower():
        raise AssertionError(f"Unexpected error (want RLS/permission):\n{combined}")


def run_rls_checks(db: str) -> None:
    print(f"=== RLS checks as {API_PROOF_ROLE} (NOBYPASSRLS) ===")
    checks: list[str] = []

    for table in RLS_PROOF_TABLES:
        own = api_count(db, "whieda", table, "tenant_id = 'whieda'")
        foreign = api_count(db, "whieda", table, "tenant_id = 'test-acme'")
        if own < 1:
            raise AssertionError(f"{table}: whieda context must see whieda rows (got {own})")
        if foreign != 0:
            raise AssertionError(f"{table}: whieda context must not see test-acme rows (got {foreign})")
        checks.append(f"{table}: whieda visible={own}, cross-tenant={foreign}")

    api_expect_fail(
        db,
        """
SELECT platform_set_tenant_context('whieda');
INSERT INTO website_leads (
  tenant_id, name, contact, product_name, assigned_owner_id, idempotency_key
) VALUES (
  'test-acme', 'Bad', '+1', 'X', 'acme-owner', 'proof-cross-tenant-insert'
);
""",
    )
    checks.append("website_leads: cross-tenant INSERT rejected")

    role_flags = psql_scalar(
        db,
        f"SELECT rolsuper::text || '|' || rolbypassrls::text FROM pg_roles WHERE rolname='{API_PROOF_ROLE}';",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    if role_flags != "false|false":
        raise AssertionError(f"{API_PROOF_ROLE} must not be superuser/bypassrls (got {role_flags})")
    checks.append(f"{API_PROOF_ROLE}: superuser=false bypassrls=false")

    for line in checks:
        print(f"  PASS {line}")


def drop_db(db: str) -> None:
    psql_exec(
        "postgres",
        f"""
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "{db}";
""",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA local staging proof (Docker Postgres 55432)")
    parser.add_argument("--skip-docker-up", action="store_true", help="Assume container already running")
    parser.add_argument("--keep-db", action="store_true", help="Keep verify DB after success (debug)")
    args = parser.parse_args()

    db = f"whieda_platform_staging_verify_{uuid.uuid4().hex[:10]}"
    validate_proof_db_name(db)
    validate_proof_host(LOCAL_STAGING_HOST)
    validate_proof_port(LOCAL_STAGING_PORT)

    print("=== WHIEDA local staging proof ===")
    print(f"  host={LOCAL_STAGING_HOST}:{LOCAL_STAGING_PORT}")
    print(f"  database={db}")
    print(f"  api_role={API_PROOF_ROLE}")

    exit_code = 1
    db_created = False
    try:
        require_docker()
        if not args.skip_docker_up:
            docker_compose_up()
        wait_postgres()

        psql_exec(
            "postgres",
            f'CREATE DATABASE "{db}";',
            user=LOCAL_STAGING_SUPERUSER,
            password=LOCAL_STAGING_SUPERPASSWORD,
        )
        db_created = True

        apply_all_migrations(db, pass_label="pass 1/2")
        apply_all_migrations(db, pass_label="pass 2/2 (idempotent)")

        create_api_role(db)
        seed_rls_fixtures(db)
        run_rls_checks(db)

        print("\n=== LOCAL STAGING PROOF: PASS ===")
        print(f"  SQL files x2: {len(APPLY_ORDER)} (+ seed)")
        print(f"  RLS tables: {', '.join(RLS_PROOF_TABLES)}")
        print(f"  Role: {API_PROOF_ROLE} (NOBYPASSRLS)")
        exit_code = 0
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
    except (TimeoutError, RuntimeError, AssertionError, FileNotFoundError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
    finally:
        if db_created and not args.keep_db:
            try:
                drop_db(db)
                print(f"  dropped temporary database {db}")
            except Exception as exc:  # noqa: BLE001
                print(f"  warning: could not drop {db}: {exc}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
