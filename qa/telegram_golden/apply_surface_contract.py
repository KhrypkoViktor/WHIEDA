#!/usr/bin/env python3
"""Apply execution_surface to golden corpus JSONL (in-place)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TG = Path(__file__).resolve().parent
LAB = TG / "lab"
CASES = TG / "whieda_telegram_golden_cases_v1.jsonl"
FLOWS = TG / "whieda_telegram_golden_flows_v1.jsonl"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    surface_mod = _load("surface", LAB / "surface.py")
    cases = [surface_mod.apply_execution_surface(row) for row in _read_jsonl(CASES)]
    flows = [surface_mod.apply_flow_surfaces(row) for row in _read_jsonl(FLOWS)]
    _write_jsonl(CASES, cases)
    _write_jsonl(FLOWS, flows)
    by_surface: dict[str, int] = {}
    for row in cases:
        surf = row.get("execution_surface")
        by_surface[surf] = by_surface.get(surf, 0) + 1
    print(json.dumps({"cases": len(cases), "flows": len(flows), "by_surface": by_surface}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
