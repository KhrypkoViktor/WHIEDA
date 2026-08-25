#!/usr/bin/env python3
"""Local Docker Postgres staging proof: apply SQL twice + RLS under API role."""

from __future__ import annotations

import argparse
import json
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
    INBOX_RLS_PROOF_TABLES,
    ADVISOR_PROFILE_RLS_TABLES,
    RELEASE_PACKAGE_RLS_TABLES,
    ROOT,
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
        encoding="utf-8",
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
        print(
            "  skip staging_seed_whieda_journey_v1.sql "
            "(stale vs platform_onboarding_v1.sql; Gate B1 does not invent replacement seed)"
        )


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
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {API_PROOF_ROLE};
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

INSERT INTO telegram_update_inbox (
  binding_id, tenant_id, telegram_update_id, payload, state
) VALUES
  (
    'whieda-advisor-bot',
    'whieda',
    91001,
    '{"update_id": 91001, "message": {"text": "ping", "chat": {"id": 1, "type": "private"}}}'::jsonb,
    'pending'
  ),
  (
    'test-acme-bot-binding',
    'test-acme',
    91001,
    '{"update_id": 91001, "message": {"text": "ping", "chat": {"id": 2, "type": "private"}}}'::jsonb,
    'pending'
  )
ON CONFLICT (binding_id, telegram_update_id) DO NOTHING;

INSERT INTO tenant_release_run (
  run_id, package_id, package_version, tenant_id, package_sha256, release_status
) VALUES
  (
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'proof-whieda-package',
    '1.0.0',
    'whieda',
    '11' || repeat('a', 62),
    'candidate'
  ),
  (
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2',
    'proof-acme-package',
    '1.0.0',
    'test-acme',
    '22' || repeat('b', 62),
    'candidate'
  )
ON CONFLICT (package_id, package_sha256) DO NOTHING;

INSERT INTO tenant_release_staging_product (
  run_id, tenant_id, sku, canonical_name, review_status, media_state
) VALUES
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', 'whieda', 'WH-PROOF', 'WHIEDA Proof', 'approved', 'present'),
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', 'test-acme', 'AC-PROOF', 'Acme Proof', 'approved', 'present')
ON CONFLICT (run_id, sku) DO NOTHING;

INSERT INTO tenant_release_candidate (
  candidate_id, run_id, package_id, package_version, tenant_id, package_sha256, status
) VALUES
  (
    'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'proof-whieda-package',
    '1.0.0',
    'whieda',
    '11' || repeat('a', 62),
    'current'
  ),
  (
    'dddddddd-dddd-4ddd-8ddd-ddddddddddd2',
    'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2',
    'proof-acme-package',
    '1.0.0',
    'test-acme',
    '22' || repeat('b', 62),
    'current'
  )
ON CONFLICT (candidate_id) DO NOTHING;

INSERT INTO tenant_release_candidate_product (
  candidate_id, tenant_id, sku, canonical_name, media_state, card_present
) VALUES
  ('cccccccc-cccc-4ccc-8ccc-ccccccccccc1', 'whieda', 'WH-PROOF', 'WHIEDA Proof', 'present', true),
  ('dddddddd-dddd-4ddd-8ddd-ddddddddddd2', 'test-acme', 'AC-PROOF', 'Acme Proof', 'present', true)
ON CONFLICT (candidate_id, sku) DO NOTHING;

INSERT INTO tenant_release_candidate_price (
  candidate_id, tenant_id, sku, kind, amount, currency, source, amount_sha256
) VALUES
  (
    'cccccccc-cccc-4ccc-8ccc-ccccccccccc1',
    'whieda',
    'WH-PROOF',
    'retail',
    41,
    'BYN',
    'proof-whieda',
    repeat('c', 64)
  ),
  (
    'dddddddd-dddd-4ddd-8ddd-ddddddddddd2',
    'test-acme',
    'AC-PROOF',
    'retail',
    37.13,
    'USD',
    'proof-acme',
    repeat('d', 64)
  )
