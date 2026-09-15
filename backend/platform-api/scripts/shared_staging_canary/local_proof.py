"""Local Docker proof for the shared-staging canary runner. Not shared staging."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from shared_staging_canary.catalog import render_import_sql
from shared_staging_canary.migrations import migration_manifest_sha
from shared_staging_canary.rollback import render_rollback_plan
from tenant_release.package import load_package, sha256_file

_ROOT = Path(__file__).resolve().parents[4]
_POSTGRES = _ROOT / "postgres"
_ENSURE = _POSTGRES / "scripts" / "ensure_local_shared_staging_canary_database.py"
_COMPOSE = _POSTGRES / "docker-compose.local-staging.yml"
_DEFAULT_PACKAGE = (
    _ROOT / "qa" / "tenant_canary_preflight" / "tenants" / "tenant-north" / "package"
)
CANARY_DB = "whieda_platform_shared_staging_local"
CONTAINER = "whieda-local-staging-postgres"
SUPERUSER = "postgres"
SUPERPASSWORD = "local_staging_proof"


def _run(cmd: list[str], *, input_text: str | None = None, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=input_text,
        cwd=cwd,
    )


def _psql(sql: str, *, db: str = CANARY_DB) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={SUPERPASSWORD}",
            CONTAINER,
            "psql",
            "-U",
            SUPERUSER,
            "-d",
            db,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ]
    )


def _psql_script(sql: str, *, db: str = CANARY_DB) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={SUPERPASSWORD}",
            CONTAINER,
            "psql",
            "-U",
            SUPERUSER,
            "-d",
            db,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ],
        input_text=sql,
    )


def run_local_proof(package: Path | None) -> int:
    package = (package or _DEFAULT_PACKAGE).resolve()
    if not (package / "manifest.json").is_file():
        print(
            json.dumps(
                {"ok": False, "code": "package_required", "message": f"package not found: {package}"},
                ensure_ascii=False,
            )
        )
        return 1

    up = _run(["docker", "compose", "-f", str(_COMPOSE), "up", "-d"], cwd=str(_POSTGRES))
    if up.returncode != 0:
        print(json.dumps({"ok": False, "code": "docker_required", "message": "local postgres compose failed"}))
        return 1

    ensure = _run([sys.executable, str(_ENSURE)])
    print(ensure.stdout, end="" if ensure.stdout.endswith("\n") else "\n")
    if ensure.returncode != 0:
        print(ensure.stderr, file=sys.stderr)
        print(json.dumps({"ok": False, "code": "ensure_failed", "state": "local_verified_failed"}))
        return 1

    loaded = load_package(package)
    tenant_id = str(loaded.manifest.get("tenant_id") or "").strip()
    occupant_before = _psql(
        "SELECT sku FROM advisor_structured_products WHERE client_id = 'whieda' ORDER BY sku;"
    ).stdout.strip()
    whieda_before = _psql(
        "SELECT tenant_id || ':' || status FROM tenant_bot_bindings WHERE binding_id = 'whieda-advisor-bot';"
    ).stdout.strip()

    sql, planned = render_import_sql(package, tenant_id=tenant_id)
    if sql is None or not planned.ok:
        print(json.dumps({"ok": False, "mode": "local-proof", "errors": planned.errors}, ensure_ascii=False, indent=2))
        return 2

    first = _psql_script(sql)
    if first.returncode != 0:
        print(first.stderr[-800:], file=sys.stderr)
        print(json.dumps({"ok": False, "code": "import_failed", "pass": 1}))
        return 2
    second = _psql_script(sql)
    if second.returncode != 0:
        print(second.stderr[-800:], file=sys.stderr)
        print(json.dumps({"ok": False, "code": "import_failed", "pass": 2}))
        return 2

    tenant_skus = _psql(
        f"SELECT coalesce(string_agg(sku, ',' ORDER BY sku), '') FROM advisor_structured_products WHERE client_id = '{tenant_id}';"
    ).stdout.strip()
    occupant_after = _psql(
        "SELECT sku FROM advisor_structured_products WHERE client_id = 'whieda' ORDER BY sku;"
    ).stdout.strip()
    binding = _psql(
        f"SELECT status FROM tenant_bot_bindings WHERE tenant_id = '{tenant_id}' ORDER BY binding_id;"
    ).stdout.strip()
    whieda_after = _psql(
        "SELECT tenant_id || ':' || status FROM tenant_bot_bindings WHERE binding_id = 'whieda-advisor-bot';"
    ).stdout.strip()
    sku_count = _psql(
        f"SELECT count(*) FROM advisor_structured_products WHERE client_id = '{tenant_id}';"
    ).stdout.strip()
    alias_count = _psql(
        f"SELECT count(*) FROM advisor_structured_aliases WHERE client_id = '{tenant_id}';"
    ).stdout.strip()
    card_count = _psql(
        f"SELECT count(*) FROM advisor_structured_product_cards WHERE client_id = '{tenant_id}';"
    ).stdout.strip()
    resource_count = _psql(
        f"SELECT count(*) FROM advisor_structured_resources WHERE client_id = '{tenant_id}';"
    ).stdout.strip()

    blockers: list[str] = []
    expected = ",".join(planned.imported_skus)
    if tenant_skus != expected:
        blockers.append(f"sku mismatch {tenant_skus!r} != {expected!r}")
    if occupant_after != occupant_before:
        blockers.append("WHIEDA occupant catalog changed")
    if whieda_after != whieda_before or whieda_after != "whieda:active":
        blockers.append("WHIEDA binding changed")
    if any(status != "disabled" for status in binding.splitlines() if status):
        blockers.append(f"canary binding not disabled: {binding!r}")
    if "3538" in tenant_skus or "RU21912" in tenant_skus:
        blockers.append("review-required SKU imported")
    if sku_count != str(len(planned.imported_skus)):
        blockers.append("duplicate sku rows after second apply")

    reports = Path(__file__).resolve().parents[2] / "reports" / "shared_staging_canary"
    reports.mkdir(parents=True, exist_ok=True)
    plan_path = reports / "ROLLBACK_PLAN.md"
    plan_path.write_text(
        render_rollback_plan(
            database=CANARY_DB,
            tenant_id=tenant_id,
            package_id=str(loaded.manifest.get("package_id") or ""),
        ),
        encoding="utf-8",
    )
    payload: dict[str, Any] = {
        "ok": not blockers,
        "mode": "local-proof",
        "local_verified": not blockers,
        "shared_staging_not_run": True,
        "state": "catalog_only_ready" if not blockers else "blocked",
        "tenant_id": tenant_id,
        "package_id": loaded.manifest.get("package_id"),
        "package_sha256": sha256_file(package / "manifest.json"),
        "migration_manifest_sha256": migration_manifest_sha(),
        "apply_passes": 2,
        "counts": {
            "sku": int(sku_count or 0),
            "aliases": int(alias_count or 0),
            "cards": int(card_count or 0),
            "resources": int(resource_count or 0),
        },
        "binding_status": binding.splitlines(),
        "photo_delivery": "catalog_only",
        "rollback_plan": str(plan_path),
        "blockers": blockers,
        "target": {"database": CANARY_DB, "host": "127.0.0.1", "port": 55432, "user": SUPERUSER},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("\n=== SHARED STAGING TENANT CANARY LOCAL PROOF: "
          + ("PASS" if payload["ok"] else "FAIL")
          + " ===")
    return 0 if payload["ok"] else 2
