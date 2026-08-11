"""WHIEDA master snapshot integrity helpers (read-only, local)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"

EXPECTED_LAYERS: dict[str, str] = {
    "products": "1035748906",
    "aliases": "2001001",
    "resources": "2001005",
    "product_cards": "2001006",
    "product_details": "2001008",
    "product_comparisons": "1415928637",
    "users_access": "161104189",
    "structure_owners": "86214234",
    "business_objections": "43717728",
    "business_faq": "28151102",
    "promotions": "1621722235",
    "recommendation_rules": "1468974041",
    "starter_basket_templates": "1974396045",
    "events": "1444298798",
    "community_resources": "947236678",
    "intent_registry": "2001020",
    "clarification_prompts": "2001021",
    "capability_responses": "2001022",
    "canonical_questions": "1160261466",
    "partners_ref": "1733124410",
}

CRITICAL_FLOORS: dict[str, int] = {
    "products": 20,
    "aliases": 60,
    "resources": 25,
    "product_cards": 10,
}

OPTIONAL_EMPTY_DATA_LAYERS = frozenset({"product_details"})

LAYER_ID_FIELDS: dict[str, list[str]] = {
    "products": ["sku"],
    "aliases": ["alias"],
    "resources": ["resource_id"],
    "product_cards": ["sku"],
    "product_details": ["detail_id"],
    "product_comparisons": ["comparison_id"],
    "users_access": ["telegram_user_id"],
    "structure_owners": ["structure_code"],
    "business_objections": ["objection_id"],
    "business_faq": ["faq_id"],
    "promotions": ["promotion_id"],
    "recommendation_rules": ["product_id"],
    "starter_basket_templates": ["template_id"],
    "events": ["event_id"],
    "community_resources": ["resource_id"],
    "intent_registry": ["intent_id"],
    "clarification_prompts": ["clarification_key"],
    "capability_responses": ["response_id"],
    "canonical_questions": ["question_id"],
    "partners_ref": ["partner_id"],
}

RUNTIME_TABLES: dict[str, tuple[str, list[str]]] = {
    "products": ("advisor_structured_products", ["sku", "canonical_name"]),
    "aliases": ("advisor_structured_aliases", ["alias", "canonical_sku"]),
    "resources": ("advisor_structured_resources", ["resource_id", "title"]),
    "product_cards": ("advisor_structured_product_cards", ["sku", "canonical_name"]),
    "product_details": ("advisor_structured_product_details", ["detail_id", "sku"]),
    "product_comparisons": ("advisor_structured_product_comparisons", ["comparison_id", "left_sku", "right_sku"]),
    "users_access": ("advisor_structured_users_access", ["telegram_user_id"]),
    "structure_owners": ("advisor_structured_structure_owners", ["structure_code"]),
    "business_objections": ("advisor_structured_business_objections", ["objection_id"]),
    "business_faq": ("advisor_structured_business_faq", ["faq_id"]),
    "promotions": ("advisor_promotions", ["promotion_id", "title"]),
    "recommendation_rules": ("advisor_product_recommendation_rules", ["product_id"]),
    "starter_basket_templates": ("advisor_starter_basket_templates", ["template_id"]),
    "events": ("advisor_whieda_events", ["event_id", "title"]),
    "community_resources": ("advisor_whieda_community_resources", ["resource_id", "title"]),
    "intent_registry": ("advisor_structured_intent_registry", ["intent_id"]),
    "clarification_prompts": ("advisor_structured_clarification_prompts", ["clarification_key"]),
    "capability_responses": ("advisor_structured_capability_responses", ["response_id"]),
    "canonical_questions": ("advisor_structured_canonical_questions", ["question_id"]),
}

PROJECT_ID = "whieda"

TITLE_NORMALIZED_LAYERS = frozenset({"products", "product_cards", "resources"})
TITLE_LAYER_CONFIG: dict[str, tuple[str, str]] = {
    "products": ("sku", "canonical_name"),
    "product_cards": ("sku", "canonical_name"),
    "resources": ("resource_id", "title"),
}
ALIAS_BUSINESS_KEY_FIELDS = ("alias", "canonical_sku")
DUPLICATE_EXAMPLES_CAP = 10

DISPLAY_QUOTE_CHARS = re.compile(r'["""\'\'«»]+')

DSN_REDACT = re.compile(
    r"(postgresql(?:\+[\w]+)?://)[^\s@]+@|password[=:\s]\S+|token[=:\s]\S+",
    re.IGNORECASE,
)


