"""Read-only export snapshot fingerprints (no row content stored)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dqc.loader import load_table


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_count(path: Path, fmt: str) -> int:
    try:
        return len(load_table(path, fmt))
    except Exception:
        return 0


def _resolve_source_path(root: Path, manifest: dict[str, Any], src: dict[str, Any]) -> Path:
    file_path = src.get("path") or str(Path(manifest.get("exports_root", "qa/data_quality/exports")) / src["file"])
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    return path


def build_snapshot(*, root: Path, manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    for src in manifest.get("sources", []):
        layer = src["layer"]
        path = _resolve_source_path(root, manifest, src)
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        entry: dict[str, Any] = {
            "layer": layer,
            "path": rel.replace("\\", "/"),
            "status": "found" if path.is_file() else "missing",
        }
        if path.is_file():
            entry["size_bytes"] = path.stat().st_size
            entry["sha256"] = _sha256_file(path)
            entry["row_count"] = _row_count(path, src.get("format", "tsv"))
        layers[layer] = entry

    rel_manifest = (
        str(manifest_path.relative_to(root)).replace("\\", "/")
        if manifest_path.is_relative_to(root)
        else str(manifest_path)
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": rel_manifest,
        "layers": layers,
    }


def save_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_snapshot(*, root: Path, manifest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    current = build_snapshot(root=root, manifest_path=Path(snapshot.get("manifest", "")), manifest=manifest)
    prev_layers = snapshot.get("layers") or {}
    curr_layers = current.get("layers") or {}

    changed: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []

    all_layers = sorted(set(prev_layers) | set(curr_layers))
    for layer in all_layers:
        prev = prev_layers.get(layer) or {"layer": layer, "status": "missing"}
        curr = curr_layers.get(layer) or {"layer": layer, "status": "missing"}
        prev_status = prev.get("status", "missing")
        curr_status = curr.get("status", "missing")

        if prev_status == "found" and curr_status == "missing":
            removed.append({"layer": layer, "path": prev.get("path", "")})
            continue
        if prev_status == "missing" and curr_status == "found":
            added.append({"layer": layer, "path": curr.get("path", "")})
            continue
        if prev_status == "found" and curr_status == "found":
            if prev.get("sha256") != curr.get("sha256"):
                changed.append(
                    {
                        "layer": layer,
                        "path": curr.get("path", ""),
                        "previous_sha256": prev.get("sha256"),
                        "current_sha256": curr.get("sha256"),
                        "previous_row_count": prev.get("row_count"),
                        "current_row_count": curr.get("row_count"),
                    }
                )

    status = "PASS" if not (changed or removed or added) else "FAIL"
    return {
        "status": status,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "verified_at": current.get("generated_at"),
        "changed": changed,
        "removed": removed,
        "added": added,
        "summary": {
            "changed_count": len(changed),
            "removed_count": len(removed),
            "added_count": len(added),
        },
    }


def format_verify_report(result: dict[str, Any]) -> str:
    lines = [
        f"Snapshot verify status: {result['status']}",
        f"Snapshot from: {result.get('snapshot_generated_at', '?')}",
        f"Verified at: {result.get('verified_at', '?')}",
        "",
    ]
    if not (result.get("changed") or result.get("removed") or result.get("added")):
        lines.append("No layer file changes detected.")
        return "\n".join(lines)

    for title, key in (("Changed files", "changed"), ("Removed files", "removed"), ("Added files", "added")):
        items = result.get(key) or []
        lines.append(f"{title}: {len(items)}")
        for item in items:
            if key == "changed":
                lines.append(
                    f"  - {item['layer']} ({item['path']}): rows {item.get('previous_row_count')} -> {item.get('current_row_count')}"
                )
            else:
                lines.append(f"  - {item['layer']} ({item.get('path', '')})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
