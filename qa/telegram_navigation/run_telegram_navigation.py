#!/usr/bin/env python3
"""Telegram navigation acceptance runner (offline-first)."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TG_ROOT = Path(__file__).resolve().parent
LAB = TG_ROOT / "lab"
PLATFORM = ROOT / "backend" / "platform-api"
DEFAULT_CORPUS = TG_ROOT / "whieda_telegram_navigation_flows_v1.jsonl"
REPORT_DIR = ROOT / "backend" / "platform-api" / "reports" / "telegram_navigation"
NAV_TESTS = ROOT / "backend" / "platform-api" / "tests" / "test_telegram_navigation.py"

if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("tg_nav_corpus", LAB / "corpus.py")
offline_mod = _load("tg_nav_offline", LAB / "offline_runner.py")


def _pytest() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(NAV_TESTS),
        str(ROOT / "backend" / "platform-api" / "tests" / "test_telegram_catalog_repository.py"),
        "-q",
    ]
    return subprocess.run(cmd, cwd=str(ROOT / "backend" / "platform-api")).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA Telegram navigation lab")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    build_script = TG_ROOT / "build_flows.py"
    if build_script.is_file():
        subprocess.run([sys.executable, str(build_script)], cwd=str(ROOT), check=False)
    if not args.corpus.is_file():
        print(f"FAIL: corpus missing {args.corpus}", file=sys.stderr)
        return 1

    flows = corpus_mod.load_flows(args.corpus)
    lint_errors = corpus_mod.validate_flows(flows)
    stats = corpus_mod.flow_stats(flows)
    if lint_errors:
        print("Corpus lint: FAIL")
        for err in lint_errors:
            print(f"  - {err}")
        return 1
    print(f"Corpus lint: PASS ({stats['flows']} flows)")

    if _pytest() != 0:
        print("Unit tests: FAIL")
        return 1
    print("Unit tests: PASS")

    offline_result = offline_mod.run_offline_checks(flows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest_run.json").write_text(
        __import__("json").dumps(offline_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(offline_result.get("summary_line"))
    for category in corpus_mod.CATEGORIES:
        row = (offline_result.get("category_stats") or {}).get(category) or {"flows": 0, "passed": 0}
        status = "PASS" if row["flows"] and row["passed"] == row["flows"] else ("NOT_RUN" if not row["flows"] else "FAIL")
        print(f"{category}: {status} ({row['passed']}/{row['flows']} flows)")

    if offline_result.get("status") != "PASS":
        return 1

    if args.offline and not args.live:
        print("HTTP E2E: NOT_RUN (offline mode)")
        print("TELEGRAM_NAVIGATION: PASS (offline)")
        return 0

    print("Live HTTP: NOT_RUN (navigation lab is offline contract)")
    print("TELEGRAM_NAVIGATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
