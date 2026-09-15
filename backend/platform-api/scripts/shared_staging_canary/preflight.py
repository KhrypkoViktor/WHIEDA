"""Read-only shared-staging canary preflight. Extends Gate I; never writes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PLATFORM_API = Path(__file__).resolve().parents[2]
if str(_PLATFORM_API) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_API))
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


from app.telegram.tenant_media import normalize_media_base_url
from shared_staging_canary.catalog import select_import_skus
from shared_staging_canary.migrations import migration_errors, migration_files, migration_manifest_sha
from shared_staging_canary.snapshots import package_snapshots
from shared_staging_canary.target import CanaryTarget
from tenant_canary.preflight import evaluate_preflight
from tenant_release.package import load_package, sha256_file


def _disabled_canary_binding_ok(snapshot_path: Path, *, tenant_id: str) -> bool:
    """Gate M stages one disabled binding; activation belongs to a later release."""
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    rows = payload.get("bindings") if isinstance(payload, dict) else None
    matching = [
        row
        for row in (rows or [])
        if isinstance(row, dict) and str(row.get("tenant_id") or "").strip() == tenant_id
    ]
    return len(matching) == 1 and str(matching[0].get("status") or "").strip().lower() == "disabled"


def _state(*, gate_i_ok: bool, media_host_verified: bool, blocked: list[str]) -> str:
    if blocked:
        return "blocked"
    if gate_i_ok and media_host_verified:
        return "ready_to_apply"
    if gate_i_ok:
        return "catalog_only_ready"
    return "blocked"


def run_preflight(
    *,
    target: CanaryTarget,
    dsn: str,
    package: Path,
    tenant_id: str | None,
    media_base_url: str | None,
    media_manifest: Path | None,
    binding_snapshot: Path | None,
    runtime_snapshot: Path | None,
    connect=None,
) -> dict[str, Any]:
    blocked: list[str] = []
    package = package.resolve()
    loaded = load_package(package)
    tenant = (tenant_id or str(loaded.manifest.get("tenant_id") or "")).strip()
    skus, import_errors, _context = select_import_skus(package, tenant_id=tenant)
    blocked.extend(str(item.get("message") or item.get("code")) for item in import_errors)
    blocked.extend(migration_errors())

    media_base = normalize_media_base_url(media_base_url)
    if not media_base:
        blocked.append("PLATFORM_TENANT_MEDIA_BASE_URL must be HTTPS on our media host")

    snapshots = package_snapshots(
        package,
        tenant_id=tenant,
        media_manifest=media_manifest,
        binding_snapshot=binding_snapshot,
        runtime_snapshot=runtime_snapshot,
    )
    gate_i = None
    if snapshots.get("ok"):
        gate_i = evaluate_preflight(
            tenant_id=tenant,
            package_dir=package,
            media_manifest=snapshots["media_manifest"],
            binding_snapshot=snapshots["binding_snapshot"],
            runtime_snapshot=snapshots["runtime_snapshot"],
            media_base_url=media_base or "https://media.example.org/media",
            mode="plan",
        )
        allowed_disabled_binding_gap = _disabled_canary_binding_ok(
            snapshots["binding_snapshot"], tenant_id=tenant
        )
        for item in gate_i.findings:
            if allowed_disabled_binding_gap and item.category == "binding" and item.code == "active_binding_missing":
                continue
            blocked.append(f"{item.category}:{item.code}")
    else:
        blocked.append(str(snapshots.get("message") or "snapshots missing"))

    fingerprint = {
        "host": target.host,
        "port": target.port,
        "database": target.database,
        "user": target.user,
        "redacted_dsn": target.redacted_dsn,
    }
    live: dict[str, Any] = {}
    if connect is not None:
        live = connect(dsn, read_only=True)
        if live.get("current_database") != target.database:
            blocked.append(
                f"current_database {live.get('current_database')!r} != expected {target.database!r}"
            )
    elif dsn:
        try:
            import psycopg

            with psycopg.connect(dsn, connect_timeout=8) as conn:
                conn.execute("BEGIN READ ONLY")
                row = conn.execute(
                    "select current_database() as db, current_user as usr, "
                    "current_setting('is_superuser') as super"
                ).fetchone()
                live = {
                    "current_database": row[0],
                    "current_user": row[1],
                    "is_superuser": str(row[2]).lower() in {"on", "true"},
                }
                if live["current_database"] != target.database:
                    blocked.append(
                        f"current_database {live['current_database']!r} != expected {target.database!r}"
                    )
                if live["is_superuser"]:
                    blocked.append("connected role is superuser; shared staging forbids that")
        except Exception as exc:
            blocked.append(f"read-only connect failed: {type(exc).__name__}")

    media_host_verified = str(__import__("os").environ.get("PLATFORM_TENANT_MEDIA_HOST_VERIFIED") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    gate_i_ok = bool(gate_i) and not any(
        not (
            _disabled_canary_binding_ok(snapshots["binding_snapshot"], tenant_id=tenant)
            and item.category == "binding"
            and item.code == "active_binding_missing"
        )
        for item in (gate_i.findings if gate_i else [])
    )
    state = _state(gate_i_ok=gate_i_ok, media_host_verified=media_host_verified, blocked=blocked)
    return {
        "ok": state in {"ready_to_apply", "catalog_only_ready"},
        "state": state,
        "mode": "preflight",
        "tenant_id": tenant,
        "package_id": loaded.manifest.get("package_id"),
        "package_sha256": sha256_file(package / "manifest.json"),
        "migration_manifest_sha256": migration_manifest_sha(),
        "migration_files": migration_files(),
        "import_skus": skus,
        "import_count": len(skus),
        "target": fingerprint,
        "live": live,
        "gate_i": gate_i.to_dict() if gate_i else None,
        "blockers": blocked,
        "writes": False,
    }
