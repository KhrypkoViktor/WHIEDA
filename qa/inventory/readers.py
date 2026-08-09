"""Read-only file helpers for inventory scan."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TsvInfo:
    path: Path
    header: tuple[str, ...]
    data_rows: int
    non_empty_rows: int


@dataclass(frozen=True)
class JsonlInfo:
    path: Path
    valid_objects: int
    invalid_lines: int


def count_text_lines(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def read_tsv_info(path: Path) -> TsvInfo:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            header_row = next(reader)
        except StopIteration:
            return TsvInfo(path=path, header=(), data_rows=0, non_empty_rows=0)
        header = tuple(cell.strip() for cell in header_row)
        data_rows = 0
        non_empty = 0
        for row in reader:
            if not row or all(not str(cell).strip() for cell in row):
                continue
            data_rows += 1
            if any(str(cell).strip() for cell in row):
                non_empty += 1
    return TsvInfo(path=path, header=header, data_rows=data_rows, non_empty_rows=non_empty)


def read_jsonl_info(path: Path) -> JsonlInfo:
    valid = 0
    invalid = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(obj, dict):
                valid += 1
            else:
                invalid += 1
    return JsonlInfo(path=path, valid_objects=valid, invalid_lines=invalid)


def should_skip_path(path: Path, skip_parts: frozenset[str]) -> bool:
    return any(part in skip_parts for part in path.parts)


def rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
