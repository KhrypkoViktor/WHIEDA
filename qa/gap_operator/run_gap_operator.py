#!/usr/bin/env python3
"""Gap operator control plane verification (local Docker when available)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "backend" / "platform-api"
SCRIPTS = PLATFORM / "scripts"
SEED = Path(__file__).resolve().parent / "seed_advisor_gap_events.sql"

if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from asyncio_util import run_async

DEFAULT_DB = (
    "postgresql://whieda_platform_api_local:local_core_api_only@127.0.0.1:55432/whieda_platform_local_core"
)


def _docker_psql(sql: str) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD=local_staging_proof",
            "whieda-local-staging-postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "whieda_platform_local_core",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _seed_events() -> None:
    remote = "/tmp/gap_operator_seed.sql"
    subprocess.run(
        ["docker", "cp", str(SEED), f"whieda-local-staging-postgres:{remote}"],
        check=True,
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD=local_staging_proof",
            "whieda-local-staging-postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "whieda_platform_local_core",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            remote,
        ],
        check=True,
    )


async def _run_refresh(tenant: str, dry_run: bool) -> dict:
    from app.admin.gap_review.service import run_refresh
    from app.db import close_pool, init_pool

    await init_pool()
    try:
        return await run_refresh(tenant, dry_run=dry_run)
    finally:
        await close_pool()


async def _verify_redaction(tenant: str) -> None:
    from app.admin.gap_review.service import build_export
    from app.db import close_pool, init_pool

    await init_pool()
    try:
        md = await build_export(tenant, fmt="md")
        csv = await build_export(tenant, fmt="csv")
    finally:
        await close_pool()
    combined = str(md.get("content") or "") + str(csv.get("content") or "")
    for forbidden in ("session_ref", "telegram:", "+375", "abc123deadbeef", "feedface"):
        if forbidden in combined:
            raise RuntimeError(f"export leaked forbidden fragment: {forbidden!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gap operator local verification")
    parser.add_argument("--tenant", default="whieda")
    parser.add_argument("--offline", action="store_true", help="Skip Docker; pytest only")
    args = parser.parse_args()
    os.environ.setdefault("PLATFORM_DATABASE_URL", DEFAULT_DB)

    pytest = subprocess.run(
        [sys.executable, "-m", "pytest", str(PLATFORM / "tests" / "test_advisor_gap_operator.py"), "-q"],
        cwd=str(PLATFORM),
    )
    if pytest.returncode != 0:
        print("pytest: FAIL")
        return pytest.returncode
    print("pytest: PASS")

    if args.offline:
        print("Docker verification: NOT_RUN")
        print("GAP operator offline summary: PASS")
        return 0

    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
    except Exception:
        print("Docker verification: NOT_RUN (docker unavailable)")
        return 0

    ensure = subprocess.run(
        [sys.executable, str(ROOT / "postgres" / "scripts" / "ensure_local_core_database.py"), "--force-reapply"],
        cwd=str(ROOT),
    )
    if ensure.returncode != 0:
        print("ensure_local_core_database: FAIL")
        return ensure.returncode

    _seed_events()
    _docker_psql("delete from advisor_gap_review_items where tenant_id = 'whieda';")
    first = run_async(_run_refresh(args.tenant, dry_run=False))
    second = run_async(_run_refresh(args.tenant, dry_run=False))
    dry = run_async(_run_refresh(args.tenant, dry_run=True))
    run_async(_verify_redaction(args.tenant))

    if first.get("inserted", 0) < 1:
        print(f"refresh first run: FAIL (inserted={first})")
        return 1
    if second.get("inserted", 0) != 0:
        print(f"refresh second run duplicated inserts: FAIL ({second})")
        return 1
    if dry.get("inserted", 0) != 0:
        print(f"dry-run wrote inserts: FAIL ({dry})")
        return 1

    _docker_psql(
        "set app.tenant_id = 'whieda'; select count(*) from advisor_gap_review_items;"
    )
    cross = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD=local_api_proof_only",
            "whieda-local-staging-postgres",
            "psql",
            "-U",
            "whieda_platform_api_local",
            "-d",
            "whieda_platform_local_core",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            "select set_config('app.tenant_id','whieda',true); select count(*) from advisor_gap_review_items;",
        ],
        capture_output=True,
        text=True,
    )
    if cross.returncode != 0:
        print("RLS read as API role: FAIL")
        return 1
    lines = [line.strip() for line in cross.stdout.splitlines() if line.strip()]
    count_line = lines[-1] if lines else ""
    if not count_line.isdigit() or int(count_line) < 1:
        print(f"RLS read as API role: FAIL (count={count_line!r})")
        return 1

    export = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "export_advisor_gap_review.py"),
            "--tenant",
            args.tenant,
            "--format",
            "md",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if export.returncode != 0:
        print("export script: FAIL")
        return 1
    if "session_ref" in export.stdout:
        print("export redaction: FAIL")
        return 1

    print(
        f"refresh1 inserted={first['inserted']} refresh2 inserted={second['inserted']} "
        f"unchanged={second['unchanged']}"
    )
    print("GAP operator verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
