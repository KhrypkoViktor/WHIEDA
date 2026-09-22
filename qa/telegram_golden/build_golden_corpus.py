#!/usr/bin/env python3
"""Build Telegram golden corpus from Tier-1 labs (read-only sources)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TG = Path(__file__).resolve().parent
LAB = TG / "lab"
OUT_CASES = TG / "whieda_telegram_golden_cases_v1.jsonl"
OUT_FLOWS = TG / "whieda_telegram_golden_flows_v1.jsonl"
OUT_REPORT = TG / "reports" / "build_lint.json"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


importer = _load("tg_golden_importer", LAB / "importer.py")
corpus = _load("tg_golden_corpus", LAB / "corpus.py")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _out_dir() -> Path:
    """Where the built corpus lands. The curated corpus (the one the bot is
    measured against) lives next to this script, and a test run used to
    overwrite it silently — `--out-dir` lets a test build into a temp folder
    (found 22.09.2026, after a test run replaced 267 curated checks)."""
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == "--out-dir" and index + 1 < len(args):
            return Path(args[index + 1])
        if arg.startswith("--out-dir="):
            return Path(arg.split("=", 1)[1])
    return TG


def main() -> int:
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    global OUT_CASES, OUT_FLOWS, OUT_REPORT
    OUT_CASES = out_dir / OUT_CASES.name
    OUT_FLOWS = out_dir / OUT_FLOWS.name
    OUT_REPORT = out_dir / "reports" / OUT_REPORT.name

    compile_script = TG / "compile_snapshot_fixtures.py"
    if compile_script.is_file():
        subprocess.run([sys.executable, str(compile_script)], cwd=str(ROOT), check=False)

    negative_script = TG / "build_negative_fixtures.py"
    if negative_script.is_file():
        code = subprocess.run([sys.executable, str(negative_script)], cwd=str(ROOT)).returncode
        if code != 0:
            return code

    cases, flows, meta = importer.build_all(ROOT)
    flows = [flow for flow in flows if len(flow.get("turns") or []) >= 2]
    meta["flows_multi_turn"] = len(flows)
    case_errors = corpus.validate_cases(cases)
    flow_errors = corpus.validate_flows(flows)
    stats = corpus.case_stats(cases)
    flow_stats = corpus.flow_stats(flows)

    _write_jsonl(OUT_CASES, cases)
    _write_jsonl(OUT_FLOWS, flows)

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    lint = {
        "meta": meta,
        "stats": stats,
        "flow_stats": flow_stats,
        "case_errors": case_errors,
        "flow_errors": flow_errors,
    }
    OUT_REPORT.write_text(json.dumps(lint, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {len(cases)} cases -> {OUT_CASES}")
    print(f"Wrote {len(flows)} flows -> {OUT_FLOWS}")
    print(f"Lint report -> {OUT_REPORT}")
    print(f"By class: {stats['by_class']}")

    if case_errors or flow_errors:
        print("Lint: FAIL", file=sys.stderr)
        for err in case_errors + flow_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Lint: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
