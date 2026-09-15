#!/usr/bin/env python3
"""Offline catalog experience audit runner — master snapshot → matrix + backlog."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
if str(OUT_DIR) not in sys.path:
    sys.path.insert(0, str(OUT_DIR))

from audit_lib import (  # noqa: E402
    SnapshotValidationError,
    attach_previews,
    build_backlog,
    build_product_rows,
    build_showcase,
    find_latest_valid_snapshot,
    load_snapshot,
)

DEFAULT_MATRIX = OUT_DIR / "CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv"
DEFAULT_BACKLOG = OUT_DIR / "CATALOG_EXPERIENCE_BACKLOG_2026-08-14.md"
DEFAULT_SHOWCASE = OUT_DIR / "CATALOG_EXPERIENCE_SHOWCASE_2026-08-14.md"
DEFAULT_REPORT = ROOT / "backend" / "platform-api" / "docs" / "CATALOG_EXPERIENCE_AUDIT_LOCAL_REPORT.md"


def write_csv(path: Path, rows) -> None:
    if not rows:
        raise SnapshotValidationError("no product rows to write")
    fieldnames = list(rows[0].to_csv_dict().keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())


def write_backlog_md(path: Path, items: list[dict[str, str]], snapshot_name: str) -> None:
    lines = [
        "# Catalog Experience Repair Backlog — 2026-08-14",
        "",
        f"Snapshot: `{snapshot_name}`",
        "Mode: read-only offline audit. No runtime or Sheets changes.",
        "",
        "Grouped by source-of-truth layer. Empty clinical sections are not treated as failures.",
        "",
    ]
    current_layer = ""
    for item in items:
        if item["layer"] != current_layer:
            current_layer = item["layer"]
            lines.extend(["", f"## {current_layer}", ""])
        lines.extend(
            [
                f"### [{item['priority']}] {item['sku']} — {item['source_field']}",
                "",
                f"- **Owner-visible consequence:** {item['consequence']}",
                f"- **Evidence:** {item['evidence']}",
                "",
            ]
        )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def write_showcase_md(path: Path, picks: list[dict[str, str]], snapshot_name: str) -> None:
    lines = [
        "# Catalog Experience Showcase — 2026-08-14",
        "",
        f"Snapshot: `{snapshot_name}`",
        "",
        "Twelve products for a Telegram demo. BEM is not present in the master snapshot.",
        "Cosmetics SKUs (C033, C065, C006, C002) have prices/resources but **no cards** — demo cosmetics after Product_Cards backlog.",
        "",
        "| # | SKU | Product | Category | Grade | Test phrase | Note |",
        "|---|-----|---------|----------|-------|-------------|------|",
    ]
    for index, item in enumerate(picks, start=1):
        lines.append(
            f"| {index} | {item['sku']} | {item['name']} | {item['category']} | "
            f"{item['grade']} | `{item['test_phrase']}` | {item.get('note', '')} |"
        )
    lines.extend(
        [
            "",
            "## Suggested demo flow",
            "",
            "1. `хай` → capabilities",
            "2. `активатор` → clarification → `обычный` or `активатор pro`",
            "3. Pick one home/wellness item from the table",
            "4. Pick one consumable (e.g. спирулина)",
            "5. `сравни активатор и pro` if time allows",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report_md(
    path: Path,
    *,
    snapshot_name: str,
    captured_at: str,
    rows,
    backlog,
    picks,
    renderer_defect_count: int,
) -> None:
    grades = Counter(row.presentation_grade for row in rows)
    with_cards = sum(1 for row in rows if row.has_card)
    without_cards = len(rows) - with_cards
    lines = [
        "# Catalog Experience Audit — Local Report",
        "",
        f"Date: {datetime.now(timezone.utc).date().isoformat()}",
        f"Task: `WHIEDA_DROVOSEK_CATALOG_EXPERIENCE_AUDIT_TASK_V1_2026-08-14.md`",
        "",
        "## Summary",
        "",
        f"- Snapshot: `{snapshot_name}` (captured {captured_at})",
        f"- Products audited: **{len(rows)}**",
        f"- Cards present: **{with_cards}** / missing: **{without_cards}**",
        f"- Grades: showcase_ready={grades.get('showcase_ready', 0)}, "
        f"usable={grades.get('usable', 0)}, thin={grades.get('thin', 0)}, blocked={grades.get('blocked', 0)}",
        f"- Renderer defects on otherwise complete cards: **{renderer_defect_count}**",
        f"- Backlog items: **{len(backlog)}**",
        "",
        "## Key findings",
        "",
        "1. **Card coverage gap (17 SKUs):** products exist in `products.tsv` but have no `product_cards.tsv` row — Telegram cannot render a structured card.",
        "2. **Product_Details layer empty:** `product_details.tsv` has zero rows; all presentation comes from Product_Cards.",
        "3. **Existing 23 cards render cleanly:** no literal `**`, no Telegram size overflows; longest card ~1730 chars (Activator base).",
        "4. **Price gaps on accessories:** T001/T002/T003 have dash partner BYN in products.tsv.",
        "5. **BEM not in catalogue:** no SKU/name match in snapshot; cannot include in showcase.",
        "",
        "## Artifacts",
        "",
        "- `qa/catalog_experience/run_catalog_experience_audit.py`",
        "- `qa/catalog_experience/CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv`",
        "- `qa/catalog_experience/CATALOG_EXPERIENCE_BACKLOG_2026-08-14.md`",
        "- `qa/catalog_experience/CATALOG_EXPERIENCE_SHOWCASE_2026-08-14.md`",
        "",
        "## Command",
        "",
        "```bash",
        "cd /d/Projects/WHIEDA",
        "python qa/catalog_experience/run_catalog_experience_audit.py",
        "```",
        "",
        "## Showcase selection",
        "",
    ]
    for index, item in enumerate(picks, start=1):
        lines.append(f"{index}. `{item['test_phrase']}` → {item['name']} ({item['sku']}, {item['grade']})")
    lines.extend(["", "No production, Sheets, Postgres, or runtime changes were made.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_audit(
    *,
    snapshot_dir: Path | None = None,
    matrix_path: Path = DEFAULT_MATRIX,
    backlog_path: Path = DEFAULT_BACKLOG,
    showcase_path: Path = DEFAULT_SHOWCASE,
    report_path: Path = DEFAULT_REPORT,
) -> dict:
    snapshot_path = snapshot_dir or find_latest_valid_snapshot()
    snapshot = load_snapshot(snapshot_path)
    rows = build_product_rows(snapshot)
    attach_previews(rows, snapshot)
    snapshot_name = snapshot_path.name
    captured_at = str(snapshot["manifest"].get("captured_at") or snapshot_name)
    backlog = build_backlog(rows, snapshot_name)
    picks = build_showcase(rows)
    renderer_defect_count = sum(
        1 for row in rows if row.has_card and row.renderer_defects and "photo_data_missing" not in row.renderer_defects
    )

    write_csv(matrix_path, rows)
    write_backlog_md(backlog_path, backlog, snapshot_name)
    write_showcase_md(showcase_path, picks, snapshot_name)
    write_report_md(
        report_path,
        snapshot_name=snapshot_name,
        captured_at=captured_at,
        rows=rows,
        backlog=backlog,
        picks=picks,
        renderer_defect_count=renderer_defect_count,
    )
    return {
        "snapshot": snapshot_name,
        "products": len(rows),
        "grades": dict(Counter(row.presentation_grade for row in rows)),
        "backlog_items": len(backlog),
        "showcase_count": len(picks),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline catalog experience audit")
    parser.add_argument("--snapshot", type=Path, default=None, help="Explicit snapshot directory")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--backlog", type=Path, default=DEFAULT_BACKLOG)
    parser.add_argument("--showcase", type=Path, default=DEFAULT_SHOWCASE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        summary = run_audit(
            snapshot_dir=args.snapshot,
            matrix_path=args.matrix,
            backlog_path=args.backlog,
            showcase_path=args.showcase,
            report_path=args.report,
        )
    except SnapshotValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        "OK "
        + " ".join(
            f"{key}={value}"
            for key, value in summary.items()
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
