"""Read-only verify after shared-staging tenant canary apply."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PLATFORM_API = Path(__file__).resolve().parents[2]
if str(_PLATFORM_API) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_API))
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


from app.telegram.tenant_media import filename_from_local_ref, published_media_url
from shared_staging_canary.catalog import select_import_skus
from shared_staging_canary.migrations import migration_errors
from shared_staging_canary.preflight import run_preflight
from shared_staging_canary.target import CanaryTarget
from tenant_release.package import load_package


def run_verify(
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
    loaded = load_package(package)
    tenant = (tenant_id or str(loaded.manifest.get("tenant_id") or "")).strip()
    skus, errors, _context = select_import_skus(package, tenant_id=tenant)
    blockers = list(preflight.get("blockers") or [])
    blockers.extend(str(item.get("message") or item) for item in errors)
    blockers.extend(migration_errors())

    live: dict[str, Any] = {}
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=8) as conn:
            conn.execute("BEGIN READ ONLY")
            db = conn.execute("select current_database()").fetchone()[0]
            if db != target.database:
                blockers.append(f"current_database {db!r} != expected {target.database!r}")
            product_rows = conn.execute(
                "select client_id, sku from advisor_structured_products"
            ).fetchall()
            by_tenant: dict[str, list[str]] = {}
            for client_id, sku in product_rows:
                by_tenant.setdefault(str(client_id), []).append(str(sku))
            tenant_skus = sorted(by_tenant.get(tenant) or [])
            if tenant_skus != sorted(skus):
                blockers.append(f"tenant sku mismatch: {tenant_skus} != {sorted(skus)}")
            review_hits = conn.execute(
                """
                select sku from advisor_structured_products
                where client_id = %s and sku = any(%s)
                """,
                (tenant, ["3538", "RU21912"]),
            ).fetchall()
            if review_hits:
                blockers.append("review-required SKU present in tenant catalog")
            binding = conn.execute(
                """
                select binding_id, tenant_id, status
                from tenant_bot_bindings
                where tenant_id = %s
                """,
                (tenant,),
            ).fetchall()
            if not binding:
                blockers.append("tenant binding missing")
            for _binding_id, _tenant, status in binding:
                if status != "disabled":
                    blockers.append(f"tenant binding status {status!r} is not disabled")
            whieda = conn.execute(
                """
                select tenant_id, status from tenant_bot_bindings
                where binding_id = 'whieda-advisor-bot'
                """
            ).fetchone()
            if whieda is None or whieda[0] != "whieda" or whieda[1] != "active":
                blockers.append("WHIEDA binding missing or changed")
            occupant = sorted(by_tenant.get("whieda") or [])
            live = {
                "tenant_skus": tenant_skus,
                "whieda_skus": occupant,
                "bindings": [
                    {"binding_id": row[0], "tenant_id": row[1], "status": row[2]} for row in binding
                ],
            }
    except Exception as exc:
        blockers.append(f"verify connect failed: {type(exc).__name__}")

    media_urls = []
    for row in loaded.layers.get("media") or []:
        sku = str(row.get("sku") or "")
        if sku not in skus:
            continue
        filename = filename_from_local_ref(
            str(row.get("url") or row.get("relative_path") or ""),
            tenant_id=tenant,
            sku=sku,
        ) or str(row.get("filename") or "")
        url = published_media_url(
            tenant_id=tenant,
            sku=sku,
            filename=filename,
            base_url=media_base_url,
        )
        media_urls.append(url)
        if url and not str(url).startswith("https://"):
            blockers.append("media URL is not HTTPS")

    ok = not blockers
    return {
        "ok": ok,
        "mode": "verify",
        "state": "catalog_only" if ok else "blocked",
        "tenant_id": tenant,
        "photo_delivery": "catalog_only",
        "media_urls": media_urls,
        "live": live,
        "preflight": preflight,
        "blockers": blockers,
        "target": preflight.get("target"),
        "writes": False,
    }
