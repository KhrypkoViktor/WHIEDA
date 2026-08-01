"""Turn Claude JSONL batches into reviewable SQL and regression candidates.

This script never publishes product, medical, or marketing content. It only builds
deterministic artifacts for the owner-approved import step.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path


SOURCE_DIR = Path(r"D:\Projects\WHIEDA\RAG\1 компиляция. диалоги с врачами")
OUT_DIR = Path(r"D:\Projects\WHIEDA\n8n\live-exports") / date.today().isoformat()


def read_jsonl(path: Path):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            yield number, json.loads(line)
        except json.JSONDecodeError:
            continue


def stable_id(prefix: str, value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:12]}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest, aliases, regression, medical, business = [], [], [], [], []

    for path in sorted(SOURCE_DIR.glob("batch_*.jsonl")):
        rows = list(read_jsonl(path))
        manifest.append({"file": path.name, "rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for line, row in rows:
            source = str(row.get("source_id") or path.stem)
            if path.name == "batch_aliases.jsonl" and row.get("alias") and row.get("canonical_term"):
                aliases.append({"candidate_id": stable_id("ALIAS", row), "source_id": source, "alias": row["alias"], "canonical_term": row["canonical_term"], "raw_quote": row.get("raw_quote", ""), "state": "pending_owner_mapping"})
            elif path.name == "batch_smoke_cases.jsonl" and row.get("question"):
                regression.append({"candidate_id": stable_id("SMOKE", row), "source_id": source, "question": row["question"], "topic": row.get("topic", ""), "expected_grounding": row.get("expected_grounding", ""), "state": "pending_expected_sql_route"})
            elif path.name == "batch_medical_review.jsonl":
                medical.append({"candidate_id": stable_id("MED", row), "source_id": source, "statement": row.get("statement", ""), "concern": row.get("concern", ""), "severity": row.get("severity", ""), "state": "medical_review_required"})
            elif path.name in {"batch_faq_candidates.jsonl", "batch_objections.jsonl", "batch_questions.jsonl"}:
                business.append({"candidate_id": stable_id("KNOW", row), "source_id": source, "kind": path.stem.removeprefix("batch_"), "question_or_objection": row.get("question") or row.get("objection") or "", "topic": row.get("topic") or row.get("category") or "", "state": "pending_owner_review"})

    def write_csv(name: str, rows: list[dict]):
        if not rows:
            return
        with (OUT_DIR / name).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    write_csv("WHIEDA_claude_alias_candidates.csv", aliases)
    write_csv("WHIEDA_claude_regression_candidates.csv", regression)
    write_csv("WHIEDA_claude_medical_review.csv", medical)
    write_csv("WHIEDA_claude_knowledge_review.csv", business)
    report = {"date": date.today().isoformat(), "manifest": manifest, "counts": {"aliases": len(aliases), "regression": len(regression), "medical": len(medical), "faq_objections_questions": len(business)}, "topics": Counter(x.get("topic", "") for x in regression).most_common(), "publication": "none; candidates only"}
    (OUT_DIR / "WHIEDA_claude_batch_intake_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
