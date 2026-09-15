"""Load approved snapshot cards and candidate TSV rows."""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CATALOG_AUDIT = ROOT / "qa" / "catalog_experience"
if str(CATALOG_AUDIT) not in sys.path:
    sys.path.insert(0, str(CATALOG_AUDIT))

from audit_lib import (  # noqa: E402
    CARD_SECTIONS,
    SnapshotValidationError,
    find_latest_valid_snapshot,
    load_snapshot,
)

CANDIDATES_TSV = ROOT / "qa" / "product_card_candidates" / "PRODUCT_CARDS_CANDIDATES_2026-08-14.tsv"
POLICY_FIELDS = ("do_not_claim", "when_to_escalate")
WORDING_FIELDS = CARD_SECTIONS + POLICY_FIELDS


@dataclass
class CardRecord:
    sku: str
    source_layer: str
    row: dict[str, str]
    snapshot_id: str | None = None


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_approved_cards(snapshot_dir: Path | None = None) -> tuple[str, list[CardRecord]]:
    snap = snapshot_dir or find_latest_valid_snapshot()
    data = load_snapshot(snap)
    cards = [
        CardRecord(sku=sku, source_layer="approved", row=row, snapshot_id=snap.name)
        for sku, row in sorted(data["cards"].items())
    ]
    return snap.name, cards


def load_candidate_cards(path: Path = CANDIDATES_TSV) -> list[CardRecord]:
    if not path.is_file():
        return []
    return [
        CardRecord(sku=str(row.get("sku") or ""), source_layer="candidate", row=row)
        for row in _read_tsv(path)
        if str(row.get("sku") or "").strip()
    ]


def load_all_cards(
    *,
    snapshot_dir: Path | None = None,
    candidates_path: Path = CANDIDATES_TSV,
) -> tuple[str, list[CardRecord]]:
    snapshot_id, approved = load_approved_cards(snapshot_dir)
    candidates = load_candidate_cards(candidates_path)
    return snapshot_id, approved + candidates


def parse_field_provenance(row: dict[str, str]) -> dict[str, str]:
    raw = str(row.get("field_provenance") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
