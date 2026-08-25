"""Tests for local staging proof scripts and guards."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "postgres" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from staging_proof_lib import (
    ALLOWED_VERIFY_DB,
    APPLY_ORDER,
    APPLY_PS1,
    BACKFILL_PLAN,
    FORBIDDEN_HOST_FRAGMENTS,
    LOCAL_STAGING_HOST,
    LOCAL_STAGING_PORT,
    RLS_PROOF_TABLES,
    apply_script_files,
    legacy_rls_covers_lead_tables,
    tenant_scoped_lead_tables,
    validate_proof_db_name,
    validate_proof_host,
    validate_proof_port,
)

MANIFEST = ROOT / "WHIEDA_LOCAL_BUILD_BLOCKS_V1.json"
PROOF_SCRIPT = SCRIPTS / "run_local_staging_proof.py"
VERIFY_SCRIPT = SCRIPTS / "verify_staging_apply_empty.py"
COMPOSE = ROOT / "postgres" / "docker-compose.local-staging.yml"


def _load_proof_module():
    spec = importlib.util.spec_from_file_location("run_local_staging_proof", PROOF_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_uses_local_port_only():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "55432:5432" in text
    assert "supabase" not in text.lower()


def test_proof_db_name_requires_verify_suffix():
    validate_proof_db_name("whieda_platform_staging_verify_abc123")
    for bad in ("whieda_platform", "whieda_platform_staging_verify", "postgres"):
        try:
            validate_proof_db_name(bad)
            raise AssertionError(f"expected reject for {bad}")
        except ValueError:
            pass


def test_proof_rejects_prod_hosts():
    for host in ("db.supabase.co", "185.252.232.93", "prod.wwc.best"):
        try:
            validate_proof_host(host)
            raise AssertionError(f"expected reject for {host}")
        except ValueError:
            pass
    validate_proof_host(LOCAL_STAGING_HOST)


def test_proof_port_is_docker_only():
    validate_proof_port(LOCAL_STAGING_PORT)
    try:
        validate_proof_port(5432)
        raise AssertionError("expected reject for 5432")
    except ValueError:
        pass


def test_sql_order_matches_apply_ps1():
    assert apply_script_files() == APPLY_ORDER


def test_binding_context_is_last_apply_file_once():
    assert APPLY_ORDER.count("platform_bot_binding_context_v1.sql") == 1
    assert APPLY_ORDER[-1] == "platform_telegram_durable_outbox_v1.sql"
    assert APPLY_ORDER[-2] == "platform_tenant_release_price_plane_v1.sql"
    assert APPLY_ORDER[-3] == "platform_tenant_release_package_v1.sql"
    assert APPLY_ORDER[-4] == "platform_tenant_advisor_data_plane_v1.sql"
    assert APPLY_ORDER[-5] == "platform_telegram_durable_inbox_v1.sql"
    listed = apply_script_files()
    assert listed.count("platform_bot_binding_context_v1.sql") == 1
    assert listed.count("platform_telegram_durable_inbox_v1.sql") == 1
    assert listed.count("platform_telegram_durable_outbox_v1.sql") == 1
    assert listed.count("platform_tenant_advisor_data_plane_v1.sql") == 1
    assert listed.count("platform_tenant_release_package_v1.sql") == 1
    assert listed.count("platform_tenant_release_price_plane_v1.sql") == 1
    assert listed.count("platform_telegram_durable_outbox_v1.sql") == 1
    assert BACKFILL_PLAN.is_file()
    assert BACKFILL_PLAN.name not in listed
    assert BACKFILL_PLAN.name not in APPLY_PS1.read_text(encoding="utf-8")
    apply_text = APPLY_PS1.read_text(encoding="utf-8")
    assert "skip staging_seed_whieda_journey_v1.sql" in apply_text
    assert "-f `$Seed" not in apply_text and '-f $Seed' not in apply_text


def test_apply_order_sql_does_not_seed_nsp_maxim():
    sql_dir = ROOT / "postgres" / "sql"
    for name in APPLY_ORDER:
        text = (sql_dir / name).read_text(encoding="utf-8").lower()
        assert "nsp-maxim" not in text


def test_legacy_rls_covers_all_tenant_lead_tables():
    tables = tenant_scoped_lead_tables()
    covered = legacy_rls_covers_lead_tables()
    missing = tables - covered
    assert not missing, f"missing RLS policies for: {missing}"


def test_rls_proof_tables_are_covered_by_legacy_rls():
    covered = legacy_rls_covers_lead_tables()
    for table in RLS_PROOF_TABLES:
        assert table in covered


def test_verify_script_rejects_production_db_names():
    proc = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), "--db", "whieda_platform"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Refusing database name" in proc.stderr + proc.stdout


def test_manifest_status_counts_from_blocks():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    blocks = data["blocks"]
    counts = Counter(b["status"] for b in blocks)
    assert len(blocks) == 31
    assert counts["implemented_local"] == 27
    assert counts["live_blocked"] == 4


def test_allowed_db_regex_matches_proof_pattern():
    assert ALLOWED_VERIFY_DB.match("whieda_platform_staging_verify_deadbeef01")
    assert not ALLOWED_VERIFY_DB.match("whieda_platform_staging_verify")


def test_proof_cleanup_terminates_then_drops_in_separate_psql_calls(monkeypatch):
    proof = _load_proof_module()
    calls = []

    def fake_psql_exec(db, sql, **kwargs):
        calls.append((db, sql, kwargs))

    monkeypatch.setattr(proof, "psql_exec", fake_psql_exec)
    proof.drop_db("whieda_platform_staging_verify_deadbeef01")

    assert len(calls) == 2
    assert "pg_terminate_backend" in calls[0][1]
    assert "drop database" in calls[1][1].lower()
