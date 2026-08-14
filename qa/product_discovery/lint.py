"""Lint rules for product discovery map TSV and policy consistency."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from constants import CONFIDENCE, MANDATORY_GROUPS, STATUSES, TSV_COLUMNS
from snapshot_loader import SnapshotBundle, normalize_phrase

BARE_TOKENS = frozenset({"pro", "magic", "массажер"})
POLICY_SKU_RE = re.compile(
    r"\b(?:EU-N\d{6}-\d{2}|E\d{3}-\d{2}|[A-Z]\d{3}(?:-\d{2})?|[A-Z]{1,2}\d{3}-\d{2})\b"
)


@dataclass
class LintIssue:
    code: str
    message: str
    row_index: int | None = None


def lint_rows(rows: list[dict[str, str]], bundle: SnapshotBundle) -> list[LintIssue]:
    issues: list[LintIssue] = []
    known_skus = set(bundle.products.keys())

    for idx, row in enumerate(rows, start=2):
        for col in TSV_COLUMNS:
            if col not in row:
                issues.append(LintIssue("missing_column", f"missing column {col}", idx))

        status = str(row.get("status") or "")
        confidence = str(row.get("confidence") or "")
        sku = str(row.get("sku") or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        phrase = str(row.get("phrase") or "").strip()
        notes = str(row.get("notes") or "").strip()
        rank = str(row.get("candidate_rank") or "")

        if status not in STATUSES:
            issues.append(LintIssue("invalid_status", f"invalid status {status!r} for phrase {phrase!r}", idx))
        if confidence not in CONFIDENCE:
            issues.append(LintIssue("invalid_confidence", f"invalid confidence {confidence!r} for phrase {phrase!r}", idx))
        if not evidence:
            issues.append(LintIssue("missing_evidence", f"missing evidence for phrase {phrase!r}", idx))
        if sku and sku not in known_skus:
            issues.append(LintIssue("unknown_sku", f"unknown sku {sku!r} for phrase {phrase!r}", idx))
        if status == "do_not_resolve" and sku:
            issues.append(LintIssue("do_not_resolve_with_sku", f"do_not_resolve must not carry sku for {phrase!r}", idx))
        if phrase == "подарок" and status == "ready_for_core_review":
            issues.append(
                LintIssue(
                    "gift_task_selection_conflict",
                    "подарок must not be ready_for_core_review; first turn is task_selection",
                    idx,
                )
            )
        if phrase in BARE_TOKENS and status == "ready_for_core_review" and confidence in {"low", "medium"}:
            if "direct-safe" not in notes.casefold() and "show_choices" not in notes.casefold():
                issues.append(
                    LintIssue(
                        "bare_token_direct_unsafe",
                        f"{phrase!r} is a bare token with {confidence} confidence and no direct-safe note",
                        idx,
                    )
                )
        try:
            rank_num = int(rank)
            if rank_num < 1 or rank_num > 3:
                issues.append(LintIssue("invalid_rank", f"candidate_rank out of range for {phrase!r}", idx))
        except ValueError:
            issues.append(LintIssue("invalid_rank", f"candidate_rank not int for {phrase!r}", idx))

    by_phrase: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_phrase[normalize_phrase(row.get("normalized_phrase") or row.get("phrase") or "")].append(row)

    for norm, group_rows in by_phrase.items():
        if len(group_rows) > 3:
            issues.append(LintIssue("too_many_candidates", f"more than 3 candidates for {norm!r}"))
        seen_sku: set[str] = set()
        for row in group_rows:
            sku = str(row.get("sku") or "").strip()
            if sku:
                if sku in seen_sku:
                    issues.append(LintIssue("duplicate_phrase_sku", f"duplicate ({norm!r}, {sku!r})"))
                seen_sku.add(sku)

        generic_rows = [r for r in group_rows if str(r.get("status") or "") == "generic_category"]
        sku_rows = [r for r in generic_rows if str(r.get("sku") or "").strip()]
        if len(generic_rows) == 1 and len(sku_rows) == 1:
            notes = str(sku_rows[0].get("notes") or "").casefold()
            if not any(x in notes for x in ("still generic", "single sku", "hlr", "show_choices", "task_selection", "intentional")):
                issues.append(
                    LintIssue(
                        "generic_single_sku_unexplained",
                        f"generic_category with one SKU for {norm!r} needs note explaining why still generic",
                    )
                )

    present = {str(r.get("phrase") or "") for r in rows}
    for group, phrases in MANDATORY_GROUPS.items():
        for phrase in phrases:
            if phrase not in present:
                issues.append(LintIssue("missing_mandatory_phrase", f"missing mandatory phrase {phrase!r} in group {group}"))

    for color in ("красный", "зелёный", "синий"):
        color_rows = [r for r in rows if str(r.get("phrase") or "") == color]
        if not color_rows:
            issues.append(LintIssue("missing_color_guard", f"missing color guard row for {color!r}"))
        elif any(str(r.get("status") or "") != "do_not_resolve" for r in color_rows):
            issues.append(LintIssue("color_not_guarded", f"{color!r} must be do_not_resolve"))

    return issues


def lint_policy_skus(policy_path: Path, rows: list[dict[str, str]]) -> list[LintIssue]:
    if not policy_path.is_file():
        return [LintIssue("missing_policy", f"missing policy file {policy_path}")]
    map_skus = {str(r.get("sku") or "").strip() for r in rows if str(r.get("sku") or "").strip()}
    text = policy_path.read_text(encoding="utf-8")
    issues: list[LintIssue] = []
    for sku in sorted(set(POLICY_SKU_RE.findall(text))):
        if sku not in map_skus:
            issues.append(
                LintIssue(
                    "policy_sku_not_in_map",
                    f"policy mentions SKU {sku!r} which is not present in discovery map TSV",
                )
            )
    return issues
