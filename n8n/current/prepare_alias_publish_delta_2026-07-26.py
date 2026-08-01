"""Build a deduplicated, testable alias publish delta from the reviewed staging list.

This script does not edit Google Sheets or production. It is deliberately a
separate gate between raw-chat extraction and the editable master layer.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from io import StringIO
from pathlib import Path

import requests


ROOT = Path(r"D:\Projects\WHIEDA")
OUT = ROOT / "n8n" / "live-exports" / date.today().isoformat()
DECISIONS = OUT / "WHIEDA_alias_candidate_decisions_2026-07-26.csv"
SHEET_TSV = "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001001"


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold().replace("ё", "е").strip())


def is_publishable(alias: str) -> bool:
    # Notes accidentally extracted as alias labels must never go to runtime.
    return bool(alias) and len(alias) >= 2 and "(" not in alias and ")" not in alias and not re.search(r"\bвариант|орфограф", alias, re.I)


def main() -> None:
    decision_rows = list(csv.DictReader(DECISIONS.open(encoding="utf-8-sig")))
    response = requests.get(SHEET_TSV, timeout=30)
    response.raise_for_status()
    master_rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig")), delimiter="\t"))
    master_aliases = {normalize(row.get("alias", "")) for row in master_rows}
    seen = set(master_aliases)
    publish, skipped = [], []

    for row in decision_rows:
        alias = str(row.get("alias", "")).strip()
        key = normalize(alias)
        if row.get("decision") != "direct_name_variant":
            continue
        if not is_publishable(alias):
            skipped.append({**row, "skip_reason": "annotation_or_invalid_alias"})
            continue
        if key in seen:
            skipped.append({**row, "skip_reason": "already_in_master"})
            continue
        seen.add(key)
        publish.append({
            "project_id": "whieda",
            "alias": alias,
            "canonical_sku": row["canonical_sku"],
            "canonical_name": row["canonical_name"],
            "match_type": "alias",
            "priority": "85",
            "active": "TRUE",
            "answer_scope": "price+description",
            "notes": "candidate source: raw dialogues; direct name variant; auto-deduped 2026-07-26",
            "updated_at": date.today().isoformat(),
        })

    OUT.mkdir(parents=True, exist_ok=True)
    publish_path = OUT / "WHIEDA_alias_publish_delta_ready_2026-07-26.csv"
    skip_path = OUT / "WHIEDA_alias_publish_delta_skipped_2026-07-26.csv"
    with publish_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "alias", "canonical_sku", "canonical_name", "match_type", "priority", "active", "answer_scope", "notes", "updated_at"])
        writer.writeheader(); writer.writerows(publish)
    with skip_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(skipped[0]) if skipped else ["alias", "skip_reason"])
        writer.writeheader(); writer.writerows(skipped)
    print({"master_aliases": len(master_aliases), "ready_to_publish": len(publish), "skipped": len(skipped), "publish_path": str(publish_path), "skip_path": str(skip_path)})


if __name__ == "__main__":
    main()
