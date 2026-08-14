"""TSV read/write for product discovery map."""

from __future__ import annotations

import csv
from pathlib import Path

from constants import TSV_COLUMNS


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        for col in TSV_COLUMNS:
            row.setdefault(col, "")
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TSV_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: str(row.get(col) or "") for col in TSV_COLUMNS})
