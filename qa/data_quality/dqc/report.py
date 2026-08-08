"""Report generation (Markdown + JSON)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def overall_status(issues: list[dict[str, Any]], missing: list[dict[str, Any]]) -> str:
    if any(i["severity"] == "error" for i in issues):
        return "FAIL"
    if any(i["severity"] == "warning" for i in issues) or missing:
        return "WARN"
    return "PASS"


def build_report_payload(
    *,
    issues: list[dict[str, Any]],
    missing_sources: list[dict[str, Any]],
    layer_stats: dict[str, int],
    diff: dict[str, Any],
    manifest_path: str,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation_status = overall_status(issues, [] if gate else missing_sources)
    status = gate["status"] if gate else validation_status
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    top = sorted(issues, key=lambda x: (0 if x["severity"] == "error" else 1, x["layer"], x.get("row", 0)))[:20]
    blocks = {
        "prices_pv": [i for i in issues if i["layer"] == "products_prices" or "price" in i.get("check", "")],
        "links": [i for i in issues if i["layer"] in {"resource_links", "certificates"} or "url" in i.get("check", "")],
        "aliases": [i for i in issues if i["layer"] == "product_aliases" or "alias" in i.get("check", "")],
        "safety": [i for i in issues if i.get("check") in {"medical_language", "suspicious_artifact"} or "safety" in i.get("check", "")],
    }
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest_path,
        "status": status,
        "validation_status": validation_status,
        "layer_stats": layer_stats,
        "missing_sources": missing_sources,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "top_issues": top,
        "blocks": blocks,
        "diff": diff,
    }
    if gate:
        payload["mode"] = gate["mode"]
        payload["required_layers"] = gate["required_layers"]
        payload["found_layers"] = gate["found_layers"]
        payload["missing_layers"] = gate["missing_layers"]
        payload["missing_required_layers"] = gate["missing_required_layers"]
        payload["can_sync"] = "yes" if gate["can_sync"] else "no"
    return payload


def write_reports(payload: dict[str, Any], md_path: Path, json_path: Path) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")


def _render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# WHIEDA Data Quality Report",
        "",
        f"**Generated:** {payload['generated_at']}",
        f"**Manifest:** `{payload['manifest']}`",
        f"**Status:** **{payload['status']}**",
        "",
    ]
    if payload.get("mode"):
        lines.extend(
            [
                "## Sync gate",
                "",
                f"- **Mode:** `{payload['mode']}`",
                f"- **Required layers:** {', '.join(f'`{l}`' for l in payload.get('required_layers') or []) or 'none'}",
                f"- **Found layers:** {', '.join(f'`{l}`' for l in payload.get('found_layers') or []) or 'none'}",
                f"- **Missing layers:** {', '.join(f'`{l}`' for l in payload.get('missing_layers') or []) or 'none'}",
                f"- **Can sync:** **{payload.get('can_sync', 'unknown')}**",
                "",
            ]
        )
    lines.extend(
        [
        "## Row counts by layer",
        "",
        "| Layer | Rows |",
        "|---|---:|",
        ]
    )
    for layer, count in sorted((payload.get("layer_stats") or {}).items()):
        lines.append(f"| `{layer}` | {count} |")

    missing = payload.get("missing_sources") or []
    lines.extend(["", "## Missing sources", ""])
    if missing:
        for item in missing:
            lines.append(f"- **SOURCE MISSING** `{item['layer']}` — expected `{item['expected_path']}`")
    else:
        lines.append("- None (all manifest sources present)")

    lines.extend(["", "## Blocking errors", ""])
    errors = payload.get("errors") or []
    if errors:
        for item in errors[:50]:
            lines.append(
                f"- `{item['layer']}` row {item.get('row', '?')} field `{item.get('field', '')}`: {item['message']} (`{item['file']}`)"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings") or []
    if warnings:
        for item in warnings[:30]:
            lines.append(f"- `{item['layer']}` row {item.get('row', '?')}: {item['message']}")
    else:
        lines.append("- None")

    diff = payload.get("diff") or {}
    lines.extend(["", "## Baseline diff", ""])
    if diff.get("status") == "no_baseline":
        lines.append("- No baseline saved yet (`--baseline` first)")
    else:
        for layer, info in (diff.get("layers") or {}).items():
            lines.append(
                f"- `{layer}`: +{len(info.get('added', []))} / -{len(info.get('removed', []))} / ~{len(info.get('changed', []))} changed"
            )
            if info.get("price_changed"):
                lines.append(f"  - price changed: {', '.join(info['price_changed'][:10])}")
            if info.get("safety_changed"):
                lines.append(f"  - safety/limit changed: {', '.join(info['safety_changed'][:10])}")

    lines.extend(["", "## Top 20 issues", ""])
    for item in payload.get("top_issues") or []:
        lines.append(
            f"- [{item['severity'].upper()}] `{item['check']}` {item['layer']} row {item.get('row')} `{item.get('field')}` — {item['message']}"
        )

    for title, key in [
        ("Prices / PV", "prices_pv"),
        ("Links / certificates", "links"),
        ("Aliases", "aliases"),
        ("Safety", "safety"),
    ]:
        block = (payload.get("blocks") or {}).get(key) or []
        lines.extend(["", f"## {title}", ""])
        if block:
            for item in block[:15]:
                lines.append(f"- {item['message']} ({item['layer']})")
        else:
            lines.append("- None")

    return "\n".join(lines) + "\n"