def normalize_display_title(text: str) -> str:
    cleaned = DISPLAY_QUOTE_CHARS.sub("", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def alias_business_key(row: Mapping[str, str]) -> tuple[str, str]:
    return (str(row.get("alias", "") or "").strip(), str(row.get("canonical_sku", "") or "").strip())


def alias_business_key_token(key: tuple[str, str]) -> str:
    return f"{key[0]}\x1f{key[1]}"


def alias_business_key_from_token(token: str) -> tuple[str, str]:
    alias, _, sku = str(token).partition("\x1f")
    return alias, sku


def collect_alias_duplicate_stats(rows: list[dict[str, str]]) -> dict[str, Any]:
    from collections import Counter

    keys = [alias_business_key(row) for row in rows if alias_business_key(row)[0]]
    counter = Counter(keys)
    duplicate_keys = sorted(key for key, count in counter.items() if count > 1)
    duplicate_rows = sum(count - 1 for count in counter.values() if count > 1)
    examples = [alias_business_key_token(key) for key in duplicate_keys[:DUPLICATE_EXAMPLES_CAP]]
    return {
        "duplicate_rows_in_master": duplicate_rows,
        "duplicate_examples": examples,
        "duplicate_key_count": len(duplicate_keys),
    }


def normalized_title_map(rows: list[dict[str, str]], *, id_field: str, title_field: str) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for row in rows:
        business_id = str(row.get(id_field, "") or "").strip()
        if not business_id:
            continue
        mapped[business_id] = normalize_display_title(str(row.get(title_field, "") or ""))
    return mapped


def normalized_title_content_hash(title_map: Mapping[str, str]) -> str:
    parts = [f"{business_id}|{title_map[business_id]}" for business_id in sorted(title_map)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def redact_sensitive_text(text: str) -> str:
    redacted = DSN_REDACT.sub(r"\1[REDACTED]@", text)
    redacted = re.sub(r"(?i)password[=:\s]\S+", "password=[REDACTED]", redacted)
    redacted = re.sub(r"(?i)token[=:\s]\S+", "token=[REDACTED]", redacted)
    return redacted


def parse_tsv_payload(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = payload.decode("utf-8-sig", errors="replace").replace("\r", "")
    if not text.strip():
        raise ValueError("empty TSV payload")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not reader.fieldnames:
        raise ValueError("missing TSV header row")
    headers = [str(h or "").strip() for h in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for raw in reader:
        if not any(str(v or "").strip() for v in raw.values()):
            continue
        row = {headers[i]: str(raw.get(headers[i], "") or "").strip() for i in range(len(headers))}
        rows.append(row)
    return headers, rows


def header_hash(headers: Iterable[str]) -> str:
    normalized = "\t".join(h.strip().lower() for h in headers)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def content_hash_from_rows(rows: list[dict[str, str]], fields: list[str]) -> str:
    parts: list[str] = []
    for row in sorted(rows, key=lambda item: tuple(item.get(field, "") for field in fields)):
        parts.append("|".join(row.get(field, "") for field in fields))
    body = "\n".join(parts)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def extract_ids(rows: list[dict[str, str]], layer: str) -> set[str]:
    id_fields = LAYER_ID_FIELDS.get(layer, [])
    if not id_fields:
        return set()
    primary = id_fields[0]
    return {row.get(primary, "").strip() for row in rows if row.get(primary, "").strip()}


def analyze_layer_file(layer: str, path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    if not payload.strip():
        raise ValueError(f"{layer}: file is empty")
    headers, rows = parse_tsv_payload(payload)
    return {
        "gid": EXPECTED_LAYERS[layer],
        "file": path.name,
        "bytes": len(payload),
        "rows": len(rows),
        "sha256": sha256_bytes(payload),
        "header_hash": header_hash(headers),
        "headers": headers,
        "content_hash": content_hash_from_rows(rows, LAYER_ID_FIELDS.get(layer, headers[:1])),
        "ids": extract_ids(rows, layer),
    }


def load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing manifest.json in {snapshot_dir}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def validate_snapshot_dir(snapshot_dir: Path, *, strict_files: bool = True) -> dict[str, Any]:
    manifest = load_manifest(snapshot_dir)
    layers_meta = manifest.get("layers") or {}
    issues: list[str] = []
    details: dict[str, Any] = {}

    for layer, expected_gid in EXPECTED_LAYERS.items():
        meta = layers_meta.get(layer)
        if not meta:
            issues.append(f"{layer}: missing from manifest")
            details[layer] = {"status": "blocking", "reason": "missing_manifest_entry"}
            continue
        file_name = meta.get("file") or f"{layer}.tsv"
        path = snapshot_dir / file_name
        if not path.is_file():
            issues.append(f"{layer}: missing file {file_name}")
            details[layer] = {"status": "blocking", "reason": "missing_file"}
            continue
        try:
            analyzed = analyze_layer_file(layer, path)
        except ValueError as exc:
            issues.append(f"{layer}: invalid TSV ({exc})")
            details[layer] = {"status": "blocking", "reason": str(exc)}
            continue

        if strict_files and analyzed["bytes"] == 0:
            issues.append(f"{layer}: empty file")
            details[layer] = {"status": "blocking", "reason": "empty_file"}
            continue

        if analyzed["rows"] == 0 and layer not in OPTIONAL_EMPTY_DATA_LAYERS:
            if strict_files and len(analyzed["headers"]) == 0:
                issues.append(f"{layer}: invalid header")
                details[layer] = {"status": "blocking", "reason": "invalid_header"}
                continue

        if layer in CRITICAL_FLOORS and analyzed["rows"] < CRITICAL_FLOORS[layer]:
            issues.append(
                f"{layer}: row count {analyzed['rows']} below floor {CRITICAL_FLOORS[layer]}"
            )
            details[layer] = {
                "status": "blocking",
                "reason": "below_critical_floor",
                "rows": analyzed["rows"],
            }
            continue

        if analyzed["rows"] == 0 and layer in OPTIONAL_EMPTY_DATA_LAYERS:
            if not analyzed["headers"]:
                issues.append(f"{layer}: header required for optional empty layer")
                details[layer] = {"status": "blocking", "reason": "missing_header_optional_layer"}
                continue

        details[layer] = {"status": "ok", **{k: analyzed[k] for k in ("rows", "sha256", "header_hash", "bytes", "gid") if k in analyzed}}
        if str(meta.get("gid")) != expected_gid:
            issues.append(f"{layer}: gid mismatch manifest={meta.get('gid')} expected={expected_gid}")

    valid = not issues and len(details) == len(EXPECTED_LAYERS)
    return {
        "snapshot": str(snapshot_dir),
        "valid": valid,
        "issues": issues,
        "layers": details,
        "captured_at": manifest.get("captured_at"),
    }


@dataclass
class LayerDrift:
    layer: str
    classification: str
    row_delta: int | None = None
    hash_changed: bool = False
    header_changed: bool = False
    ids_added: list[str] = field(default_factory=list)
    ids_removed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _worst_classification(current: str, new: str) -> str:
    order = {"expected_content_change": 0, "review_required": 1, "blocking": 2}
    return current if order[current] >= order[new] else new


def compare_layer(
    layer: str,
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
    *,
    left_ids: set[str] | None = None,
    right_ids: set[str] | None = None,
) -> LayerDrift:
    drift = LayerDrift(layer=layer, classification="expected_content_change")

    if left is None or right is None:
        drift.classification = "blocking"
        drift.reasons.append("missing_layer")
        return drift

    left_rows = int(left.get("rows", 0))
    right_rows = int(right.get("rows", 0))
    drift.row_delta = right_rows - left_rows

    if left.get("header_hash") != right.get("header_hash"):
        drift.header_changed = True
        drift.classification = "review_required"
        drift.reasons.append("header_changed")

    if left.get("sha256") != right.get("sha256"):
        drift.hash_changed = True
        if drift.classification == "expected_content_change":
            drift.reasons.append("content_changed")

    if left_rows > 0:
        drop_ratio = (left_rows - right_rows) / left_rows
        if drop_ratio > 0.30:
            drift.classification = _worst_classification(drift.classification, "review_required")
            drift.reasons.append("row_count_drop_over_30_percent")

    if layer in CRITICAL_FLOORS:
        if right_rows == 0 and left_rows > 0:
            drift.classification = _worst_classification(drift.classification, "review_required")
            drift.reasons.append("critical_layer_lost")
        if right_rows < CRITICAL_FLOORS[layer]:
            drift.classification = "blocking"
            drift.reasons.append("below_critical_floor")

    if left_ids is not None and right_ids is not None:
        drift.ids_added = sorted(right_ids - left_ids)
        drift.ids_removed = sorted(left_ids - right_ids)
        if drift.ids_added or drift.ids_removed:
            if drift.classification == "expected_content_change" and not drift.hash_changed:
                drift.reasons.append("id_set_changed")

    if (
        drift.classification == "expected_content_change"
        and (drift.hash_changed or drift.row_delta)
        and not drift.header_changed
    ):
        drift.reasons.append("expected_content_change")

    return drift


def compare_snapshots(left_dir: Path, right_dir: Path) -> dict[str, Any]:
    left_validation = validate_snapshot_dir(left_dir, strict_files=False)
    right_validation = validate_snapshot_dir(right_dir, strict_files=False)
    left_manifest = load_manifest(left_dir)
    right_manifest = load_manifest(right_dir)

    layer_reports: dict[str, Any] = {}
    overall = "expected_content_change"

    for layer in EXPECTED_LAYERS:
        left_meta = left_manifest.get("layers", {}).get(layer)
        right_meta = right_manifest.get("layers", {}).get(layer)
        left_path = left_dir / (left_meta or {}).get("file", f"{layer}.tsv")
        right_path = right_dir / (right_meta or {}).get("file", f"{layer}.tsv")

        left_analysis = None
        right_analysis = None
        left_ids: set[str] | None = None
        right_ids: set[str] | None = None

        if left_path.is_file():
            try:
                left_analysis = analyze_layer_file(layer, left_path)
                left_ids = left_analysis.pop("ids")
                left_analysis.pop("headers", None)
            except ValueError:
                left_analysis = None
        if right_path.is_file():
            try:
                right_analysis = analyze_layer_file(layer, right_path)
                right_ids = right_analysis.pop("ids")
                right_analysis.pop("headers", None)
            except ValueError:
                right_analysis = None

        if left_analysis is None or right_analysis is None:
            drift = LayerDrift(layer=layer, classification="blocking", reasons=["missing_or_invalid_layer"])
        else:
            drift = compare_layer(layer, left_analysis, right_analysis, left_ids=left_ids, right_ids=right_ids)

        overall = _worst_classification(overall, drift.classification)
        layer_reports[layer] = {
            "classification": drift.classification,
            "row_delta": drift.row_delta,
            "hash_changed": drift.hash_changed,
            "header_changed": drift.header_changed,
            "ids_added": drift.ids_added[:20],
            "ids_removed": drift.ids_removed[:20],
            "ids_added_count": len(drift.ids_added),
            "ids_removed_count": len(drift.ids_removed),
            "reasons": drift.reasons,
            "left_rows": (left_analysis or {}).get("rows"),
            "right_rows": (right_analysis or {}).get("rows"),
        }

    return {
        "left": str(left_dir),
        "right": str(right_dir),
        "overall_classification": overall,
        "left_valid": left_validation["valid"],
        "right_valid": right_validation["valid"],
        "layers": layer_reports,
    }


def retention_plan(snapshot_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    snapshots: list[tuple[Path, datetime]] = []
    for path in sorted(snapshot_root.glob("*/manifest.json")):
        if ".partial" in path.parent.name:
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            captured = datetime.fromisoformat(str(manifest.get("captured_at")))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
        except Exception:
            captured = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        snapshots.append((path.parent, captured))

    keep: set[Path] = set()
    for directory, captured in snapshots:
        age_days = (now - captured).days
        if age_days <= 30:
            keep.add(directory)

    by_month: dict[str, tuple[Path, datetime]] = {}
    for directory, captured in snapshots:
        if (now - captured).days <= 30:
            continue
        month_key = captured.strftime("%Y-%m")
        current = by_month.get(month_key)
        if current is None or captured > current[1]:
            by_month[month_key] = (directory, captured)

    keep.update(path for path, _ in by_month.values())

    remove = [str(path) for path, _ in snapshots if path not in keep]
    return {
        "snapshot_root": str(snapshot_root),
        "total_snapshots": len(snapshots),
        "keep_count": len(keep),
        "would_remove": remove,
        "monthly_kept": sorted(str(path) for path, _ in by_month.values()),
    }


def apply_retention(snapshot_root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    plan = retention_plan(snapshot_root, now=now)
    removed: list[str] = []
    for item in plan["would_remove"]:
        path = Path(item)
        if path.exists():
            shutil.rmtree(path)
            removed.append(item)
    plan["removed"] = removed
    return plan


def runtime_hash_sql(table: str, fields: list[str]) -> str:
    cols = " || '|' || ".join(f"coalesce({field}::text, '')" for field in fields)
    return f"""
SELECT count(*)::bigint AS row_count,
       coalesce(md5(string_agg({cols}, E'\\n' ORDER BY {fields[0]})), md5('')) AS content_hash
FROM {table}
WHERE client_id = '{PROJECT_ID}';
""".strip()


def master_layer_stats(snapshot_dir: Path, layer: str) -> dict[str, Any]:
    manifest = load_manifest(snapshot_dir)
    meta = manifest["layers"][layer]
    path = snapshot_dir / meta["file"]
    analyzed = analyze_layer_file(layer, path)
    analyzed.pop("ids")
    analyzed.pop("headers")
    return analyzed


def compare_master_to_runtime(snapshot_dir: Path, runtime_rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    overall = "in_sync"
    layers: dict[str, Any] = {}

    for layer in EXPECTED_LAYERS:
        if layer == "partners_ref":
            layers[layer] = {"status": "not_checked", "reason": "runtime_uses_lead_tables_not_structured"}
            overall = _parity_worst(overall, "not_checked")
            continue
        if layer not in RUNTIME_TABLES:
            layers[layer] = {"status": "not_checked", "reason": "no_runtime_mapping"}
            continue
        try:
            master = master_layer_stats(snapshot_dir, layer)
        except Exception as exc:
            layers[layer] = {"status": "master_review_required", "reason": redact_sensitive_text(str(exc))}
            overall = _parity_worst(overall, "master_review_required")
            continue

        runtime = runtime_rows.get(layer)
        if not runtime:
            layers[layer] = {"status": "not_checked", "reason": "runtime_layer_not_queried"}
            overall = _parity_worst(overall, "not_checked")
            continue

        master_rows = int(master["rows"])
        runtime_count = int(runtime.get("row_count", 0))
        status = "in_sync"
        reasons: list[str] = []

        if runtime_count == 0 and master_rows > 0:
            status = "runtime_missing_layer"
            reasons.append("runtime_empty")
        elif runtime_count != master_rows:
            status = "stale_runtime"
            reasons.append("row_count_mismatch")
        elif runtime.get("content_hash") and master.get("content_hash"):
            if runtime["content_hash"] != master["content_hash"]:
                status = "stale_runtime"
                reasons.append("content_hash_mismatch")

        if layer in CRITICAL_FLOORS and runtime_count < CRITICAL_FLOORS[layer]:
            status = "master_review_required"
            reasons.append("runtime_below_critical_floor")

        overall = _parity_worst(overall, status)
        layers[layer] = {
            "status": status,
            "master_rows": master_rows,
            "runtime_rows": runtime_count,
            "master_content_hash": master.get("content_hash"),
            "runtime_content_hash": runtime.get("content_hash"),
            "reasons": reasons,
        }

    return {"snapshot": str(snapshot_dir), "overall_status": overall, "layers": layers}


def _parity_worst(current: str, new: str) -> str:
    order = {
        "in_sync": 0,
        "not_checked": 1,
        "stale_runtime": 2,
        "runtime_missing_layer": 3,
        "master_review_required": 4,
    }
    return current if order.get(current, 0) >= order.get(new, 0) else new


ID_LIST_CAP = 25

RUNTIME_LAYER_STATUSES = frozenset(
    {
        "in_sync",
        "runtime_stale",
        "runtime_extra",
        "schema_mismatch",
        "runtime_missing",
        "master_review_required",
        "not_runtime_backed",
    }
)

OVERALL_RUNTIME_STATUSES = frozenset({"safe", "review_required", "blocking"})


def cap_id_list(ids: Iterable[str], *, cap: int = ID_LIST_CAP) -> dict[str, Any]:
    ordered = sorted({str(item).strip() for item in ids if str(item).strip()})
    return {
        "values": ordered[:cap],
        "total": len(ordered),
        "truncated": len(ordered) > cap,
    }


def iter_snapshot_dirs(snapshot_root: Path) -> list[Path]:
    directories: list[Path] = []
    if not snapshot_root.is_dir():
        return directories
    for manifest_path in snapshot_root.glob("*/manifest.json"):
        if ".partial" in manifest_path.parent.name:
            continue
        directories.append(manifest_path.parent)
    return sorted(directories, key=lambda path: path.name, reverse=True)


def find_newest_valid_snapshot(snapshot_root: Path) -> tuple[Path, dict[str, Any]]:
    candidates = iter_snapshot_dirs(snapshot_root)
    if not candidates:
        raise FileNotFoundError(f"no snapshots under {snapshot_root}")

    skipped: list[dict[str, Any]] = []
    for directory in candidates:
        validation = validate_snapshot_dir(directory)
        if validation["valid"]:
            validation["selected_reason"] = (
                "newest_valid" if not skipped else "fallback_after_invalid_newer"
            )
            validation["skipped_newer_invalid"] = skipped
            return directory, validation
        skipped.append(
            {
                "snapshot": str(directory),
                "issues": validation.get("issues", []),
            }
        )

    raise ValueError(
        f"no valid snapshot under {snapshot_root}; checked {len(candidates)} candidate(s)"
    )


def detect_tenant_discriminator(columns: Iterable[str]) -> str | None:
    normalized = {str(column).strip().lower() for column in columns}
    if "client_id" in normalized:
        return "client_id"
    if "project_id" in normalized:
        return "project_id"
    return None


def master_runtime_layer_stats(snapshot_dir: Path, layer: str) -> dict[str, Any]:
    if layer not in RUNTIME_TABLES:
        raise ValueError(f"{layer}: no runtime mapping")
    _, fields = RUNTIME_TABLES[layer]
    manifest = load_manifest(snapshot_dir)
    meta = manifest["layers"][layer]
    path = snapshot_dir / meta["file"]
    headers, rows = parse_tsv_payload(path.read_bytes())
    header_set = {header.strip().lower() for header in headers}
    missing = [field for field in fields if field.lower() not in header_set]
    if missing:
        raise ValueError(f"{layer}: master TSV missing runtime fields {missing}")
    ids = extract_ids(rows, layer)
    stats: dict[str, Any] = {
        "rows": len(rows),
        "content_hash": content_hash_from_rows(rows, fields),
        "ids": ids,
        "fields": fields,
    }
    if layer == "aliases":
        business_keys = {alias_business_key(row) for row in rows if alias_business_key(row)[0]}
        duplicate_stats = collect_alias_duplicate_stats(rows)
        stats.update(
            {
                "master_raw_rows": len(rows),
                "master_unique_rows": len(business_keys),
                "business_keys": business_keys,
                **duplicate_stats,
            }
        )
    if layer in TITLE_NORMALIZED_LAYERS:
        id_field, title_field = TITLE_LAYER_CONFIG[layer]
        title_map = normalized_title_map(rows, id_field=id_field, title_field=title_field)
        stats["normalized_title_map"] = title_map
        stats["normalized_content_hash"] = normalized_title_content_hash(title_map)
    return stats


def _compare_aliases_layer(master: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    master_raw = int(master.get("master_raw_rows", master.get("rows", 0)))
    master_unique = int(master.get("master_unique_rows", len(master.get("business_keys") or [])))
    runtime_raw = int(runtime.get("row_count", 0))
    runtime_unique = int(runtime.get("unique_business_row_count", runtime_raw))
    master_keys = set(master.get("business_keys") or [])
    runtime_keys = set(runtime.get("business_keys") or [])
    removed = cap_id_list(alias_business_key_token(key) for key in master_keys - runtime_keys)
    added = cap_id_list(alias_business_key_token(key) for key in runtime_keys - master_keys)
    duplicate_rows = int(master.get("duplicate_rows_in_master", 0))
    duplicate_examples = list(master.get("duplicate_examples") or [])[:DUPLICATE_EXAMPLES_CAP]

    reasons: list[str] = []
    if removed["total"]:
        reasons.append("true_runtime_missing_ids")
    if added["total"]:
        reasons.append("true_runtime_extra_ids")
    if duplicate_rows:
        reasons.append("master_duplicate_rows")

    if removed["total"]:
        status = "runtime_stale"
    elif added["total"]:
        status = "runtime_extra"
    elif master_unique == runtime_unique == len(master_keys) == len(runtime_keys) and master_keys == runtime_keys:
        status = "in_sync"
        if master_raw != runtime_raw or duplicate_rows:
            if duplicate_rows and master_raw > master_unique:
                pass
            elif master_raw != runtime_raw and not duplicate_rows:
                reasons.append("raw_row_count_presentation_only")
    else:
        status = "runtime_stale"
        reasons.append("business_key_mismatch")

    if (
        status == "in_sync"
        and master.get("content_hash") != runtime.get("content_hash")
        and not removed["total"]
        and not added["total"]
    ):
        reasons.append("equivalent_after_normalization")

    return {
        "status": status,
        "master_rows": master_raw,
        "runtime_rows": runtime_raw,
        "master_raw_rows": master_raw,
        "master_unique_rows": master_unique,
        "runtime_unique_rows": runtime_unique,
        "duplicate_rows_in_master": duplicate_rows,
        "duplicate_examples": duplicate_examples,
        "master_content_hash": master.get("content_hash"),
        "runtime_content_hash": runtime.get("content_hash"),
        "ids_removed": removed["values"],
        "ids_removed_total": removed["total"],
        "ids_added": added["values"],
        "ids_added_total": added["total"],
        "reasons": reasons,
        "tenant_discriminator": runtime.get("tenant_discriminator"),
    }


def _compare_title_normalized_layer(layer: str, master: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    id_field, _title_field = TITLE_LAYER_CONFIG[layer]
    master_map = dict(master.get("normalized_title_map") or {})
    runtime_map = dict(runtime.get("normalized_title_map") or {})
    master_rows = int(master.get("rows", 0))
    runtime_rows = int(runtime.get("row_count", 0))
    master_ids = set(master_map)
    runtime_ids = set(runtime_map)
    removed = cap_id_list(master_ids - runtime_ids)
    added = cap_id_list(runtime_ids - master_ids)
    mismatched = sorted(business_id for business_id in master_ids & runtime_ids if master_map[business_id] != runtime_map[business_id])
    mismatch = cap_id_list(mismatched)

    reasons: list[str] = []
    status = "in_sync"

    if master_rows > 0 and runtime_rows == 0:
        return {
            "status": "runtime_missing",
            "master_rows": master_rows,
            "runtime_rows": 0,
            "ids_removed": removed["values"],
            "ids_removed_total": removed["total"],
            "ids_added": added["values"],
            "ids_added_total": added["total"],
            "reasons": ["runtime_empty_master_nonempty"],
            "tenant_discriminator": runtime.get("tenant_discriminator"),
        }

    if removed["total"]:
        status = "runtime_stale"
        reasons.append("true_runtime_missing_ids")
    elif added["total"]:
        status = "runtime_extra"
        reasons.append("true_runtime_extra_ids")
    elif mismatch["total"]:
        status = "runtime_stale"
        reasons.append("true_content_mismatch_after_normalization")
    elif master.get("content_hash") != runtime.get("content_hash"):
        reasons.append("equivalent_after_normalization")

    if layer in CRITICAL_FLOORS and master_rows >= CRITICAL_FLOORS[layer] and runtime_rows < CRITICAL_FLOORS[layer]:
        return {
            "status": "master_review_required",
            "master_rows": master_rows,
            "runtime_rows": runtime_rows,
            "master_content_hash": master.get("content_hash"),
            "runtime_content_hash": runtime.get("content_hash"),
            "normalized_master_hash": master.get("normalized_content_hash"),
            "normalized_runtime_hash": runtime.get("normalized_content_hash"),
            "ids_removed": removed["values"],
            "ids_removed_total": removed["total"],
            "ids_added": added["values"],
            "ids_added_total": added["total"],
            "content_mismatch_ids": mismatch["values"],
            "content_mismatch_total": mismatch["total"],
            "reasons": reasons + ["runtime_below_critical_floor"],
            "tenant_discriminator": runtime.get("tenant_discriminator"),
        }

    return {
        "status": status,
        "master_rows": master_rows,
        "runtime_rows": runtime_rows,
        "master_content_hash": master.get("content_hash"),
        "runtime_content_hash": runtime.get("content_hash"),
        "normalized_master_hash": master.get("normalized_content_hash"),
        "normalized_runtime_hash": runtime.get("normalized_content_hash"),
        "ids_removed": removed["values"],
        "ids_removed_total": removed["total"],
        "ids_added": added["values"],
        "ids_added_total": added["total"],
        "content_mismatch_ids": mismatch["values"],
        "content_mismatch_total": mismatch["total"],
        "reasons": reasons,
        "tenant_discriminator": runtime.get("tenant_discriminator"),
    }


def compare_layer_runtime_v2(
    layer: str,
    master: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    if layer == "partners_ref":
        return {
            "status": "not_runtime_backed",
            "reason": "legacy_partner_path_only_no_structured_runtime_table",
            "master_rows": (master or {}).get("rows"),
            "runtime_rows": None,
        }

    if master is None:
        return {
            "status": "master_review_required",
            "reasons": ["master_layer_unavailable"],
        }

    if runtime is None:
        return {
            "status": "runtime_missing",
            "reasons": ["runtime_layer_not_queried"],
            "master_rows": master.get("rows"),
            "runtime_rows": 0,
        }

    if runtime.get("status") == "schema_mismatch":
        return {
            "status": "schema_mismatch",
            "reasons": runtime.get("reasons", ["schema_mismatch"]),
            "master_rows": master.get("rows"),
            "runtime_rows": runtime.get("row_count"),
            "tenant_discriminator": runtime.get("tenant_discriminator"),
        }

    if runtime.get("status") == "runtime_missing":
        return {
            "status": "runtime_missing",
            "reasons": runtime.get("reasons", ["runtime_table_missing"]),
            "master_rows": master.get("rows"),
            "runtime_rows": 0,
        }

    if layer == "aliases":
        return _compare_aliases_layer(master, runtime)
    if layer in TITLE_NORMALIZED_LAYERS:
        return _compare_title_normalized_layer(layer, master, runtime)

    master_rows = int(master.get("rows", 0))
    runtime_rows = int(runtime.get("row_count", 0))
    master_ids = set(master.get("ids") or [])
    runtime_ids = set(runtime.get("ids") or [])
    ids_removed = master_ids - runtime_ids
    ids_added = runtime_ids - master_ids
    removed = cap_id_list(ids_removed)
    added = cap_id_list(ids_added)

    reasons: list[str] = []
    status = "in_sync"

    if master_rows == 0 and runtime_rows == 0 and layer in OPTIONAL_EMPTY_DATA_LAYERS:
        return {
            "status": "in_sync",
            "master_rows": 0,
            "runtime_rows": 0,
            "master_content_hash": master.get("content_hash"),
            "runtime_content_hash": runtime.get("content_hash"),
            "ids_removed": removed["values"],
            "ids_removed_total": removed["total"],
            "ids_added": added["values"],
            "ids_added_total": added["total"],
            "reasons": ["optional_empty_layer"],
            "tenant_discriminator": runtime.get("tenant_discriminator"),
        }

    if master_rows > 0 and runtime_rows == 0:
        return {
            "status": "runtime_missing",
            "master_rows": master_rows,
            "runtime_rows": 0,
            "ids_removed": removed["values"],
            "ids_removed_total": removed["total"],
            "ids_added": added["values"],
            "ids_added_total": added["total"],
            "reasons": ["runtime_empty_master_nonempty"],
        }

    if removed["total"]:
        status = "runtime_stale"
        reasons.append("true_runtime_missing_ids")
    if added["total"]:
        status = "runtime_extra" if status == "in_sync" else "runtime_stale"
        reasons.append("true_runtime_extra_ids")
    if master_rows != runtime_rows and status == "in_sync":
        status = "runtime_stale" if runtime_rows < master_rows else "runtime_extra"
        reasons.append("row_count_mismatch")

    master_hash = master.get("content_hash")
    runtime_hash = runtime.get("content_hash")
    if master_hash and runtime_hash and master_hash != runtime_hash and status == "in_sync":
        status = "runtime_stale"
        reasons.append("true_content_mismatch_after_normalization")

    if layer in CRITICAL_FLOORS and master_rows >= CRITICAL_FLOORS[layer] and runtime_rows < CRITICAL_FLOORS[layer]:
        return {
            "status": "master_review_required",
            "master_rows": master_rows,
            "runtime_rows": runtime_rows,
            "master_content_hash": master_hash,
            "runtime_content_hash": runtime_hash,
            "ids_removed": removed["values"],
            "ids_removed_total": removed["total"],
            "ids_added": added["values"],
            "ids_added_total": added["total"],
            "reasons": reasons + ["runtime_below_critical_floor"],
            "tenant_discriminator": runtime.get("tenant_discriminator"),
        }

    return {
        "status": status,
        "master_rows": master_rows,
        "runtime_rows": runtime_rows,
        "master_content_hash": master_hash,
        "runtime_content_hash": runtime_hash,
        "ids_removed": removed["values"],
        "ids_removed_total": removed["total"],
        "ids_added": added["values"],
        "ids_added_total": added["total"],
        "reasons": reasons,
        "tenant_discriminator": runtime.get("tenant_discriminator"),
    }


def compute_runtime_overall_status(layer_reports: Mapping[str, Mapping[str, Any]]) -> str:
    worst = "safe"
    blocking = frozenset({"schema_mismatch", "runtime_missing", "master_review_required"})
    review = frozenset({"runtime_stale", "runtime_extra"})
    for report in layer_reports.values():
        layer_status = str(report.get("status", "master_review_required"))
        if layer_status in blocking:
            worst = "blocking"
        elif layer_status in review and worst != "blocking":
            worst = "review_required"
    return worst


def assert_connection_readonly(conn) -> dict[str, str]:
    conn.execute("BEGIN READ ONLY")
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        row = cur.fetchone()
        if not row or str(row[0]).lower() != "on":
            raise RuntimeError("connection is not read-only (transaction_read_only != on)")
    return {"transaction_read_only": "on", "begin_mode": "READ ONLY"}


def build_runtime_hash_sql(
    table: str,
    fields: list[str],
    *,
    tenant_column: str | None,
    tenant_value: str = PROJECT_ID,
    id_field: str,
) -> str:
    cols = " || '|' || ".join(f"coalesce({field}::text, '')" for field in fields)
    where = ""
    if tenant_column:
        where = f"WHERE {tenant_column} = '{tenant_value}'"
    return f"""
SELECT count(*)::bigint AS row_count,
       encode(
         sha256(convert_to(coalesce(string_agg({cols}, E'\\n' ORDER BY {fields[0]}), ''), 'UTF8')),
         'hex'
       ) AS content_hash,
       coalesce(array_agg(distinct {id_field}::text ORDER BY {id_field}::text), ARRAY[]::text[]) AS ids
FROM {table}
{where};
""".strip()


def build_runtime_alias_sql(
    table: str,
    *,
    tenant_column: str | None,
    tenant_value: str = PROJECT_ID,
) -> str:
    where = ""
    if tenant_column:
        where = f"WHERE {tenant_column} = '{tenant_value}'"
    return f"""
SELECT count(*)::bigint AS row_count,
       count(distinct (alias, canonical_sku))::bigint AS unique_business_row_count,
       coalesce(
         array_agg(
           distinct (alias || chr(31) || canonical_sku)
           ORDER BY alias || chr(31) || canonical_sku
         ),
         ARRAY[]::text[]
       ) AS business_keys
FROM {table}
{where};
""".strip()


def build_runtime_title_rows_sql(
    table: str,
    *,
    id_field: str,
    title_field: str,
    tenant_column: str | None,
    tenant_value: str = PROJECT_ID,
) -> str:
    where = ""
    if tenant_column:
        where = f"WHERE {tenant_column} = '{tenant_value}'"
    return f"""
SELECT {id_field}::text AS business_id, {title_field}::text AS display_title
FROM {table}
{where}
ORDER BY {id_field};
""".strip()


def runtime_title_map_from_rows(rows: Iterable[tuple[str, str]], *, id_field: str, title_field: str) -> dict[str, str]:
    del id_field, title_field
    mapped: dict[str, str] = {}
    for business_id, display_title in rows:
        token = str(business_id or "").strip()
        if not token:
            continue
        mapped[token] = normalize_display_title(str(display_title or ""))
    return mapped


def runtime_alias_keys_from_tokens(tokens: Iterable[str]) -> set[tuple[str, str]]:
    return {alias_business_key_from_token(token) for token in tokens if str(token).strip()}


def compare_master_runtime_v2(
    snapshot_dir: Path,
    runtime_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    manifest = load_manifest(snapshot_dir)
    layers: dict[str, Any] = {}

    for layer in EXPECTED_LAYERS:
        master: dict[str, Any] | None
        try:
            if layer == "partners_ref" or layer not in RUNTIME_TABLES:
                master = None
            else:
                master = master_runtime_layer_stats(snapshot_dir, layer)
        except Exception as exc:
            layers[layer] = {
                "status": "master_review_required",
                "reasons": [redact_sensitive_text(str(exc))],
            }
            continue

        runtime = runtime_rows.get(layer)
        layers[layer] = compare_layer_runtime_v2(layer, master, runtime)

    snapshot_hash = manifest.get("snapshot_sha256") or manifest.get("manifest_sha256")
    return {
        "snapshot": str(snapshot_dir),
        "snapshot_captured_at": manifest.get("captured_at"),
        "snapshot_id": snapshot_dir.name,
        "snapshot_hash": snapshot_hash,
        "layers": layers,
        "overall_status": compute_runtime_overall_status(layers),
    }


def format_runtime_integrity_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# WHIEDA master/runtime integrity",
        "",
        f"- Run: `{report.get('run_id', 'local')}`",
        f"- Snapshot: `{report.get('snapshot_id', '')}` ({report.get('snapshot_captured_at', '')})",
        f"- Overall: **{report.get('overall_status', 'unknown')}**",
        f"- Read-only: `{report.get('read_only_proof', {}).get('transaction_read_only', 'n/a')}`",
        "",
        "## Layers",
        "",
        "| Layer | Status | Master | Runtime |",
        "|---|---:|---:|---:|",
    ]
    for layer, details in (report.get("layers") or {}).items():
        lines.append(
            "| {layer} | {status} | {master} | {runtime} |".format(
                layer=layer,
                status=details.get("status", "?"),
                master=details.get("master_rows", "—"),
                runtime=details.get("runtime_rows", "—"),
            )
        )

    unbacked = [
        layer
        for layer, details in (report.get("layers") or {}).items()
        if details.get("status") == "not_runtime_backed"
    ]
    if unbacked:
        lines.extend(["", "## Not runtime-backed", "", ", ".join(unbacked)])

    sync = report.get("sync_freshness") or {}
    if sync:
        lines.extend(
            [
                "",
                "## Sync freshness",
                "",
                f"- status: `{sync.get('status', 'unknown')}`",
                f"- last_success_at: `{sync.get('last_success_at', '—')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def _snapshot_captured_at(snapshot_dir: Path) -> datetime:
    manifest_path = snapshot_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        captured = datetime.fromisoformat(str(manifest.get("captured_at")))
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        return captured
    except Exception:
        return datetime.fromtimestamp(manifest_path.stat().st_mtime, tz=timezone.utc)


def _directory_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def load_blocking_snapshot_ids(
    integrity_root: Path,
) -> dict[str, str]:
    blocking: dict[str, str] = {}
    if not integrity_root.is_dir():
        return blocking
    for json_path in integrity_root.glob("*/report.json"):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("overall_status") != "blocking":
            continue
        snapshot_id = payload.get("snapshot_id") or Path(str(payload.get("snapshot", ""))).name
        if snapshot_id:
            blocking[snapshot_id] = str(json_path.parent)
    return blocking


def retention_plan_v2(
    snapshot_root: Path,
    *,
    now: datetime | None = None,
    integrity_root: Path | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    integrity_root = integrity_root or snapshot_root.parent / "master-runtime-integrity"
    blocking_by_snapshot = load_blocking_snapshot_ids(integrity_root)

    entries: list[dict[str, Any]] = []
    for directory in iter_snapshot_dirs(snapshot_root):
        manifest_valid = True
        manifest_issue = ""
        try:
            validation = validate_snapshot_dir(directory)
            manifest_valid = validation["valid"]
            if not manifest_valid:
                manifest_issue = "; ".join(validation.get("issues", [])[:3])
        except Exception as exc:
            manifest_valid = False
            manifest_issue = redact_sensitive_text(str(exc))

        captured = _snapshot_captured_at(directory)
        age_days = (now - captured).days
        snapshot_id = directory.name
        blocking_report = blocking_by_snapshot.get(snapshot_id)
        manual_review = (not manifest_valid) or bool(blocking_report)
        reason = []
        if not manifest_valid:
            reason.append("invalid_manifest")
        if blocking_report:
            reason.append("blocking_integrity_report")

        entries.append(
            {
                "snapshot": str(directory),
                "snapshot_id": snapshot_id,
                "captured_at": captured.isoformat(),
                "age_days": age_days,
                "bytes": _directory_size_bytes(directory),
                "manifest_valid": manifest_valid,
                "manifest_issue": manifest_issue,
                "manual_review": manual_review,
                "manual_review_reasons": reason,
                "blocking_report": blocking_report,
            }
        )

    keep: set[str] = set()
    for entry in entries:
        if entry["manual_review"]:
            keep.add(entry["snapshot"])
            entry["retention_class"] = "manual_review"
            continue
        age = entry["age_days"]
        if age <= 14:
            keep.add(entry["snapshot"])
            entry["retention_class"] = "keep_all_14d"
            continue
        entry["retention_class"] = "candidate"

    by_day: dict[str, tuple[str, datetime]] = {}
    for entry in entries:
        if entry["snapshot"] in keep or entry["manual_review"]:
            continue
        age = entry["age_days"]
        if age <= 44:
            day_key = entry["captured_at"][:10]
            captured = datetime.fromisoformat(entry["captured_at"])
            current = by_day.get(day_key)
            if current is None or captured > current[1]:
                by_day[day_key] = (entry["snapshot"], captured)

    for snapshot, _ in by_day.values():
        keep.add(snapshot)
    for entry in entries:
        if entry.get("retention_class") == "candidate" and entry["snapshot"] in keep:
            entry["retention_class"] = "keep_daily_30d"

    by_week: dict[str, tuple[str, datetime]] = {}
    for entry in entries:
        if entry["snapshot"] in keep or entry["manual_review"]:
            continue
        age = entry["age_days"]
        if 45 <= age <= 134:
            captured = datetime.fromisoformat(entry["captured_at"])
            week_key = captured.strftime("%G-W%V")
            current = by_week.get(week_key)
            if current is None or captured > current[1]:
                by_week[week_key] = (entry["snapshot"], captured)

    for snapshot, _ in by_week.values():
        keep.add(snapshot)
    for entry in entries:
        if entry.get("retention_class") == "candidate" and entry["snapshot"] in keep:
            entry["retention_class"] = "keep_weekly_90d"

    removable_bytes = 0
    would_remove: list[str] = []
    for entry in entries:
        if entry["snapshot"] not in keep:
            entry["retention_class"] = "removable_candidate"
            would_remove.append(entry["snapshot"])
            removable_bytes += int(entry["bytes"])

    return {
        "snapshot_root": str(snapshot_root),
        "integrity_root": str(integrity_root),
        "generated_at": now.isoformat(),
        "total_snapshots": len(entries),
        "keep_count": len(keep),
        "removable_candidate_count": len(would_remove),
        "removable_bytes": removable_bytes,
        "would_remove": would_remove,
        "entries": entries,
        "policy": {
            "keep_all_days": 14,
            "daily_newest_days": 30,
            "weekly_newest_days": 90,
        },
    }


def format_retention_markdown(plan: Mapping[str, Any]) -> str:
    lines = [
        "# WHIEDA master snapshot retention plan (dry-run)",
        "",
        f"- Generated: `{plan.get('generated_at', '')}`",
        f"- Snapshots: {plan.get('total_snapshots', 0)} total, {plan.get('keep_count', 0)} keep",
        f"- Removable candidates: {plan.get('removable_candidate_count', 0)} "
        f"({plan.get('removable_bytes', 0)} bytes)",
        "",
        "## Policy",
        "",
        "- keep all for 14 days",
        "- then daily newest for 30 days",
        "- then weekly newest for 90 days",
        "- invalid manifest / blocking integrity / unparseable manifest → manual_review (always keep)",
        "",
    ]
    manual = [entry for entry in plan.get("entries", []) if entry.get("manual_review")]
    if manual:
        lines.extend(["## Manual review (always kept)", ""])
        for entry in manual:
            lines.append(
                f"- `{entry.get('snapshot_id')}`: {', '.join(entry.get('manual_review_reasons', []))}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
