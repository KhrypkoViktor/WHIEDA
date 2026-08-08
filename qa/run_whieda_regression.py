#!/usr/bin/env python3
"""Offline WHIEDA regression corpus validator and reporter."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"
REPORT_PATH = ROOT / "qa" / "reports" / "WHIEDA_REGRESSION_CORPUS_REPORT.md"

REQUIRED_FIELDS = (
    "case_id",
    "group",
    "priority",
    "input",
    "context_before",
    "expected_mode",
    "must_contain",
    "must_not_contain",
    "expected_product",
    "source",
)

ALLOWED_GROUPS = {
    "catalog_card",
    "catalog_price",
    "aliases_typo",
    "followup_context",
    "photo_video_certificate",
    "comparison",
    "cart_and_basket",
    "business_faq",
    "promotion_event",
    "safety_and_clarification",
}

GROUP_MINIMUMS = {
    "catalog_card": 35,
    "catalog_price": 35,
    "aliases_typo": 35,
    "followup_context": 30,
    "photo_video_certificate": 25,
    "comparison": 20,
    "cart_and_basket": 20,
    "business_faq": 20,
    "promotion_event": 15,
    "safety_and_clarification": 15,
}

ALLOWED_PRIORITIES = {"P0", "P1", "P2"}

PLACEHOLDER_PATTERNS = (
    re.compile(r"\bTODO\b", re.I),
    re.compile(r"\bexample\s*1\b", re.I),
    re.compile(r"\bplaceholder\b", re.I),
    re.compile(r"\bfixme\b", re.I),
    re.compile(r"\blorem ipsum\b", re.I),
)

FORBIDDEN_ARTIFACTS = ("Nordman", "I need human review", "Traceback")

SECRET_PATTERNS = (
    re.compile(r"api\.telegram\.org", re.I),
    re.compile(r"duckdns\.org", re.I),
    re.compile(r"185\.252\.\d+", re.I),
    re.compile(r"supabase", re.I),
    re.compile(r"PLATFORM_TELEGRAM_BOT_TOKEN\s*=", re.I),
    re.compile(r"postgresql://[^@\s]+:[^@\s]+@", re.I),
    re.compile(r"\+375\d{9}"),
    re.compile(r"@[a-z0-9.-]+\.(ru|com|by)", re.I),
)

DEMO_PRODUCTS = (
    "Активатор клеток",
    "Активатор клеток PRO",
    "Вэнтун",
    "Magic Foherb",
    "Ба-Гуа",
    "Очки",
    "Стельки",
    "Палантин",
    "Магнитный пояс",
    "Линчжи",
    "Лювэй",
    "Соевый пептид",
    "Эликсир Фохоу",
    "Эликсир Саньцин",
    "Эликсир 3 Драгоценности",
    "Роза Фохоу",
)

SAFETY_DIAGNOSIS_WORDS = ("диагноз", "лечени", "излечен", "гарант", "схема лечения", "назнач")

RELEASE_GATE_CASE_IDS = (
    "CAT-001",
    "CPR-001",
    "ALI-001",
    "ALI-002",
    "FUP-001",
    "FUP-002",
    "MED-001",
    "MED-011",
    "CMP-001",
    "CRT-001",
    "BUS-001",
    "SAF-001",
    "SAF-003",
    "SAF-004",
    "FUP-031",
    "CPR-004",
    "CAT-007",
    "ALI-037",
    "MED-026",
    "CRT-021",
)


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Corpus not found: {path}")
    cases: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_no}: expected object, got {type(row).__name__}")
            cases.append(row)
    return cases


def _text_blob(row: dict) -> str:
    parts = [str(row.get("input", ""))]
    parts.extend(str(x) for x in row.get("context_before") or [])
    parts.extend(str(x) for x in row.get("must_contain") or [])
    parts.extend(str(x) for x in row.get("must_not_contain") or [])
    if row.get("expected_product"):
        parts.append(str(row["expected_product"]))
    return "\n".join(parts)


def validate_cases(cases: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(cases) < 250:
        errors.append(f"Corpus has {len(cases)} cases; minimum is 250")

    seen_ids: set[str] = set()
    seen_inputs: set[tuple[str, tuple[str, ...]]] = set()
    group_counts: Counter[str] = Counter()

    for row in cases:
        case_id = row.get("case_id")
        if not case_id or not str(case_id).strip():
            errors.append("Case missing case_id")
            continue
        if case_id in seen_ids:
            errors.append(f"Duplicate case_id: {case_id}")
        seen_ids.add(case_id)

        for field in REQUIRED_FIELDS:
            if field not in row:
                errors.append(f"{case_id}: missing field {field}")

        group = row.get("group")
        if group not in ALLOWED_GROUPS:
            errors.append(f"{case_id}: invalid group {group!r}")

        priority = row.get("priority")
        if priority not in ALLOWED_PRIORITIES:
            errors.append(f"{case_id}: invalid priority {priority!r}")

        input_text = str(row.get("input", "")).strip()
        if len(input_text) < 2:
            errors.append(f"{case_id}: empty input")

        ctx = row.get("context_before")
        if not isinstance(ctx, list):
            errors.append(f"{case_id}: context_before must be list")
        elif group == "followup_context" and len(ctx) == 0:
            errors.append(f"{case_id}: followup_context requires context_before")

        must_contain = row.get("must_contain")
        must_not = row.get("must_not_contain")
        if not isinstance(must_contain, list) or not must_contain:
            errors.append(f"{case_id}: must_contain must be non-empty list")
        if not isinstance(must_not, list) or not must_not:
            errors.append(f"{case_id}: must_not_contain must be non-empty list")

        if priority == "P0":
            if not must_contain or not must_not:
                errors.append(f"{case_id}: P0 requires must_contain and must_not_contain")

        blob = _text_blob(row)
        for pattern in PLACEHOLDER_PATTERNS:
            if pattern.search(blob):
                errors.append(f"{case_id}: placeholder text matched {pattern.pattern}")
        for artifact in FORBIDDEN_ARTIFACTS:
            if artifact.lower() in blob.lower() and artifact not in (must_not or []):
                # allowed inside must_not_contain definitions only as explicit guard
                if artifact not in str(must_not):
                    errors.append(f"{case_id}: forbidden artifact {artifact!r} in case text")

        for secret_re in SECRET_PATTERNS:
            if secret_re.search(blob):
                errors.append(f"{case_id}: forbidden secret/prod pattern {secret_re.pattern}")

        dup_key = (input_text.casefold(), tuple(str(x).casefold() for x in (ctx or [])))
        if dup_key in seen_inputs:
            errors.append(f"{case_id}: duplicate input+context")
        seen_inputs.add(dup_key)

        if group == "safety_and_clarification":
            mode = row.get("expected_mode")
            if mode not in {"clarification", "knowledge_gap"}:
                errors.append(f"{case_id}: safety case expected clarification/knowledge_gap, got {mode}")
            if any(word in input_text.lower() for word in SAFETY_DIAGNOSIS_WORDS):
                joined_not = " ".join(str(x).lower() for x in (must_not or []))
                if not any(word in joined_not for word in ("леч", "диагноз", "гарант", "схема", "назнач", "излечен")):
                    errors.append(f"{case_id}: safety medical case missing must_not_contain guard")

        group_counts[group] += 1

    for group, minimum in GROUP_MINIMUMS.items():
        count = group_counts.get(group, 0)
        if count < minimum:
            errors.append(f"Group {group}: {count} cases, need >= {minimum}")

    missing_release = [cid for cid in RELEASE_GATE_CASE_IDS if cid not in seen_ids]
    if missing_release:
        errors.append(f"Release gate cases missing: {', '.join(missing_release)}")

    return errors


def summarize_cases(cases: list[dict]) -> dict:
    by_group: Counter[str] = Counter()
    by_priority: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    by_product: Counter[str] = Counter()
    by_mode: Counter[str] = Counter()

    for row in cases:
        by_group[row["group"]] += 1
        by_priority[row["priority"]] += 1
        by_source[row.get("source") or "unknown"] += 1
        by_mode[row.get("expected_mode") or "unknown"] += 1
        product = row.get("expected_product")
        if product:
            by_product[str(product)] += 1

    return {
        "total": len(cases),
        "by_group": dict(sorted(by_group.items())),
        "by_priority": dict(sorted(by_priority.items())),
        "by_source": dict(sorted(by_source.items())),
        "by_product": dict(sorted(by_product.items(), key=lambda x: (-x[1], x[0]))),
        "by_mode": dict(sorted(by_mode.items(), key=lambda x: (-x[1], x[0]))),
    }


def write_report(cases: list[dict], summary: dict, errors: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WHIEDA Regression Corpus Report",
        "",
        f"**Generated:** {date.today().isoformat()}",
        f"**Corpus:** `{CASES_PATH.relative_to(ROOT).as_posix()}`",
        "",
        "## Summary",
        "",
        f"- Total cases: **{summary['total']}**",
        f"- Validation: **{'PASS' if not errors else 'FAIL'}**",
        "",
        "### By group",
        "",
        "| Group | Count | Minimum |",
        "|---|---:|---:|",
    ]
    for group, minimum in GROUP_MINIMUMS.items():
        count = summary["by_group"].get(group, 0)
        status = "OK" if count >= minimum else "GAP"
        lines.append(f"| `{group}` | {count} | {minimum} ({status}) |")

    lines.extend(["", "### By priority", ""])
    for pri, count in summary["by_priority"].items():
        lines.append(f"- {pri}: {count}")

    lines.extend(["", "### Top products", ""])
    for product, count in list(summary["by_product"].items())[:20]:
        lines.append(f"- {product}: {count}")

    lines.extend(["", "## Release gate (20 cases)", ""])
    for cid in RELEASE_GATE_CASE_IDS:
        lines.append(f"- `{cid}`")

    lines.extend(["", "## Known gaps", ""])
    gaps = [
        "Corpus validates expectations offline; live answer execution is separate.",
        "Legacy-only flows (review commands, sheet sync) are not in this corpus.",
        "Promotion/community cases depend on live content freshness — marked P2.",
        "Photo/video/certificate cases assert mode contract, not binary media bytes.",
    ]
    for gap in gaps:
        lines.append(f"- {gap}")

    if errors:
        lines.extend(["", "## Validation errors", ""])
        for err in errors:
            lines.append(f"- {err}")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_validate(cases: list[dict]) -> int:
    errors = validate_cases(cases)
    if errors:
        print("VALIDATION: FAIL", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"VALIDATION: PASS ({len(cases)} cases)")
    return 0


def cmd_summary(cases: list[dict]) -> int:
    summary = summarize_cases(cases)
    print(f"Total cases: {summary['total']}")
    print("\nBy group:")
    for group, count in summary["by_group"].items():
        print(f"  {group}: {count}")
    print("\nBy priority:")
    for pri, count in summary["by_priority"].items():
        print(f"  {pri}: {count}")
    print("\nBy source:")
    for src, count in summary["by_source"].items():
        print(f"  {src}: {count}")
    print("\nTop products:")
    for product, count in list(summary["by_product"].items())[:15]:
        print(f"  {product}: {count}")
    return 0


def cmd_report(cases: list[dict]) -> int:
    errors = validate_cases(cases)
    summary = summarize_cases(cases)
    write_report(cases, summary, errors)
    print(f"Report written: {REPORT_PATH}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA regression corpus QA (offline)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true")
    group.add_argument("--summary", action="store_true")
    group.add_argument("--report", action="store_true")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.validate:
        return cmd_validate(cases)
    if args.summary:
        return cmd_summary(cases)
    if args.report:
        return cmd_report(cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
