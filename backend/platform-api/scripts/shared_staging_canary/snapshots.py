"""Build Gate I snapshots from a package when live files are not passed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tenant_release.package import validate_package, write_json


def package_snapshots(
    package: Path,
    *,
    tenant_id: str,
    media_manifest: Path | None,
    binding_snapshot: Path | None,
    runtime_snapshot: Path | None,
) -> dict[str, Any]:
    package = package.resolve()
    sibling = package.parent
    manifest = media_manifest or sibling / "media-manifest.tsv"
    binding = binding_snapshot or sibling / "binding.json"
    runtime = runtime_snapshot or sibling / "runtime.json"
    generated = sibling / ".generated-canary-snapshots"
    if not manifest.is_file():
        return {"ok": False, "message": f"media manifest not found: {manifest}"}
    if not binding.is_file():
        generated.mkdir(parents=True, exist_ok=True)
        write_json(
            generated / "binding.json",
            {
                "tenant_id": tenant_id,
                "tenant_status": "active",
                "bindings": [
                    {
                        "binding_id": f"{tenant_id}-canary-bot",
                        "tenant_id": tenant_id,
                        "status": "disabled",
                        "bot_username": f"{tenant_id.replace('-', '_')}_bot",
                        "processing_mode": "core",
                    }
                ],
            },
        )
        binding = generated / "binding.json"
    if not runtime.is_file():
        report = validate_package(package)
        generated.mkdir(parents=True, exist_ok=True)
        write_json(
            generated / "runtime.json",
            {
                "tenant_id": tenant_id,
                "candidates": [
                    {"tenant_id": tenant_id, "sku": sku, "review_status": "approved"}
                    for sku in report.eligible_skus
                ],
            },
        )
        runtime = generated / "runtime.json"
    payload = json.loads(binding.read_text(encoding="utf-8"))
    for row in payload.get("bindings") or []:
        if str(row.get("tenant_id") or "") == tenant_id and str(row.get("status") or "") != "disabled":
            row["status"] = "disabled"
    if binding_snapshot is None:
        generated.mkdir(parents=True, exist_ok=True)
        write_json(generated / "binding.json", payload)
        binding = generated / "binding.json"
    return {
        "ok": True,
        "media_manifest": manifest,
        "binding_snapshot": binding,
        "runtime_snapshot": runtime,
    }