ON CONFLICT (candidate_id, sku, kind, currency) DO NOTHING;
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

    for table in INBOX_RLS_PROOF_TABLES:
        own = api_count(db, "whieda", table, "tenant_id = 'whieda'")
        foreign = api_count(db, "whieda", table, "tenant_id = 'test-acme'")
        if own < 1 and table == "telegram_update_inbox":
            raise AssertionError(f"{table}: whieda context must see whieda rows (got {own})")
        if foreign != 0:
            raise AssertionError(f"{table}: whieda context must not see test-acme rows (got {foreign})")
        checks.append(f"{table}: whieda visible={own}, cross-tenant={foreign}")

    for table in ADVISOR_PROFILE_RLS_TABLES:
        own = api_count(db, "whieda", table, "tenant_id = 'whieda'")
        foreign = api_count(db, "whieda", table, "tenant_id = 'test-acme'")
        if own < 1:
            raise AssertionError(f"{table}: whieda context must see own profile (got {own})")
        if foreign != 0:
            raise AssertionError(f"{table}: whieda context must not see test-acme profile (got {foreign})")
        checks.append(f"{table}: whieda visible={own}, cross-tenant={foreign}")

    for table in RELEASE_PACKAGE_RLS_TABLES:
        own = api_count(db, "whieda", table, "tenant_id = 'whieda'")
        foreign = api_count(db, "whieda", table, "tenant_id = 'test-acme'")
        if own < 1:
            raise AssertionError(f"{table}: whieda context must see own release rows (got {own})")
        if foreign != 0:
            raise AssertionError(f"{table}: whieda context must not see test-acme release rows (got {foreign})")
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

    api_expect_fail(
        db,
        """
SELECT platform_set_tenant_context('whieda');
INSERT INTO telegram_update_inbox (
  binding_id, tenant_id, telegram_update_id, payload
) VALUES (
  'test-acme-bot-binding', 'test-acme', 91099, '{"update_id": 91099}'::jsonb
);
""",
    )
    checks.append("telegram_update_inbox: cross-tenant INSERT rejected")

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


def run_inbox_durable_checks(db: str) -> None:
    print("=== Durable Telegram inbox uniqueness and lease ===")
    checks: list[str] = []
    super_kw = {
        "user": LOCAL_STAGING_SUPERUSER,
        "password": LOCAL_STAGING_SUPERPASSWORD,
    }

    tables = psql_scalar(
        db,
        """
SELECT (to_regclass('public.telegram_update_inbox') IS NOT NULL)
   AND (to_regclass('public.telegram_delivery_outbox') IS NOT NULL)
   AND (to_regclass('public.tenant_advisor_profile') IS NOT NULL)
   AND (to_regclass('public.tenant_release_run') IS NOT NULL)
   AND (to_regclass('public.tenant_release_candidate') IS NOT NULL)
   AND (to_regclass('public.tenant_release_candidate_price') IS NOT NULL);
""",
        **super_kw,
    )
    if tables != "t":
        raise AssertionError(f"inbox/outbox/data-plane/release tables missing: {tables}")
    checks.append("inbox, outbox, tenant_advisor_profile, and tenant_release tables exist")

    first = psql_scalar(
        db,
        """
SELECT inserted
FROM telegram_inbox_enqueue(
  'whieda-advisor-bot',
  'whieda',
  92001,
  '{"update_id": 92001}'::jsonb
);
""",
        **super_kw,
    )
    second = psql_scalar(
        db,
        """
SELECT inserted
FROM telegram_inbox_enqueue(
  'whieda-advisor-bot',
  'whieda',
  92001,
  '{"update_id": 92001}'::jsonb
);
""",
        **super_kw,
    )
    same_binding = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_update_inbox
WHERE binding_id = 'whieda-advisor-bot' AND telegram_update_id = 92001;
""",
        **super_kw,
    )
    if first != "t" or second != "f" or same_binding != "1":
        raise AssertionError(
            f"same binding+update must insert once (first={first} second={second} count={same_binding})"
        )
    checks.append("same binding+update_id is unique")

    psql_exec(
        db,
        """
