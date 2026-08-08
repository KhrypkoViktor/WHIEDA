"""Load local export files (read-only)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

SUPPORTED_FORMATS = {"csv", "tsv", "json", "jsonl", "xlsx"}


def _load_xlsx(path: Path) -> list[dict[str, Any]]:
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise RuntimeError("xlsx support requires openpyxl (not installed)") from exc
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h or "").strip() for h in rows[0]]
    out: list[dict[str, Any]] = []
    for line in rows[1:]:
        item = {headers[i]: ("" if line[i] is None else str(line[i])) for i in range(len(headers))}
        out.append(item)
    return out


def load_table(path: Path, fmt: str) -> list[dict[str, Any]]:
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format: {fmt}")
    if not path.is_file():
        raise FileNotFoundError(str(path))

    if fmt == "csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    if fmt == "tsv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t"))
    if fmt == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [dict(x) for x in data]
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            return [dict(x) for x in data["rows"]]
        raise ValueError("JSON must be array of objects or {rows: []}")
    if fmt == "jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(dict(json.loads(line)))
        return rows
    if fmt == "xlsx":
        return _load_xlsx(path)
    raise ValueError(fmt)
