"""Controlled shared-staging apply. Requires preflight ready and confirm."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_POSTGRES = Path(__file__).resolve().parents[4] / "postgres" / "scripts"
if str(_POSTGRES) not in sys.path:
    sys.path.insert(0, str(_POSTGRES))


from shared_staging_canary.catalog import render_import_sql
from shared_staging_canary.migrations import migration_manifest_sha
from shared_staging_canary.preflight import run_preflight
from shared_staging_canary.target import CanaryTarget
from tenant_release.package import load_package, sha256_file

LOCK_SQL = "SELECT pg_advisory_lock(hashtext('whieda.shared_staging.tenant_canary'))"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('whieda.shared_staging.tenant_canary'))"


def _backup_dir() -> Path:
    configured = str(os.environ.get("WHIEDA_SHARED_STAGING_REPORT_DIR") or "").strip()
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "reports" / "shared_staging_canary"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_apply(
    *,
    target: CanaryTarget,
    dsn: str,
    package: Path,
    tenant_id: str | None,
    media_base_url: str | None,
    media_manifest: Path | None,
    binding_snapshot: Path | None,
    runtime_snapshot: Path | None,
) -> dict[str, Any]:
    preflight = run_preflight(
        target=target,
        dsn=dsn,
        package=package,
        tenant_id=tenant_id,
        media_base_url=media_base_url,
        media_manifest=media_manifest,
        binding_snapshot=binding_snapshot,
        runtime_snapshot=runtime_snapshot,
    )
    if preflight.get("state") not in {"ready_to_apply", "catalog_only_ready"}:
        return {**preflight, "ok": False, "mode": "apply"}

    loaded = load_package(package)
    tenant = (tenant_id or str(loaded.manifest.get("tenant_id") or "")).strip()
    sql, planned = render_import_sql(package, tenant_id=tenant)
    if sql is None or not planned.ok:
        return {
            "ok": False,
            "mode": "apply",
            "state": "blocked",
            "errors": planned.errors,
            "target": preflight.get("target"),
        }

    backup = _backup_dir()
    import psycopg
    from staging_proof_lib import APPLY_ORDER, SQL_DIR

    run_record = {
        "mode": "apply",
        "database": target.database,
        "host": target.host,
        "package_id": loaded.manifest.get("package_id"),
        "package_sha256": sha256_file(package / "manifest.json"),
        "migration_manifest_sha256": migration_manifest_sha(),
        "tenant_id": tenant,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (backup / "run_record.json").write_text(
        json.dumps(run_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        with psycopg.connect(dsn, connect_timeout=15) as conn:
            conn.execute(LOCK_SQL)
            snapshot = conn.execute(
                """
                select current_database() as db,
                       (select count(*) from tenants) as tenants,
                       (select count(*) from tenant_bot_bindings) as bindings
                """
            ).fetchone()
            (backup / "before.json").write_text(
                json.dumps(
                    {
                        "database": snapshot[0],
                        "tenant_count": int(snapshot[1]),
                        "binding_count": int(snapshot[2]),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            for name in APPLY_ORDER:
                conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
            conn.execute(sql)
            conn.commit()
            conn.execute(UNLOCK_SQL)
            conn.commit()
    except Exception as exc:
        return {
            "ok": False,
            "mode": "apply",
            "state": "blocked",
            "message": f"{type(exc).__name__}: migration/import stopped; see rollback plan",
            "backup_dir": str(backup),
            "target": preflight.get("target"),
            "preflight": preflight,
        }

    run_record["finished_at"] = datetime.now(timezone.utc).isoformat()
    run_record["imported_skus"] = planned.imported_skus
    (backup / "run_record.json").write_text(
        json.dumps(run_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "mode": "apply",
        "state": preflight.get("state"),
        "tenant_id": tenant,
        "imported_skus": planned.imported_skus,
        "backup_dir": str(backup),
        "target": preflight.get("target"),
        "package_sha256": run_record["package_sha256"],
        "migration_manifest_sha256": run_record["migration_manifest_sha256"],
        "preflight": preflight,
    }