SELECT telegram_inbox_enqueue(
  'test-acme-bot-binding',
  'test-acme',
  92001,
  '{"update_id": 92001}'::jsonb
);
""",
        **super_kw,
    )
    isolated = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_update_inbox
WHERE telegram_update_id = 92001
  AND binding_id IN ('whieda-advisor-bot', 'test-acme-bot-binding');
""",
        **super_kw,
    )
    if isolated != "2":
        raise AssertionError(f"same update_id in two bindings must be two rows (got {isolated})")
    checks.append("WHIEDA vs other binding isolate the same update_id")

    claimed = psql_scalar(
        db,
        """
SELECT lease_owner
FROM telegram_inbox_claim_by_id(
  (SELECT inbox_id FROM telegram_update_inbox
   WHERE binding_id = 'whieda-advisor-bot' AND telegram_update_id = 92001),
  'owner-a',
  30
);
""",
        **super_kw,
    )
    if claimed != "owner-a":
        raise AssertionError(f"first claim must own the row (got {claimed!r})")
    second_claim = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_inbox_claim_by_id(
  (SELECT inbox_id FROM telegram_update_inbox
   WHERE binding_id = 'whieda-advisor-bot' AND telegram_update_id = 92001),
  'owner-b',
  30
);
""",
        **super_kw,
    )
    if second_claim != "0":
        raise AssertionError("active lease must not be stolen")
    checks.append("parallel claim keeps a single owner")

    psql_exec(
        db,
        """
UPDATE telegram_update_inbox
SET lease_until = now() - interval '1 second'
WHERE binding_id = 'whieda-advisor-bot' AND telegram_update_id = 92001;
""",
        **super_kw,
    )
    retry_owner = psql_scalar(
        db,
        """
SELECT lease_owner
FROM telegram_inbox_claim_by_id(
  (SELECT inbox_id FROM telegram_update_inbox
   WHERE binding_id = 'whieda-advisor-bot' AND telegram_update_id = 92001),
  'owner-b',
  30
);
""",
        **super_kw,
    )
    if retry_owner != "owner-b":
        raise AssertionError(f"expired lease must be reclaimable (got {retry_owner!r})")
    checks.append("expired lease is retryable from DB state")

    for line in checks:
        print(f"  PASS {line}")


def run_outbox_delivery_checks(db: str) -> None:
    print("=== Durable Telegram outbox plan, order, unknown ===")
    checks: list[str] = []
    super_kw = {
        "user": LOCAL_STAGING_SUPERUSER,
        "password": LOCAL_STAGING_SUPERPASSWORD,
    }

    columns = psql_scalar(
        db,
        """
SELECT (to_regclass('public.telegram_delivery_outbox') IS NOT NULL)
   AND EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_schema='public' AND table_name='telegram_delivery_outbox'
       AND column_name='sequence_no'
   )
   AND EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_schema='public' AND table_name='telegram_delivery_outbox'
       AND column_name='payload_json'
   )
   AND EXISTS (
     SELECT 1 FROM information_schema.columns
     WHERE table_schema='public' AND table_name='telegram_delivery_outbox'
       AND column_name='status'
   );
""",
        **super_kw,
    )
    if columns != "t":
        raise AssertionError(f"durable outbox columns missing: {columns}")
    checks.append("telegram_delivery_outbox has Gate K columns")

    psql_exec(
        db,
        """
SELECT telegram_inbox_enqueue(
  'whieda-advisor-bot',
  'whieda',
  92101,
  '{"update_id": 92101}'::jsonb
);
SELECT telegram_outbox_enqueue_items(
  (SELECT inbox_id FROM telegram_update_inbox
    WHERE binding_id='whieda-advisor-bot' AND telegram_update_id=92101),
  'whieda-advisor-bot',
  'whieda',
  92101,
  '[
     {"kind":"photo","payload":{"chat_id":"1","photo_url":"https://media.example.org/p.webp"}},
     {"kind":"text","payload":{"chat_id":"1","text":"hi"}}
   ]'::jsonb
);
SELECT telegram_outbox_enqueue_items(
  (SELECT inbox_id FROM telegram_update_inbox
    WHERE binding_id='whieda-advisor-bot' AND telegram_update_id=92101),
  'whieda-advisor-bot',
  'whieda',
  92101,
  '[
     {"kind":"photo","payload":{"chat_id":"1","photo_url":"https://media.example.org/p.webp"}},
     {"kind":"text","payload":{"chat_id":"1","text":"hi"}}
   ]'::jsonb
);
""",
        **super_kw,
    )
    plan_count = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_delivery_outbox
WHERE binding_id='whieda-advisor-bot' AND telegram_update_id=92101;
""",
        **super_kw,
    )
    if plan_count != "2":
        raise AssertionError(f"duplicate plan must stay at 2 rows (got {plan_count})")
    checks.append("duplicate enqueue does not duplicate photo/text plan")

    first_kind = psql_scalar(
        db,
        """
SELECT kind
FROM telegram_outbox_claim_next('owner-outbox', 30, 'whieda-advisor-bot');
""",
        **super_kw,
    )
    if first_kind != "photo":
        raise AssertionError(f"first claim must be photo (got {first_kind!r})")
    second_kind = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_outbox_claim_next('owner-outbox-2', 30, 'whieda-advisor-bot');
