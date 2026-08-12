"""Offline golden corpus checks: lint, snapshots, importer round-trip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TG = Path(__file__).resolve().parents[1]

FORBIDDEN_SNAPSHOT_PHRASES = ("Dify", "Traceback", "не знаю", "нет в базе")
REQUIRED_SERVICE_INTENTS = (
    "greeting",
    "capabilities",
    "help",
    "smalltalk_status",
    "company_intro_fallback",
    "calculator_instruction",
    "discomfort_boundary",
    "mlm_objection_fallback",
)


def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


MARKER_TO_FIELD = {
    "Коротко:": "what_it_is",
    "Для кого:": "who_asks_about_it",
    "Ограничения:": "contraindications_short",
}


def _marker_field(marker: str) -> str | None:
    for suffix, field in MARKER_TO_FIELD.items():
        if marker.endswith(suffix) or suffix in marker:
            return field
    return None


def validate_snapshot_cards(tg_root: Path | None = None, *, min_cards: int = 12) -> list[str]:
    root = tg_root or TG
    manifest_path = root / "fixtures" / "snapshot_cards" / "manifest.json"
    errors: list[str] = []
    if not manifest_path.is_file():
        return ["snapshot_cards manifest missing"]
    manifest = _load_manifest(manifest_path)
    cards = manifest.get("cards") or []
    if len(cards) < min_cards:
        errors.append(f"snapshot_cards manifest expected >={min_cards} cards, got {len(cards)}")
    for card in cards:
        slug = str(card.get("slug") or "")
        file_name = str(card.get("file") or "")
        markers = card.get("golden_markers") or []
        if not markers:
            errors.append(f"snapshot card {slug}: missing golden_markers")
        card_path = root / "fixtures" / "snapshot_cards" / file_name
        if not card_path.is_file():
            errors.append(f"snapshot card file missing: {file_name}")
            continue
        payload = json.loads(card_path.read_text(encoding="utf-8"))
        card_body = payload.get("card") or {}
        sku = str(card.get("sku") or "")
        if sku and str(payload.get("sku") or card_body.get("sku") or "") != sku:
            errors.append(f"snapshot card {slug}: sku mismatch")
        for marker in markers:
            field = _marker_field(marker)
            if field and not str(card_body.get(field) or "").strip():
                errors.append(f"snapshot card {slug}: missing source field {field} for marker {marker!r}")
    return errors


def validate_service_replies(tg_root: Path | None = None) -> list[str]:
    root = tg_root or TG
    manifest_path = root / "fixtures" / "snapshot_service_replies" / "manifest.json"
    errors: list[str] = []
    if not manifest_path.is_file():
        return ["snapshot_service_replies manifest missing"]
    manifest = _load_manifest(manifest_path)
    replies = manifest.get("replies") or []
    intent_ids = {str(r.get("intent_id") or "") for r in replies}
    for required in REQUIRED_SERVICE_INTENTS:
        if required not in intent_ids:
            errors.append(f"service reply missing intent: {required}")
    for reply in replies:
        intent_id = str(reply.get("intent_id") or "")
        text = str(reply.get("text") or "")
        must_contain = list(reply.get("must_contain") or [])
        must_not = list(reply.get("must_not_contain") or [])
        if not text:
            errors.append(f"service reply {intent_id}: empty text")
        for token in must_contain:
            if token not in text:
                errors.append(f"service reply {intent_id}: must_contain missing {token!r}")
        for token in must_not:
            if token in text:
                errors.append(f"service reply {intent_id}: forbidden phrase present {token!r}")
        for token in FORBIDDEN_SNAPSHOT_PHRASES:
            if token in text and token not in must_not:
                errors.append(f"service reply {intent_id}: contains forbidden {token!r}")
    return errors


def validate_smoke_price_roundtrip(importer_mod: Any) -> list[str]:
    root = TG.parents[1]
    smoke_path = root / "n8n/current/source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv"
    cases, _ = importer_mod.import_smoke_cases(smoke_path)
    p0_004 = [c for c in cases if c.get("source", {}).get("ref") == "P0-004"]
    if not p0_004:
        return ["smoke P0-004 not imported"]
    row = p0_004[0]
    if row.get("class") != "price":
        return [f"smoke P0-004 expected class price, got {row.get('class')}"]
    if row.get("expected", {}).get("mode") != "structured_price":
        return ["smoke P0-004 expected mode structured_price"]
    return []


def run_offline(
    *,
    cases: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    corpus_mod: Any,
    importer_mod: Any | None = None,
    negative_fixtures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    case_errors = corpus_mod.validate_cases(cases)
    flow_errors = corpus_mod.validate_flows(flows)
    snapshot_errors = validate_snapshot_cards(min_cards=12) + validate_service_replies()
    roundtrip_errors: list[str] = []
    if importer_mod is not None:
        roundtrip_errors = validate_smoke_price_roundtrip(importer_mod)
    negative_errors: list[str] = []
    if negative_fixtures is not None:
        negative_errors = corpus_mod.validate_negative_fixtures(negative_fixtures)

    all_errors = case_errors + flow_errors + snapshot_errors + roundtrip_errors + negative_errors
    stats = corpus_mod.case_stats(cases)
    flow_stats = corpus_mod.flow_stats(flows)
    return {
        "status": "PASS" if not all_errors else "FAIL",
        "case_errors": case_errors,
        "flow_errors": flow_errors,
        "snapshot_errors": snapshot_errors,
        "roundtrip_errors": roundtrip_errors,
        "negative_errors": negative_errors,
        "stats": stats,
        "flow_stats": flow_stats,
        "negative_count": len(negative_fixtures or []),
        "summary_line": (
            f"Telegram golden offline: {stats['cases']} cases, "
            f"{flow_stats['flows']} flows, {len(negative_fixtures or [])} negative fixtures"
        ),
    }
