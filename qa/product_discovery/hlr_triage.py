"""Build HLR discovery triage from live HTTP acceptance report."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT / "qa" / "human_language_rails" / "reports"
CORPUS = ROOT / "qa" / "human_language_rails" / "whieda_human_language_rails_v1.jsonl"

MAP_PHRASES = {
    "активатор",
    "паста",
    "пояс",
    "красный",
    "эликсир",
    "стельки",
    "очки",
    "маска",
    "гель",
    "шампунь",
    "бад",
    "кофе",
    "капсулы",
    "чай",
    "линчжи",
    "прокладки",
    "pro",
    "пептид",
    "набор",
    "подарок",
    "нужен подарок",
    "зеленый эликсир",
    "зелёный эликсир",
    "синий эликсир",
    "красный эликсир",
    "цена",
    "цена?",
    "спирулина",
}


def find_latest_hlr_report(reports_dir: Path = REPORTS_DIR) -> Path | None:
    latest = reports_dir / "latest.json"
    if latest.is_file():
        meta = json.loads(latest.read_text(encoding="utf-8"))
        candidate = reports_dir / str(meta.get("path") or "")
        if candidate.is_file():
            return candidate
    files = sorted(reports_dir.glob("HLR_HTTP_REPORT_*.json"), reverse=True)
    return files[0] if files else None


def load_corpus_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not CORPUS.is_file():
        return index
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row.get("case_id") or "")
        if case_id:
            index[case_id] = row
    return index


def classify_failure(failure: dict[str, Any], corpus_row: dict[str, Any] | None) -> str:
    user_text = str((corpus_row or {}).get("user_text") or "").casefold()
    expected_rail = str(failure.get("expected_rail") or "")
    answer_mode = str(failure.get("answer_mode") or "")
    reason = str(failure.get("reason") or "")

    if expected_rail == "task_selection" and any(x in user_text for x in ("подарок", "косметик", "подбер")):
        if answer_mode == "structured_card":
            return "core_resolver_gap"
        return "core_resolver_gap"

    if user_text in {"набор"} or "набор" in user_text and expected_rail == "product_choices":
        return "fixture_data_gap"

    if user_text in {"прокладки", "пептид"} and expected_rail == "product_choices":
        return "fixture_data_gap"

    if expected_rail == "direct_answer" and answer_mode == "clarification":
        return "core_resolver_gap"

    if expected_rail == "product_choices":
        if "missing_any ['уточн']" in reason and user_text.casefold() == "красный":
            return "corpus_expectation_gap"
        if answer_mode == "structured_card":
            return "core_resolver_gap"
        if answer_mode in {"knowledge_gap", "clarification"} and "missing_any" in reason:
            return "core_resolver_gap"

    if expected_rail == "universal_menu":
        return "core_resolver_gap"

    if expected_rail == "task_selection" and answer_mode == "structured_starter_basket":
        return "corpus_expectation_gap"

    return "core_resolver_gap"


def triage_bucket(expected_rail: str, user_text: str) -> str:
    text = user_text.casefold()
    if expected_rail == "product_choices":
        return "product_choices"
    if expected_rail == "direct_answer":
        return "direct_product"
    if expected_rail == "task_selection" and any(x in text for x in ("подарок", "косметик")):
        return "generic_vs_task_selection"
    if expected_rail == "task_selection":
        return "task_selection_other"
    if expected_rail == "universal_menu":
        return "universal_menu"
    return "other"


def build_triage_markdown(report_path: Path, *, map_phrases: set[str] | None = None) -> str:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    run_id = str(payload.get("run_id") or report_path.stem.replace("HLR_HTTP_REPORT_", ""))
    corpus = load_corpus_index()
    failures = list((payload.get("summary") or {}).get("failures") or [])
    map_phrases = map_phrases or MAP_PHRASES

    discovery_failures: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for failure in failures:
        case_id = str(failure.get("case_id") or "")
        corpus_row = corpus.get(case_id)
        user_text = str((corpus_row or {}).get("user_text") or "")
        expected_rail = str(failure.get("expected_rail") or "")
        bucket = triage_bucket(expected_rail, user_text)
        if bucket in {"product_choices", "direct_product", "generic_vs_task_selection"}:
            discovery_failures.append((bucket, failure, corpus_row))
        elif user_text.casefold() in {p.casefold() for p in map_phrases}:
            discovery_failures.append((bucket, failure, corpus_row))

    class_counts = Counter(
        classify_failure(failure, corpus_row) for _, failure, corpus_row in discovery_failures
    )

    lines = [
        "# HLR Discovery Triage V1",
        "",
        "Read-only triage from **live** Human Language Rails HTTP acceptance.",
        "",
        f"- HLR report: `{report_path.name}`",
        f"- Run ID: `{run_id}`",
        f"- Report status: `{payload.get('status')}`",
        f"- Discovery-relevant failures: {len(discovery_failures)} / {len(failures)} total live failures",
        "",
        "## Classification summary",
        "",
        "| classification | count |",
        "|---|---:|",
    ]
    for label in ("core_resolver_gap", "corpus_expectation_gap", "fixture_data_gap", "owner_decision"):
        if class_counts.get(label):
            lines.append(f"| `{label}` | {class_counts[label]} |")
    lines.extend(["", "## product_choices failures", ""])
    lines.append("| case_id | user_text | observed answer_mode | classification | reason |")
    lines.append("|---|---|---|---|---|")
    for bucket, failure, corpus_row in discovery_failures:
        if bucket != "product_choices":
            continue
        user_text = str((corpus_row or {}).get("user_text") or "")
        classification = classify_failure(failure, corpus_row)
        lines.append(
            f"| {failure.get('case_id')} | {user_text} | {failure.get('answer_mode')} | "
            f"{classification} | {failure.get('reason')} |"
        )

    lines.extend(["", "## direct_answer / typo product failures", ""])
    lines.append("| case_id | user_text | observed answer_mode | classification | reason |")
    lines.append("|---|---|---|---|---|")
    for bucket, failure, corpus_row in discovery_failures:
        if bucket != "direct_product":
            continue
        user_text = str((corpus_row or {}).get("user_text") or "")
        classification = classify_failure(failure, corpus_row)
        lines.append(
            f"| {failure.get('case_id')} | {user_text} | {failure.get('answer_mode')} | "
            f"{classification} | {failure.get('reason')} |"
        )

    lines.extend(["", "## task_selection vs product-card auto-open", ""])
    lines.append("| case_id | user_text | observed answer_mode | classification | reason |")
    lines.append("|---|---|---|---|---|")
    for bucket, failure, corpus_row in discovery_failures:
        if bucket not in {"generic_vs_task_selection", "task_selection_other"}:
            continue
        user_text = str((corpus_row or {}).get("user_text") or "")
        classification = classify_failure(failure, corpus_row)
        lines.append(
            f"| {failure.get('case_id')} | {user_text} | {failure.get('answer_mode')} | "
            f"{classification} | {failure.get('reason')} |"
        )

    lines.extend(
        [
            "",
            "## Notes for Core + discovery map",
            "",
            "- Live run shows Core often **auto-opens** `structured_card` where HLR expects `product_choices` "
            "(активатор, pro, стельки, очки, линчжи, прокладки).",
            "- Goal phrases («нужен подарок», budget/task flows) fail when Core opens a product card instead of "
            "`task_selection`. Map row «подарок» is `generic_category` and must not drive auto-open.",
            "- `красный` failure is `corpus_expectation_gap`: Core asks about red elixir specifically; corpus "
            "expects generic «уточн» wording — align after owner review.",
            "- `набор`, `прокладки`, `пептид` are **fixture_data_gap** for map V1 (not in mandatory phrases).",
            "",
            "Regenerate after new live run:",
            "",
            "```bash",
            "python qa/product_discovery/run_product_discovery_map.py --offline",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_triage(out_path: Path, report_path: Path | None = None) -> Path:
    report = report_path or find_latest_hlr_report()
    if report is None:
        out_path.write_text(
            "# HLR Discovery Triage V1\n\nNo live HLR HTTP reports found; corpus_proxy not generated.\n",
            encoding="utf-8",
        )
        return out_path
    out_path.write_text(build_triage_markdown(report), encoding="utf-8")
    return report