""",
        **super_kw,
    )
    if second_kind != "0":
        raise AssertionError("text must wait while photo is leased")
    checks.append("photo-first ordering blocks later sequence")

    psql_exec(
        db,
        """
SELECT telegram_outbox_mark_unknown(
  (SELECT outbox_id FROM telegram_delivery_outbox
    WHERE binding_id='whieda-advisor-bot' AND telegram_update_id=92101 AND sequence_no=1),
  'telegram_send_ambiguous'
);
""",
        **super_kw,
    )
    blocked = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM telegram_outbox_claim_next('owner-outbox-3', 30, 'whieda-advisor-bot');
""",
        **super_kw,
    )
    if blocked != "0":
        raise AssertionError("unknown_delivery must not auto-send the next sequence")
    checks.append("unknown_delivery does not resend later sequence")

    for line in checks:
        print(f"  PASS {line}")


def run_binding_isolation_checks(db: str) -> None:
    print("=== Telegram binding isolation after apply ===")
    checks: list[str] = []
    super_kw = {
        "user": LOCAL_STAGING_SUPERUSER,
        "password": LOCAL_STAGING_SUPERPASSWORD,
    }

    table = psql_scalar(
        db,
        "SELECT to_regclass('public.tenant_bot_bindings') IS NOT NULL;",
        **super_kw,
    )
    if table != "t":
        raise AssertionError("tenant_bot_bindings was not created")
    checks.append("tenant_bot_bindings exists")

    columns = psql_scalar(
        db,
        """
SELECT string_agg(column_name, ',' ORDER BY column_name)
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'tenant_bot_bindings';
""",
        **super_kw,
    )
    for required in (
        "binding_id",
        "tenant_id",
        "webhook_secret_ref",
        "bot_token_ref",
        "bot_username",
        "processing_mode",
        "status",
    ):
        if required not in columns.split(","):
            raise AssertionError(f"tenant_bot_bindings missing column {required}: {columns}")
    checks.append("binding context columns present")

    nsp_tenants = psql_scalar(
        db,
        "SELECT count(*)::text FROM tenants WHERE tenant_id = 'nsp-maxim';",
        **super_kw,
    )
    if nsp_tenants != "0":
        raise AssertionError(f"Core apply must not create nsp-maxim tenant (got {nsp_tenants})")
    nsp_bindings = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM tenant_bot_bindings
WHERE tenant_id = 'nsp-maxim'
   OR binding_id LIKE 'nsp%';
""",
        **super_kw,
    )
    if nsp_bindings != "0":
        raise AssertionError(f"Core apply must not create NSP bindings (got {nsp_bindings})")
    checks.append("nsp-maxim absent after Core apply")

    unknown = psql_scalar(
        db,
        "SELECT count(*)::text FROM tenant_bot_bindings WHERE binding_id = 'unknown-binding-does-not-exist';",
        **super_kw,
    )
    if unknown != "0":
        raise AssertionError("unknown binding_id resolved to a row")
    checks.append("unknown binding_id has no row")

    whieda_bot = psql_scalar(
        db,
        """
SELECT tenant_id || '|' || status || '|' || coalesce(bot_username, '')
FROM tenant_bot_bindings
WHERE binding_id = 'whieda-advisor-bot';
""",
        **super_kw,
    )
    if not whieda_bot.startswith("whieda|active|"):
        raise AssertionError(f"WHIEDA advisor binding drifted: {whieda_bot!r}")
    checks.append("whieda-advisor-bot stays on whieda")

    # Local fixture only: prove a disabled NSP binding cannot attach to WHIEDA.
    psql_exec(
        db,
        """
INSERT INTO tenants (tenant_id, display_name, status, default_locale)
VALUES ('nsp-maxim', 'NSP Maxim', 'draft', 'ru')
ON CONFLICT (tenant_id) DO UPDATE
SET status = 'draft', updated_at = now();

