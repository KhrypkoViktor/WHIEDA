"""Staging SQL apply order (file presence + apply script)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
APPLY_SCRIPT = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
RLS_CORE = SQL_DIR / "platform_tenant_rls_v1.sql"
sys.path.insert(0, str(ROOT / "postgres" / "scripts"))

from staging_proof_lib import APPLY_ORDER, apply_script_files

EXPECTED_ORDER = list(APPLY_ORDER)


def test_all_sql_files_exist():
    for name in EXPECTED_ORDER:
        assert (SQL_DIR / name).is_file(), name


def test_apply_script_lists_full_order():
    assert apply_script_files() == EXPECTED_ORDER


def test_core_rls_does_not_touch_legacy_leads_tables():
    text = RLS_CORE.read_text(encoding="utf-8").lower()
    assert "website_leads" not in text
    assert "referral_profiles" not in text


def test_bot_binding_context_sql_is_in_apply_order_once():
    name = "platform_bot_binding_context_v1.sql"
    inbox = "platform_telegram_durable_inbox_v1.sql"
    assert (SQL_DIR / name).is_file()
    assert (SQL_DIR / inbox).is_file()
    listed = apply_script_files()
    assert listed.count(name) == 1
    assert listed.count(inbox) == 1
    assert EXPECTED_ORDER.count(name) == 1
    assert listed.index("platform_whieda_telegram_binding_v1.sql") < listed.index(name)
    assert listed.index(name) < listed.index(inbox)


def test_apply_script_lists_each_expected_file_once():
    listed = apply_script_files()
    assert listed == EXPECTED_ORDER
    assert len(listed) == len(set(listed))
    assert len(listed) == 14


def test_core_apply_sql_does_not_seed_nsp_maxim():
    for name in EXPECTED_ORDER:
        text = (SQL_DIR / name).read_text(encoding="utf-8").lower()
        assert "nsp-maxim" not in text
        assert "nsp_maxim" not in text
