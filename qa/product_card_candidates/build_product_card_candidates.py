#!/usr/bin/env python3
"""Build upload-ready Product_Cards draft candidates from snapshot + local sources."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "qa" / "catalog_experience"
if str(CATALOG_DIR) not in sys.path:
    sys.path.insert(0, str(CATALOG_DIR))
if str(OUT_DIR) not in sys.path:
    sys.path.insert(0, str(OUT_DIR))

from audit_lib import (  # noqa: E402
    CARD_SECTIONS,
    SnapshotValidationError,
    _clean,
    _is_active,
    _resources_by_sku,
    find_latest_valid_snapshot,
    load_snapshot,
    verify_snapshot,
)
from candidate_drafts import CANDIDATE_DRAFTS, MISSING_SKUS, REVIEW_KEYS  # noqa: E402

DEFAULT_TSV = OUT_DIR / "PRODUCT_CARDS_CANDIDATES_2026-08-14.tsv"
DEFAULT_REVIEW = OUT_DIR / "PRODUCT_CARDS_CANDIDATES_REVIEW_2026-08-14.md"
DEFAULT_REPORT = ROOT / "backend" / "platform-api" / "docs" / "PRODUCT_CARD_CANDIDATES_LOCAL_REPORT.md"

RUNTIME_COLUMNS = (
    "project_id",
    "sku",
    "canonical_name",
    "short_name",
    "category",
    "what_it_is",
    "who_asks_about_it",
    "common_use_cases",
    "how_to_use_short",
    "what_to_expect_soft",
    "contraindications_short",
    "do_not_claim",
    "when_to_escalate",
    "price_answer_mode",
    "primary_image_url",
    "source_status",
    "notes",
)
REVIEW_COLUMNS = ("source_refs", "provenance_status", "review_notes", "field_provenance")


@dataclass
class BuildSummary:
    snapshot_id: str
    candidate_count: int
    by_status: dict[str, int]
    missing_drafts: list[str]
    validation_errors: list[str]


def _primary_image_url(resources: list[dict[str, str]]) -> str:
    images = [
        row
        for row in resources
        if _clean(row.get("resource_type")).lower() == "image" and _is_active(row.get("active"))
    ]
    if not images:
        return ""
    images.sort(key=lambda row: int(_clean(row.get("priority")) or 0), reverse=True)
    return _clean(images[0].get("url"))


def _collect_source_refs(draft: dict[str, Any], snapshot_id: str) -> str:
    refs = {f"snapshot={snapshot_id}", "products.tsv"}
    fp = draft.get("field_provenance") or {}
    for value in fp.values():
        if ":" in str(value):
            refs.add(str(value).split(":", 1)[1])
    extra = draft.get("extra_source_refs") or []
    refs.update(extra)
    return "; ".join(sorted(refs))


def _validate_draft(sku: str, row: dict[str, str], draft: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if row["sku"] != sku:
        errors.append(f"{sku}: sku mismatch")
    for field in CARD_SECTIONS:
        text = _clean(row.get(field))
        status = _clean((draft.get("field_provenance") or {}).get(field, ""))
        if text and not status:
            if field == "contraindications_short" and _clean(row.get("do_not_claim")):
                continue
            errors.append(f"{sku}: {field} filled without field_provenance")
        if not text and (
            status.startswith("missing_source")
            or status.startswith("needs_owner_review")
            or not status
        ):
            continue
        if not text:
            errors.append(f"{sku}: empty runtime field {field}")
    image = _clean(row.get("primary_image_url"))
    image_fp = _clean((draft.get("field_provenance") or {}).get("primary_image_url", ""))
    if image and not image_fp.startswith("confirmed:"):
        errors.append(f"{sku}: primary_image_url without confirmed provenance")
    if "**" in " ".join(_clean(row.get(f)) for f in CARD_SECTIONS):
        errors.append(f"{sku}: markdown stars in card text")
    return errors


def build_candidate_row(
    sku: str,
    product: dict[str, str],
    resources: list[dict[str, str]],
    draft: dict[str, Any],
    snapshot_id: str,
) -> dict[str, str]:
    confirmed_image = _primary_image_url(resources)
    draft_image = _clean(draft.get("primary_image_url"))
    primary_image = confirmed_image or (draft_image if draft_image and confirmed_image == draft_image else confirmed_image)
    if draft_image and confirmed_image and draft_image != confirmed_image:
        primary_image = confirmed_image

    row = {
        "project_id": _clean(product.get("project_id")) or "whieda",
        "sku": sku,
        "canonical_name": _clean(product.get("canonical_name")),
        "short_name": _clean(draft.get("short_name")) or _clean(product.get("canonical_name")),
        "category": _clean(draft.get("category")) or _clean(product.get("category")) or "other",
        "what_it_is": _clean(draft.get("what_it_is")),
        "who_asks_about_it": _clean(draft.get("who_asks_about_it")),
        "common_use_cases": _clean(draft.get("common_use_cases")),
        "how_to_use_short": _clean(draft.get("how_to_use_short")),
        "what_to_expect_soft": _clean(draft.get("what_to_expect_soft")),
        "contraindications_short": _clean(draft.get("contraindications_short")),
        "do_not_claim": _clean(draft.get("do_not_claim")),
        "when_to_escalate": _clean(draft.get("when_to_escalate")),
        "price_answer_mode": "structured_price",
        "primary_image_url": primary_image,
        "source_status": "candidate_draft",
        "notes": f"Candidate draft 2026-08-14. Not uploaded. Owner review required.",
        "source_refs": _collect_source_refs(draft, snapshot_id),
        "provenance_status": _clean(draft.get("provenance_status")) or "needs_owner_review",
        "review_notes": _clean(draft.get("review_notes")),
        "field_provenance": json.dumps(draft.get("field_provenance") or {}, ensure_ascii=False, sort_keys=True),
    }
    return row


def build_rows(snapshot: dict[str, Any]) -> tuple[list[dict[str, str]], BuildSummary]:
    snapshot_id = snapshot["snapshot_dir"].name
    products = snapshot["products"]
    resources_by_sku = _resources_by_sku(snapshot["resources_rows"])
    missing_drafts = [sku for sku in MISSING_SKUS if sku not in CANDIDATE_DRAFTS]
    errors: list[str] = []
    rows: list[dict[str, str]] = []

    for sku in MISSING_SKUS:
        if sku not in products:
            errors.append(f"{sku}: absent from products.tsv")
            continue
        if sku not in CANDIDATE_DRAFTS:
            continue
        draft = CANDIDATE_DRAFTS[sku]
        row = build_candidate_row(sku, products[sku], resources_by_sku.get(sku, []), draft, snapshot_id)
        errors.extend(_validate_draft(sku, row, draft))
        rows.append(row)

    by_status = Counter(row["provenance_status"] for row in rows)
    return rows, BuildSummary(
        snapshot_id=snapshot_id,
        candidate_count=len(rows),
        by_status=dict(by_status),
        missing_drafts=missing_drafts,
        validation_errors=errors,
    )


def write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = list(RUNTIME_COLUMNS) + list(REVIEW_COLUMNS)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_review_md(path: Path, rows: list[dict[str, str]], summary: BuildSummary) -> None:
    slices = {
        "ready_for_owner_upload": [],
        "needs_owner_review": [],
        "blocked_not_in_master": [],
    }
    for row in rows:
        status = row["provenance_status"]
        if status not in slices:
            status = "needs_owner_review"
        slices[status].append(row)

    lines = [
        "# Product Card Candidates Review — 2026-08-14",
        "",
        f"Snapshot: `{summary.snapshot_id}`",
        "Mode: draft candidates only. No Sheets/Postgres/runtime upload.",
        "",
        f"Total candidates: **{summary.candidate_count}**",
        "",
        "## Counts by confidence",
        "",
    ]
    for status, count in sorted(summary.by_status.items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "BEM is documented separately in `BEM_CATALOG_EVIDENCE_2026-08-14.md`.", ""])

    titles = {
        "ready_for_owner_upload": "Ready for owner upload",
        "needs_owner_review": "Needs source or wording confirmation",
        "blocked_not_in_master": "Blocked — absent from current master",
    }
    for key, title in titles.items():
        lines.extend(["", f"## {title}", ""])
        bucket = slices[key]
        if not bucket:
            lines.append("_None._")
            continue
        for row in bucket:
            lines.extend(
                [
                    f"### {row['sku']} — {row['short_name']}",
                    "",
                    f"- **Canonical name:** {row['canonical_name']}",
                    f"- **Review notes:** {row['review_notes'] or '—'}",
                    f"- **Primary image:** {row['primary_image_url'] or '—'}",
                    f"- **Sources:** {row['source_refs']}",
                    "",
                ]
            )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def write_report(path: Path, summary: BuildSummary, tsv_path: Path, review_path: Path) -> None:
    lines = [
        "# Product Card Candidates — Local Report",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "## Scope",
        "",
        "Upload-ready **draft** Product_Cards rows for 16 consumer SKUs missing from master `product_cards.tsv`.",
        "Separate BEM evidence in `qa/product_card_candidates/BEM_CATALOG_EVIDENCE_2026-08-14.md`.",
        "",
        "## Safety",
        "",
        "- No Sheets, Postgres, n8n, Dify, Telegram or production writes were performed.",
        "- No edits under `backend/platform-api/app/**`.",
        "- Existing approved cards were not rewritten.",
        "",
        "## Snapshot",
        "",
        f"- `{summary.snapshot_id}`",
        "",
        "## Outputs",
        "",
        f"- `{_display_path(tsv_path)}`",
        f"- `{_display_path(review_path)}`",
        f"- `qa/product_card_candidates/BEM_CATALOG_EVIDENCE_2026-08-14.md`",
        "",
        "## Summary",
        "",
        f"- Candidates: **{summary.candidate_count}** / 16 expected",
        "",
        "### By provenance_status",
        "",
    ]
    for status, count in sorted(summary.by_status.items()):
        lines.append(f"- `{status}`: {count}")
    if summary.missing_drafts:
        lines.extend(["", "### Missing drafts", ""] + [f"- {sku}" for sku in summary.missing_drafts])
    if summary.validation_errors:
        lines.extend(["", "### Validation errors", ""] + [f"- {err}" for err in summary.validation_errors])
    else:
        lines.extend(["", "Validation: **pass** (schema + source refs)."])
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/product_card_candidates/build_product_card_candidates.py",
            "pytest qa/product_card_candidates/tests/test_product_card_candidates.py -q",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_build(
    *,
    snapshot_dir: Path | None = None,
    tsv_path: Path = DEFAULT_TSV,
    review_path: Path = DEFAULT_REVIEW,
    report_path: Path = DEFAULT_REPORT,
) -> BuildSummary:
    snap_path = snapshot_dir or find_latest_valid_snapshot()
    verify_snapshot(snap_path)
    snapshot = load_snapshot(snap_path)
    rows, summary = build_rows(snapshot)
    if summary.validation_errors:
        raise SnapshotValidationError("; ".join(summary.validation_errors))
    if summary.candidate_count != len(MISSING_SKUS):
        raise SnapshotValidationError(
            f"expected {len(MISSING_SKUS)} candidates, got {summary.candidate_count}"
        )
    write_tsv(tsv_path, rows)
    write_review_md(review_path, rows, summary)
    write_report(report_path, summary, tsv_path, review_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Product_Cards draft candidates TSV.")
    parser.add_argument("--snapshot", type=Path, default=None, help="Structured master snapshot directory")
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    summary = run_build(
        snapshot_dir=args.snapshot,
        tsv_path=args.tsv,
        review_path=args.review,
        report_path=args.report,
    )
    print(
        f"OK snapshot={summary.snapshot_id} candidates={summary.candidate_count} "
        f"status={summary.by_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