INSERT INTO tenant_bot_bindings (
  binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, processing_mode, status
) VALUES (
  'nsp-maxim-disabled-proof',
  'nsp-maxim',
  'env:NSP_TELEGRAM_WEBHOOK_SECRET',
  'env:NSP_TELEGRAM_BOT_TOKEN',
  'NSP_Leader_bot',
  'core',
  'disabled'
)
ON CONFLICT (binding_id) DO UPDATE
SET tenant_id = EXCLUDED.tenant_id,
    status = 'disabled',
    processing_mode = 'core';
""",
        **super_kw,
    )
    nsp_row = psql_scalar(
        db,
        """
SELECT tenant_id || '|' || status
FROM tenant_bot_bindings
WHERE binding_id = 'nsp-maxim-disabled-proof';
""",
        **super_kw,
    )
    if nsp_row != "nsp-maxim|disabled":
        raise AssertionError(f"disabled NSP binding must stay on nsp-maxim: {nsp_row!r}")
    hijack = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM tenant_bot_bindings
WHERE binding_id = 'nsp-maxim-disabled-proof' AND tenant_id = 'whieda';
""",
        **super_kw,
    )
    if hijack != "0":
        raise AssertionError("disabled NSP binding attached to WHIEDA tenant")
    fallback = psql_scalar(
        db,
        """
SELECT count(*)::text
FROM tenant_bot_bindings
WHERE tenant_id = 'whieda'
  AND binding_id IN ('unknown-binding-does-not-exist', 'nsp-maxim-disabled-proof');
""",
        **super_kw,
    )
    if fallback != "0":
        raise AssertionError("unknown/disabled NSP identity fell into WHIEDA tenant")
    checks.append("disabled nsp-maxim binding does not fall into WHIEDA")

    for line in checks:
        print(f"  PASS {line}")


def run_release_package_offline_validate() -> None:
    print("=== Tenant release package offline validate ===")
    cli = ROOT / "backend" / "platform-api" / "scripts" / "run_tenant_release_package.py"
    package = ROOT / "qa" / "tenant_release_package" / "examples" / "tenant-alpha"
    proc = subprocess.run(
        [sys.executable, str(cli), "--validate", "--package", str(package)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"tenant-alpha validate failed: {proc.stdout}\n{proc.stderr}")
    payload = json.loads(proc.stdout)
    if not payload.get("ok"):
        raise AssertionError(f"tenant-alpha validate not ok: {payload}")
    mixed = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--validate",
            "--package",
            str(ROOT / "qa" / "tenant_release_package" / "fixtures" / "mixed-tenant"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    mixed_payload = json.loads(mixed.stdout)
    if mixed.returncode == 0 or mixed_payload.get("ok"):
        raise AssertionError("mixed-tenant package must fail validate")
    publish = subprocess.run(
        [sys.executable, str(cli), "--publish"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if publish.returncode == 0:
        raise AssertionError("--publish must refuse")
    print("  PASS tenant-alpha validate")
    print("  PASS mixed-tenant aborted")
    print("  PASS --publish refused")


def drop_db(db: str) -> None:
    psql_exec(
        "postgres",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db}' AND pid <> pg_backend_pid();",
        user=LOCAL_STAGING_SUPERUSER,
        password=LOCAL_STAGING_SUPERPASSWORD,
    )
    # DROP DATABASE cannot run inside the transaction created by a multi-statement psql -c call.
    psql_exec(
        "postgres",
        f'DROP DATABASE IF EXISTS "{db}";',
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
        run_release_package_offline_validate()

        run_binding_isolation_checks(db)

        create_api_role(db)
        seed_rls_fixtures(db)
        run_rls_checks(db)
        run_inbox_durable_checks(db)
        run_outbox_delivery_checks(db)

        print("\n=== LOCAL STAGING PROOF: PASS ===")
        print(f"  SQL files x2: {len(APPLY_ORDER)}")
        print("  journey seed: skipped (stale vs onboarding schema)")
        print(f"  RLS tables: {', '.join(RLS_PROOF_TABLES)}")
        print(f"  Inbox RLS tables: {', '.join(INBOX_RLS_PROOF_TABLES)}")
        print(f"  Release package RLS tables: {', '.join(RELEASE_PACKAGE_RLS_TABLES)}")
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
