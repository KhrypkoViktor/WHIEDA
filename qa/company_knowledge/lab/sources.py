"""Read-only source loaders for company knowledge pack."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "qa" / "catalog_experience"
if str(CATALOG) not in sys.path:
    sys.path.insert(0, str(CATALOG))

from audit_lib import find_latest_valid_snapshot, load_snapshot, verify_snapshot  # noqa: E402

OBSIDIAN_PACK9 = Path(r"D:\Obsidian\WHIEDA\05_WHIEDA_RAG\Пакет 9. О компании WHIEDA - FOHERB..md")
PRODUCT_DIRECTION = ROOT / "WHIEDA_PRODUCT_DIRECTION.md"
ADVISOR_CONTRACT = ROOT / "backend" / "platform-api" / "docs" / "WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md"


@dataclass
class SourceBundle:
    snapshot_id: str
    snapshot_dir: Path
    layers: dict[str, list[dict[str, str]]]
    unreadable: list[str]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_sources(snapshot_dir: Path | None = None) -> SourceBundle:
    snap = snapshot_dir or find_latest_valid_snapshot()
    verify_snapshot(snap)
    layers = {
        "business_faq": _read_tsv(snap / "business_faq.tsv"),
        "business_objections": _read_tsv(snap / "business_objections.tsv"),
        "partners_ref": _read_tsv(snap / "partners_ref.tsv"),
        "structure_owners": _read_tsv(snap / "structure_owners.tsv"),
        "events": _read_tsv(snap / "events.tsv"),
        "community_resources": _read_tsv(snap / "community_resources.tsv"),
        "capability_responses": _read_tsv(snap / "capability_responses.tsv"),
    }
    unreadable: list[str] = []
    for path, label in (
        (OBSIDIAN_PACK9, str(OBSIDIAN_PACK9)),
        (PRODUCT_DIRECTION, "WHIEDA_PRODUCT_DIRECTION.md"),
        (ADVISOR_CONTRACT, "WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md"),
    ):
        if not path.is_file():
            unreadable.append(label)
    return SourceBundle(snapshot_id=snap.name, snapshot_dir=snap, layers=layers, unreadable=unreadable)


def read_doc_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()
