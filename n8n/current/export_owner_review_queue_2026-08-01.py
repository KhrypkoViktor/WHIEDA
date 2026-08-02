"""Export owner review queue from SQL quality candidates (no live changes)."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
REPORT_DIR = BASE_DIR.parents[1] / "reports" / "quality"
MEDICAL_CORPUS = (
    BASE_DIR.parents[1]
    / "RAG"
    / "1 компиляция. диалоги с врачами"
    / "2_SQL_корпус_из_RAW"
    / "10_MEDICAL_REVIEW_QUEUE.tsv"
)

QUEUE_BUCKETS = {
    "medical_review_required": "medical",
    "intent_review_required": "intent_gap",
    "review_required": "general",
}


def load_candidates(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates",
        type=Path,
        default=EXPORT_DIR / "WHIEDA_question_candidates.json",
    )
    args = parser.parse_args()
    if not args.candidates.exists():
        raise SystemExit(f"Candidates not found: {args.candidates}")

    rows = []
    for row in load_candidates(args.candidates):
        bucket = row.get("review_bucket") or ""
        if bucket not in QUEUE_BUCKETS:
            continue
        rows.append(
            {
                "queue_type": QUEUE_BUCKETS[bucket],
                "review_bucket": bucket,
                "candidate_id": row.get("candidate_id"),
                "canonical_question": row.get("canonical_question"),
                "intent_id": row.get("intent_id"),
                "entity_id": row.get("entity_id"),
                "frequency": row.get("frequency"),
                "source_paths": " | ".join(row.get("source_paths") or []),
                "publication": "owner_review_only",
                "status": "pending",
            }
        )

    rows.sort(key=lambda item: (item["queue_type"], -(item["frequency"] or 0), item["canonical_question"] or ""))
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = EXPORT_DIR / "WHIEDA_owner_review_queue.json"
    csv_path = EXPORT_DIR / "WHIEDA_owner_review_queue.csv"
    report_path = REPORT_DIR / f"WHIEDA_owner_review_queue_{date.today().isoformat()}.csv"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = list(rows[0].keys()) if rows else []
    for target in (csv_path, report_path):
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    summary = {
        "total": len(rows),
        "by_queue_type": dict(Counter(row["queue_type"] for row in rows)),
        "by_review_bucket": dict(Counter(row["review_bucket"] for row in rows)),
        "json": str(json_path),
        "csv": str(csv_path),
        "report_copy": str(report_path),
        "corpus_medical_reference": str(MEDICAL_CORPUS) if MEDICAL_CORPUS.exists() else None,
        "publication": "owner_review_only_no_runtime_change",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
