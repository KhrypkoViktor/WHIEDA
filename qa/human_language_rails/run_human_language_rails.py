#!/usr/bin/env python3
"""Human language rails corpus: offline lint + local HTTP acceptance."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
LAB = PKG / "lab"
DEFAULT_CORPUS = PKG / "whieda_human_language_rails_v1.jsonl"
DEFAULT_FLOWS = PKG / "flows_v1.jsonl"
DEFAULT_TARGET = PKG / "hlr_target.local.json"
EXAMPLE_TARGET = PKG / "hlr_target.example.json"
REPORTS_DIR = PKG / "reports"
BASELINE_PATH = PKG / "baselines" / "latest.json"
OFFLINE_REPORT = ROOT / "backend" / "platform-api" / "docs" / "HUMAN_LANGUAGE_RAILS_CORPUS_LOCAL_REPORT.md"
HTTP_REPORT = ROOT / "backend" / "platform-api" / "docs" / "HUMAN_LANGUAGE_RAILS_HTTP_ACCEPTANCE_LOCAL_REPORT.md"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("hlr_corpus", LAB / "corpus.py")
target_mod = _load("hlr_target", LAB / "target.py")
http_runner_mod = _load("hlr_http_runner", LAB / "http_runner.py")
report_mod = _load("hlr_report", LAB / "report.py")
baseline_mod = _load("hlr_baseline", LAB / "baseline.py")


def write_offline_report(path: Path, stats: dict[str, object], *, errors: list[str]) -> None:
    by_rail_accepted = stats.get("by_rail_accepted") or {}
    by_rail_all = stats.get("by_rail_all") or {}
    lines = [
        "# Human Language Rails Corpus — Local Report",
        "",
        "Mode: offline QA corpus only. No Core, Telegram, Postgres or Sheets changes.",
        "",
        "## Totals",
        "",
        f"- Assertion turns (all): **{stats['assertions_total']}**",
        f"- Accepted assertions: **{stats['assertions_accepted']}**",
        f"- Pending surface: {stats['assertions_pending_surface']}",
        f"- Pending policy: {stats['assertions_pending_policy']}",
        f"- Flows (reconciled): **{stats['flows_total']}**",
        "",
        "## Accepted by rail",
        "",
    ]
    for rail, minimum in corpus_mod.RAIL_MINIMUMS.items():
        lines.append(f"- `{rail}`: {by_rail_accepted.get(rail, 0)} accepted (minimum {minimum})")
    lines.extend(["", "## All assertions by rail", ""])
    for rail in corpus_mod.RAIL_MINIMUMS:
        lines.append(f"- `{rail}`: {by_rail_all.get(rail, 0)} total")
    lines.extend(
        [
            "",
            "## Universal menu rule (accepted only)",
            "",
            "Accepted `universal_menu` rows require `must_contain_all`:",
        ]
    )
    for marker in corpus_mod.UNIVERSAL_MENU_MUST_CONTAIN_ALL:
        lines.append(f"- `{marker}`")
    lines.extend(["", "## Validation", "", "PASS" if not errors else "FAIL", ""])
    if errors:
        lines.extend([f"- {err}" for err in errors[:20]])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_offline(*, corpus_path: Path, flows_path: Path, report_path: Path) -> int:
    if not corpus_path.is_file():
        print(f"Missing corpus: {corpus_path}", file=sys.stderr)
        return 1
    cases = corpus_mod.load_cases(corpus_path)
    flows = corpus_mod.load_flows(flows_path)
    errors = corpus_mod.validate_cases(cases, flows=flows)
    stats = corpus_mod.corpus_stats(cases, flows=flows)
    write_offline_report(report_path, stats, errors=errors)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for err in errors[:10]:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("OK offline validation passed")
    return 0


def run_dry_run(*, corpus_path: Path, priority: str | None) -> int:
    cases = corpus_mod.load_cases(corpus_path)
    target = target_mod.load_hlr_target(EXAMPLE_TARGET)
    payload = http_runner_mod.run_hlr_http(
        target=target,
        cases=cases,
        client=None,
        priority=priority,
        dry_run=True,
    )
    pending = payload.get("pending") or {}
    print(
        f"DRY_RUN selected_accepted={payload.get('selected_accepted')} "
        f"flows={payload.get('selected_flows')} "
        f"pending={pending.get('count')} (NOT_RUN_PENDING)"
    )
    return 0


def run_live(
    *,
    corpus_path: Path,
    target_path: Path,
    priority: str | None,
    accept_baseline: bool,
    report_path: Path,
) -> int:
    target_path = target_mod.ensure_hlr_target(EXAMPLE_TARGET, target_path)
    target = target_mod.load_hlr_target(target_path)
    cases = corpus_mod.load_cases(corpus_path)

    ACCEPTANCE = ROOT / "qa" / "acceptance"
    if str(ACCEPTANCE) not in sys.path:
        sys.path.insert(0, str(ACCEPTANCE))
    from lab.transport import UrllibTransport  # noqa: WPS433

    client = UrllibTransport()
    payload = http_runner_mod.run_hlr_http(
        target=target,
        cases=cases,
        client=client,
        priority=priority,
        dry_run=False,
    )
    payload["target"] = target_mod.target_identity(target)
    payload["summary"] = report_mod.summarize_results(payload.get("results") or [], payload.get("pending") or {})
    json_path, md_path = report_mod.write_reports(payload, REPORTS_DIR)
    # Windows PowerShell commonly uses cp1251; reports retain Unicode while
    # console output stays printable and cannot abort a completed live run.
    print(json.dumps(payload["summary"], ensure_ascii=True))
    print(f"Report JSON: {json_path}")
    print(f"Report MD: {md_path}")

    if accept_baseline:
        ok, errors = baseline_mod.can_accept_baseline(payload)
        if not ok:
            print("Baseline gate: FAIL")
            for err in errors:
                print(f"  - {err}")
            return 1
        baseline_mod.save_baseline(BASELINE_PATH, baseline_mod.build_baseline(payload))
        print(f"Baseline written: {BASELINE_PATH}")

    write_http_static_report(report_path, payload)
    print(f"HLR_HTTP: {payload.get('status')}")
    return 0 if payload.get("status") == "PASS" else 1


def write_http_static_report(path: Path, payload: dict[str, Any]) -> None:
    summary = payload.get("summary") or {}
    pending = payload.get("pending") or {}
    lines = [
        "# Human Language Rails HTTP Acceptance — Local Report",
        "",
        "Mode: local HTTP acceptance against Platform Core only. No Core changes in this task.",
        "",
        f"- Run ID: `{payload.get('run_id')}`",
        f"- Status: **{payload.get('status')}**",
        f"- Phase: `{payload.get('run_phase')}`",
        "",
        "## Execution totals",
        "",
        f"- Selected accepted: {summary.get('selected')}",
        f"- Executed: {summary.get('executed')}",
        f"- PASS: {summary.get('pass')}",
        f"- FAIL: {summary.get('fail')}",
        f"- NOT_RUN_SETUP: {summary.get('not_run_setup')}",
        f"- NOT_RUN_PENDING (corpus): {pending.get('count')}",
        "",
        "## Failures by rail",
        "",
    ]
    for rail, counts in sorted((summary.get("by_rail") or {}).items()):
        lines.append(f"- `{rail}`: {counts}")
    lines.extend(["", "## Live failures (case id + rail)", ""])
    failures = summary.get("failures") or []
    if not failures:
        lines.append("- none in this run (or HTTP lab not executed)")
    else:
        for row in failures:
            lines.append(
                f"- `{row.get('case_id')}` · rail `{row.get('expected_rail')}` · "
                f"{row.get('reason')}"
            )
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/human_language_rails/run_human_language_rails.py --dry-run",
            "python qa/human_language_rails/run_human_language_rails.py --live",
            "python backend/platform-api/scripts/run_local_core_lab.py --e2e --human-language-rails --golden-master-seed",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Human language rails corpus lab")
    parser.add_argument("--offline", action="store_true", help="Offline corpus lint/metrics")
    parser.add_argument("--live", action="store_true", help="Run accepted corpus against local Core")
    parser.add_argument("--dry-run", action="store_true", help="Count selected cases; zero HTTP")
    parser.add_argument("--check-target", action="store_true", help="Health/OpenAPI probe only")
    parser.add_argument("--accept-baseline", action="store_true", help="Write baseline after clean full run")
    parser.add_argument("--priority", help="Filter accepted assertions by priority (e.g. P0)")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--flows", type=Path, default=DEFAULT_FLOWS)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--offline-report", type=Path, default=OFFLINE_REPORT)
    parser.add_argument("--http-report", type=Path, default=HTTP_REPORT)
    args = parser.parse_args()

    if args.check_target:
        target_path = target_mod.ensure_hlr_target(EXAMPLE_TARGET, args.target)
        check = target_mod.check_hlr_target(target_path)
        print(json.dumps(check, ensure_ascii=False, indent=2))
        return 0 if check.get("status") == "PASS" else 1

    if args.dry_run:
        return run_dry_run(corpus_path=args.corpus, priority=args.priority)

    if args.live:
        return run_live(
            corpus_path=args.corpus,
            target_path=args.target,
            priority=args.priority,
            accept_baseline=args.accept_baseline,
            report_path=args.http_report,
        )

    if args.offline or not any([args.live, args.dry_run, args.check_target]):
        return run_offline(corpus_path=args.corpus, flows_path=args.flows, report_path=args.offline_report)

    parser.error("unknown mode")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
