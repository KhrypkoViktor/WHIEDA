"""Core Gate B2: shared-staging release harness guards (plan/preflight only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "postgres" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from shared_staging_release_lib import (  # noqa: E402
    APPLY_ORDER,
    EXPECTED_BINDING_CONTEXT_SHA256,
    BindingRow,
    ReleaseGuardError,
    build_offline_plan,
    evaluate_postcheck,
    evaluate_preflight,
    parse_dsn,
    refuse_apply,
    validate_shared_staging_target,
    verify_binding_context_hash,
)

RELEASE = SCRIPTS / "run_shared_staging_release.py"
POSTCHECK = SCRIPTS / "shared_staging_binding_postcheck.py"
ROLLBACK = SCRIPTS / "SHARED_STAGING_BINDING_ROLLBACK_PLAN_V1.md"

GOOD_DSN = (
    "postgresql://whieda_platform_staging:secret@staging.internal.example:5432/"
    "whieda_platform_staging"
)


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _complete_tables() -> dict[str, bool]:
    return {
        "tenants": True,
        "tenant_bot_bindings": True,
        "website_leads": True,
        "referral_profiles": True,
        "platform_session_context": True,
        "visitor_sessions": True,
        "identity_link_tokens": True,
        "onboarding_programs": True,
        "user_memory_facts": True,
        "pilot_daily_metrics": True,
        "data_retention_registry": True,
    }


def _binding_columns() -> list[str]:
    return [
        "binding_id",
        "tenant_id",
        "status",
        "webhook_secret_ref",
        "bot_token_ref",
        "bot_username",
        "processing_mode",
    ]


def test_plan_default_is_offline_and_correct():
    proc = _run(RELEASE)
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["mode"] == "plan"
    assert payload["ok"] is True
    assert payload["would_apply"] == list(APPLY_ORDER)
    assert len(payload["would_apply"]) == 41  # +lead_actor_channels_v11 (20.09.2026), +site_request_contacts_v12, +renewal_services_v13 (24.09.2026), +crm_v14, +academy_v1, +academy_shelf_v15 (25.09.2026), +support_site_forum_v16 (26.09.2026)  # +marketing_consent_v17 (26.09.2026)
    assert payload["sha256_binding_context"] == EXPECTED_BINDING_CONTEXT_SHA256
    assert payload["preconditions_failed"] == []
    assert "whieda" in payload["tenants_affected"]
    assert any("no network" in note for note in payload["notes"])
    assert any("backfill" in note.lower() for note in payload["notes"])


def test_explicit_plan_flag_matches_default():
    proc = _run(RELEASE, "--plan")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["mode"] == "plan"


def test_missing_dsn_preflight_fails():
    proc = _run(RELEASE, "--preflight")
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["ok"] is False
    assert any("missing DSN" in item for item in payload["preconditions_failed"])


def test_production_host_is_refused():
    with pytest.raises(ReleaseGuardError, match="185.252.232.93"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@185.252.232.93:5432/whieda_platform_staging"
        )


def test_supabase_host_is_refused():
    with pytest.raises(ReleaseGuardError, match="supabase"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@db.supabase.co:5432/whieda_platform_staging"
        )


def test_localhost_is_refused_for_shared_staging():
    with pytest.raises(ReleaseGuardError, match="127.0.0.1"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@127.0.0.1:55432/whieda_platform_staging"
        )
    with pytest.raises(ReleaseGuardError, match="localhost"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@localhost:5432/whieda_platform_staging"
        )


def test_foreign_database_is_refused():
    with pytest.raises(ReleaseGuardError, match="foreign database"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@staging.internal.example:5432/other_app"
        )
    with pytest.raises(ReleaseGuardError, match="local verify"):
        validate_shared_staging_target(
            "postgresql://whieda_platform_staging@staging.internal.example:5432/"
            "whieda_platform_staging_verify_deadbeef01"
        )


def test_stale_hash_fails():
    with pytest.raises(ReleaseGuardError, match="stale binding-context hash"):
        verify_binding_context_hash(expected="0" * 64)
    proc = _run(RELEASE, "--plan")
    assert json.loads(proc.stdout)["ok"] is True


def test_parse_dsn_redacts_password():
    target = parse_dsn(GOOD_DSN)
    assert "***" in target.redacted_dsn
    assert "secret" not in target.redacted_dsn
    assert target.database == "whieda_platform_staging"
    assert target.user == "whieda_platform_staging"


def test_apply_is_refused():
    report = refuse_apply()
    assert report.ok is False
    proc = _run(RELEASE, "--apply", "--dsn", GOOD_DSN)
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["mode"] == "apply"
    assert any("not enabled" in item for item in payload["preconditions_failed"])


def test_correct_preflight_snapshot():
    target = validate_shared_staging_target(GOOD_DSN)
    report = evaluate_preflight(
        target,
        current_database="whieda_platform_staging",
        current_user="whieda_platform_staging",
        is_superuser=False,
        tables_present=_complete_tables(),
        binding_columns=_binding_columns(),
        bindings=[
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
                processing_mode="core",
                bot_username="WHIEDA_Advisor_bot",
            )
        ],
        tenants=[{"tenant_id": "whieda", "status": "active"}],
    )
    assert report.ok is True
    assert report.would_apply == list(APPLY_ORDER)
    assert report.bindings[0]["binding_id"] == "whieda-advisor-bot"
    assert "whieda" in report.tenants_affected


def test_disabled_nsp_postcheck_passes():
    report = evaluate_postcheck(
        [
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
                processing_mode="core",
                bot_username="WHIEDA_Advisor_bot",
            ),
            BindingRow(
                binding_id="nsp-maxim-advisor-bot",
                tenant_id="nsp-maxim",
                status="disabled",
                processing_mode="core",
                bot_username="NSP_Leader_bot",
            ),
        ]
    )
    assert report.ok is True
    assert any("not found" in note for note in report.notes)


def test_nsp_absent_postcheck_passes():
    report = evaluate_postcheck(
        [
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
            )
        ]
    )
    assert report.ok is True
    assert any("NSP binding absent" in note for note in report.notes)


def test_unknown_binding_is_not_found():
    report = evaluate_postcheck(
        [
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
            )
        ]
    )
    assert all(row["binding_id"] != "unknown-binding-does-not-exist" for row in report.bindings)
    assert any("unknown-binding-does-not-exist: not found" in note for note in report.notes)


def test_active_nsp_or_whieda_hijack_fails_postcheck():
    hijack = evaluate_postcheck(
        [
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
            ),
            BindingRow(
                binding_id="nsp-maxim-advisor-bot",
                tenant_id="whieda",
                status="disabled",
            ),
        ]
    )
    assert hijack.ok is False
    active = evaluate_postcheck(
        [
            BindingRow(
                binding_id="whieda-advisor-bot",
                tenant_id="whieda",
                status="active",
            ),
            BindingRow(
                binding_id="nsp-maxim-advisor-bot",
                tenant_id="nsp-maxim",
                status="active",
            ),
        ]
    )
    assert active.ok is False


def test_postcheck_cli_with_snapshot(tmp_path: Path):
    snapshot = tmp_path / "bindings.json"
    snapshot.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "binding_id": "whieda-advisor-bot",
                        "tenant_id": "whieda",
                        "status": "active",
                        "processing_mode": "core",
                        "bot_username": "WHIEDA_Advisor_bot",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = _run(POSTCHECK, "--fetch-json", str(snapshot))
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["ok"] is True
    assert payload["mode"] == "postcheck"


def test_preflight_cli_uses_fetch_json(tmp_path: Path):
    snapshot = tmp_path / "preflight.json"
    snapshot.write_text(
        json.dumps(
            {
                "current_database": "whieda_platform_staging",
                "current_user": "whieda_platform_staging",
                "is_superuser": False,
                "tables_present": _complete_tables(),
                "binding_columns": _binding_columns(),
                "bindings": [
                    {
                        "binding_id": "whieda-advisor-bot",
                        "tenant_id": "whieda",
                        "status": "active",
                        "processing_mode": "core",
                        "bot_username": "WHIEDA_Advisor_bot",
                    }
                ],
                "tenants": [{"tenant_id": "whieda", "status": "active"}],
            }
        ),
        encoding="utf-8",
    )
    proc = _run(RELEASE, "--preflight", "--dsn", GOOD_DSN, "--fetch-json", str(snapshot))
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["ok"] is True
    assert payload["mode"] == "preflight"


def test_rollback_plan_is_manual_only():
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "no `--rollback`" in text.lower() or "does **not** run rollback" in text
    assert "setWebhook" in text
    assert "DROP TABLE `tenant_bot_bindings`" in text
    release = RELEASE.read_text(encoding="utf-8")
    assert "--rollback" not in release
    assert "apply is not enabled" in refuse_apply().preconditions_failed[0]


def test_build_offline_plan_function():
    report = build_offline_plan()
    assert report.ok
    assert len(report.would_apply) == 41  # +lead_actor_channels_v11 (20.09.2026), +site_request_contacts_v12, +renewal_services_v13 (24.09.2026), +crm_v14, +academy_v1, +academy_shelf_v15 (25.09.2026), +support_site_forum_v16 (26.09.2026)  # +marketing_consent_v17 (26.09.2026)
    assert report.sha256_binding_context == EXPECTED_BINDING_CONTEXT_SHA256
