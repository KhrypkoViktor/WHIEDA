"""Core Gate M: controlled shared-staging tenant canary runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend" / "platform-api" / "scripts"
CLI = SCRIPTS / "run_shared_staging_tenant_canary.py"
sys.path.insert(0, str(SCRIPTS))

from shared_staging_canary.catalog import MemoryCatalog, import_tenant_catalog  # noqa: E402
from shared_staging_canary.preflight import run_preflight  # noqa: E402
from shared_staging_canary.rollback import render_rollback_plan  # noqa: E402
from shared_staging_canary.target import (  # noqa: E402
    TargetGuardError,
    validate_canary_target,
)
from tenant_release.package import seal_package, write_json  # noqa: E402

STAGING_DSN = (
    "postgresql://whieda_platform_staging:secret@staging.internal.example:5432/"
    "whieda_platform_staging"
)
RUNTIME_DSN = (
    "postgresql://whieda_ro:secret@db.supabase.co:5432/postgres"
)


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in (
        "WHIEDA_SHARED_STAGING_DSN",
        "WHIEDA_SHARED_STAGING_EXPECTED_DB",
        "WHIEDA_RUNTIME_READONLY_DSN",
        "PLATFORM_TENANT_MEDIA_BASE_URL",
    ):
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged,
    )


def _package(tmp_path: Path, *, tenant_id: str = "tenant-lab", extra_products: list[dict] | None = None) -> Path:
    directory = tmp_path / tenant_id
    directory.mkdir()
    products = [
        {
            "tenant_id": tenant_id,
            "sku": "LAB-01",
            "canonical_name": "Lab Tonic",
            "review_status": "approved",
            "price_missing": False,
            "media_state": "present",
            "source": {"kind": "synthetic-fixture", "ref": "lab:LAB-01"},
            "prices": [{"kind": "retail", "amount": "21.00", "currency": "USD", "source": "catalogue"}],
        },
        {
            "tenant_id": tenant_id,
            "sku": "LAB-RR",
            "canonical_name": "Lab Lotion",
            "review_status": "review_required",
            "price_missing": False,
            "media_state": "missing",
            "source": {"kind": "synthetic-fixture", "ref": "lab:LAB-RR"},
            "prices": [{"kind": "retail", "amount": "9.00", "currency": "USD", "source": "catalogue"}],
        },
    ]
    if extra_products:
        products.extend(extra_products)
    write_json(directory / "products.json", products)
    write_json(
        directory / "aliases.json",
        [{"tenant_id": tenant_id, "alias": "lab tonic", "canonical_sku": "LAB-01"}],
    )
    write_json(
        directory / "cards.json",
        [{"tenant_id": tenant_id, "sku": "LAB-01", "canonical_name": "Lab Tonic", "what_it_is": "LAB-CARD"}],
    )
    write_json(
        directory / "media.json",
        [
            {
                "tenant_id": tenant_id,
                "sku": "LAB-01",
                "resource_type": "image",
                "url": f"media/{tenant_id}/LAB-01/main.webp",
                "filename": "main.webp",
                "title": "Lab Tonic",
            }
        ],
    )
    write_json(
        directory / "faq.json",
        [
            {
                "tenant_id": tenant_id,
                "faq_id": "lab-faq",
                "sku": "LAB-01",
                "title": "Lab Tonic",
                "answer_text": "LAB-FAQ",
            }
        ],
    )
    write_json(
        directory / "manifest.json",
        {
            "schema_version": "tenant-release-package.v1",
            "package_id": f"{tenant_id}-canary",
            "package_version": "1.0.0",
            "tenant_id": tenant_id,
            "release_status": "candidate",
            "display": {"display_name": tenant_id, "advisor_signature": "советник"},
            "files": {
                "products": {"path": "products.json", "sha256": ""},
                "aliases": {"path": "aliases.json", "sha256": ""},
                "cards": {"path": "cards.json", "sha256": ""},
                "media": {"path": "media.json", "sha256": ""},
                "faq": {"path": "faq.json", "sha256": ""},
            },
            "source": {"kind": "synthetic-fixture", "ref": tenant_id},
        },
    )
    seal_package(directory)
    return directory


def test_default_invocation_does_nothing_and_refuses_network():
    proc = _run()
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["code"] == "mode_required"
    combined = (proc.stdout + proc.stderr).lower()
    assert "compose" not in combined
    assert "psycopg" not in combined


def test_apply_without_confirm_is_refused():
    proc = _run("--apply", "--package", str(Path(".")))
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["code"] == "confirm_required"


def test_publish_is_refused():
    proc = _run("--publish", "--package", str(Path(".")))
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["code"] == "action_refused"


def test_preflight_missing_env_does_not_open_network():
    proc = _run("--preflight", "--package", str(Path(".")))
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["state"] == "blocked"
    combined = (proc.stdout + proc.stderr).lower()
    assert "compose up" not in combined


def test_localhost_dsn_is_refused_for_shared_staging():
    with pytest.raises(TargetGuardError, match="localhost|127.0.0.1"):
        validate_canary_target(
            "postgresql://whieda_platform_staging:x@127.0.0.1:55432/whieda_platform_staging",
            expected_db="whieda_platform_staging",
            runtime_readonly_dsn=None,
        )


def test_runtime_readonly_dsn_is_refused_as_write_target():
    with pytest.raises(TargetGuardError, match="READONLY"):
        validate_canary_target(
            RUNTIME_DSN,
            expected_db="postgres",
            runtime_readonly_dsn=RUNTIME_DSN,
        )


def test_production_host_is_refused():
    with pytest.raises(TargetGuardError, match="supabase|185.252.232.93"):
        validate_canary_target(
            "postgresql://whieda_platform_staging:x@db.supabase.co:5432/whieda_platform_staging",
            expected_db="whieda_platform_staging",
            runtime_readonly_dsn=None,
        )


def test_expected_database_must_match_dsn():
    with pytest.raises(TargetGuardError, match="expected database"):
        validate_canary_target(
            STAGING_DSN,
            expected_db="other_staging",
            runtime_readonly_dsn=None,
        )


def test_valid_staging_target_is_accepted():
    target = validate_canary_target(
        STAGING_DSN,
        expected_db="whieda_platform_staging",
        runtime_readonly_dsn=RUNTIME_DSN,
    )
    assert target.database == "whieda_platform_staging"
    assert "***" in target.redacted_dsn
    assert "secret" not in target.redacted_dsn


def test_review_required_sku_is_not_imported(tmp_path: Path):
    package_dir = _package(tmp_path)
    catalog = MemoryCatalog()
    catalog.seed_product("occupant", "KEEP-01", "Occupant Only")
    result = import_tenant_catalog(catalog, package_dir=package_dir, tenant_id="tenant-lab")
    assert result.ok
    assert result.imported_skus == ["LAB-01"]
    assert "LAB-RR" not in catalog.product_skus("tenant-lab")
    assert catalog.product_skus("occupant") == ["KEEP-01"]
    assert catalog.canonical_name("occupant", "KEEP-01") == "Occupant Only"


def test_import_transaction_rolls_back_on_error(tmp_path: Path):
    package_dir = _package(tmp_path)
    catalog = MemoryCatalog()
    catalog.seed_product("occupant", "KEEP-01", "Occupant Only")
    catalog.fail_after = "LAB-01"
    result = import_tenant_catalog(catalog, package_dir=package_dir, tenant_id="tenant-lab")
    assert result.ok is False
    assert catalog.product_skus("tenant-lab") == []
    assert catalog.canonical_name("occupant", "KEEP-01") == "Occupant Only"


def test_usd_only_import_rejects_fx_or_partner_price(tmp_path: Path):
    package_dir = _package(
        tmp_path,
        extra_products=[
            {
                "tenant_id": "tenant-lab",
                "sku": "LAB-FX",
                "canonical_name": "Lab FX",
                "review_status": "approved",
                "price_missing": False,
                "media_state": "present",
                "source": {"kind": "synthetic-fixture", "ref": "lab:LAB-FX"},
                "prices": [
                    {"kind": "retail", "amount": "21.00", "currency": "USD", "source": "catalogue"},
                    {"kind": "retail", "amount": "70.00", "currency": "BYN", "source": "fx"},
                ],
            }
        ],
    )
    catalog = MemoryCatalog()
    result = import_tenant_catalog(catalog, package_dir=package_dir, tenant_id="tenant-lab")
    assert result.ok is False
    assert any(item.get("code") == "usd_only_violation" for item in result.errors)
    assert catalog.product_skus("tenant-lab") == []


def test_disabled_binding_is_written_and_webhook_is_not():
    catalog = MemoryCatalog()
    catalog.upsert_binding(
        binding_id="lab-bot",
        tenant_id="tenant-lab",
        status="disabled",
        bot_token_ref="env:LAB_BOT_TOKEN",
    )
    row = catalog.binding("lab-bot")
    assert row["status"] == "disabled"
    assert catalog.webhooks_set == []


def test_disabled_canary_binding_is_ready_for_catalog_only_stage(tmp_path: Path):
    """Gate M must not demand activation before it imports a disabled canary."""
    fixture = ROOT / "qa" / "tenant_canary_preflight" / "tenants" / "tenant-north"
    binding = json.loads((fixture / "binding.json").read_text(encoding="utf-8"))
    binding["bindings"][0]["status"] = "disabled"
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    target = validate_canary_target(
        STAGING_DSN,
        expected_db="whieda_platform_staging",
        runtime_readonly_dsn=RUNTIME_DSN,
    )
    report = run_preflight(
        target=target,
        dsn=STAGING_DSN,
        package=fixture / "package",
        tenant_id="tenant-north",
        media_base_url="https://media.example.org/media",
        media_manifest=fixture / "media-manifest.tsv",
        binding_snapshot=binding_path,
        runtime_snapshot=fixture / "runtime.json",
        connect=lambda _dsn, read_only: {
            "current_database": "whieda_platform_staging",
            "current_user": "whieda_platform_staging",
            "is_superuser": False,
        },
    )
    assert report["ok"] is True
    assert report["state"] == "catalog_only_ready"


def test_rollback_plan_is_markdown_and_does_not_need_db():
    text = render_rollback_plan(
        database="whieda_platform_staging",
        tenant_id="tenant-lab",
        package_id="tenant-lab-canary",
    )
    assert text.startswith("#")
    assert "DROP TABLE" not in text
    assert "api.telegram.org" not in text.lower()
    assert "tenant-lab" in text
    assert "advisory" in text.lower()


def test_nsp_maxim_requires_seventeen_usd_sku(tmp_path: Path):
    package_dir = _package(tmp_path, tenant_id="nsp-maxim")
    catalog = MemoryCatalog()
    result = import_tenant_catalog(catalog, package_dir=package_dir, tenant_id="nsp-maxim")
    assert result.ok is False
    assert any(item.get("code") == "nsp_approved_count" for item in result.errors)
    assert catalog.product_skus("nsp-maxim") == []


def test_empty_package_aborts(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "manifest.json").write_text("{}", encoding="utf-8")
    catalog = MemoryCatalog()
    result = import_tenant_catalog(catalog, package_dir=empty, tenant_id="tenant-lab")
    assert result.ok is False
    assert catalog.product_skus("tenant-lab") == []


def test_ensure_local_db_script_refuses_apply():
    script = ROOT / "postgres" / "scripts" / "ensure_local_shared_staging_canary_database.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--apply"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 1
    assert "action_refused" in proc.stdout


def test_cli_rollback_plan_does_not_open_dsn():
    proc = _run("--rollback-plan", "--package", str(Path(".")), "--tenant", "tenant-lab")
    assert proc.returncode == 0
    assert proc.stdout.startswith("#")
    assert "postgresql://" not in proc.stdout
    combined = (proc.stdout + proc.stderr).lower()
    assert "compose" not in combined
