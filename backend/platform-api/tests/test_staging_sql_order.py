"""Staging SQL apply order matches script and verify helper."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
APPLY_SCRIPT = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
VERIFY_SCRIPT = ROOT / "postgres" / "scripts" / "verify_staging_apply_empty.py"

EXPECTED_ORDER = [
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


def test_all_sql_files_exist():
    for name in EXPECTED_ORDER:
        assert (SQL_DIR / name).is_file(), name


def test_apply_script_lists_full_order():
    text = APPLY_SCRIPT.read_text(encoding="utf-8")
    positions = [text.index(name) for name in EXPECTED_ORDER]
    assert positions == sorted(positions), "apply_staging_platform_all.ps1 order wrong"


def test_verify_script_same_order():
    text = VERIFY_SCRIPT.read_text(encoding="utf-8")
    for name in EXPECTED_ORDER:
        assert name in text
