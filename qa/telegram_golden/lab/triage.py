"""Golden failure taxonomy, triage reports, and classification registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FAILURE_CLASSIFICATIONS = frozenset(
    {
        "core_bug",
        "fixture_data_gap",
        "surface_mismatch",
        "expectation_mismatch",
        "policy_decision_required",
        "dependency_failure",
    }
)

TRIAGE_INPUT_CASE_IDS = (
    "GOLD-SMOKE-P0-001",
    "GOLD-NBZ-NBZ-P0-006",
    "GOLD-NBZ-NBZ-P0-014",
    "GOLD-NBZ-NBZ-P1-022",
    "GOLD-NBZ-NBZ-P1-023",
    "GOLD-EXP-TG-CART-CALC-REMOVE-T2",
    "GOLD-EXP-TG-PRES-ACTIVATOR-CHOICE-T2",
    "GOLD-EXP-TG-SVC-CATALOG-SHOW-T1",
    "GOLD-EXP-TG-SVC-ACTIVATOR-PRO-T2",
    "GOLD-BUS-EXTRA-01",
    "GOLD-BUS-EXTRA-03",
    "GOLD-BUS-EXTRA-04",
    "GOLD-SMOKE-FLOW-ctx-pro-T3",
    "GOLD-CONV-CONV-F34-events-T1",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_classification_record(case_id: str, record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    classification = str(record.get("classification") or "")
    if classification not in FAILURE_CLASSIFICATIONS:
        errors.append(f"{case_id}: invalid classification {classification!r}")
    if classification == "expectation_mismatch":
        for key in ("evidence", "source_ref", "owner_decision"):
            if not str(record.get(key) or "").strip():
                errors.append(f"{case_id}: expectation_mismatch missing {key}")
        if str(record.get("owner_decision") or "") != "pending":
            errors.append(f"{case_id}: expectation_mismatch requires owner_decision=pending")
    if classification == "core_bug":
        if not str(record.get("repro") or "").strip():
            errors.append(f"{case_id}: core_bug missing repro")
    if classification == "surface_mismatch":
        if not str(record.get("telegram_handler_ref") or record.get("repro") or "").strip():
            errors.append(f"{case_id}: surface_mismatch missing handler/repro ref")
    return errors


def validate_triage_registry(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"missing triage registry: {path}"]
    data = load_json(path)
    classifications = data.get("classifications") or {}
    for case_id in TRIAGE_INPUT_CASE_IDS:
        if case_id not in classifications:
            errors.append(f"missing classification for {case_id}")
    for case_id, record in classifications.items():
        if not isinstance(record, dict):
            errors.append(f"{case_id}: classification record must be object")
            continue
        errors.extend(validate_classification_record(case_id, record))
    return errors


def validate_policy_registry(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"missing policy registry: {path}"]
    data = load_json(path)
    decisions = data.get("decisions") or []
    if len(decisions) < 7:
        errors.append(f"expected at least 7 policy decisions, got {len(decisions)}")
    for item in decisions:
        if str(item.get("status") or "") != "pending_owner":
            errors.append(f"{item.get('policy_id')}: status must be pending_owner")
    return errors


def _result_id(row: dict[str, Any]) -> str:
    return str(row.get("case_id") or row.get("fixture_id") or f"{row.get('flow_id')}:{row.get('turn')}")


def _lookup_classification(result_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    return (registry.get("classifications") or {}).get(result_id)


def _load_case_surfaces(tg_root: Path) -> dict[str, str]:
    surfaces: dict[str, str] = {}
    for name in ("whieda_telegram_golden_cases_v1.jsonl", "whieda_telegram_golden_flows_v1.jsonl"):
        path = tg_root / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("case_id"):
                surfaces[str(row["case_id"])] = str(row.get("execution_surface") or "advisor_http")
            for turn in row.get("turns") or []:
                cid = str(turn.get("case_id") or "")
                if cid:
                    surfaces[cid] = str(turn.get("execution_surface") or "advisor_http")
    return surfaces


def build_triage_payload(
    *,
    http_report: dict[str, Any],
    classifications_path: Path,
    policy_path: Path,
    corpus_root: Path | None = None,
) -> dict[str, Any]:
    registry = load_json(classifications_path)
    policy = load_json(policy_path)
    case_surfaces = _load_case_surfaces(corpus_root or classifications_path.parent)
    run_id = str(http_report.get("run_id") or "unknown")
    results = http_report.get("results") or []
    summary = http_report.get("summary") or {}
    counters = summary.get("counters") or {}

    by_surface: dict[str, dict[str, int]] = {}
    by_classification: dict[str, int] = {name: 0 for name in sorted(FAILURE_CLASSIFICATIONS)}
    rows: list[dict[str, Any]] = []

    for row in results:
        status = str(row.get("status") or "")
        row_id = _result_id(row)
        surface = str(row.get("execution_surface") or case_surfaces.get(row_id) or "advisor_http")
        by_surface.setdefault(surface, {"executed": 0, "pass": 0, "fail": 0, "skip": 0, "not_run": 0})
        bucket = by_surface[surface]
        if status == "NOT_RUN_DEPENDENCY":
            bucket["not_run"] += 1
        elif status == "SKIP_SURFACE":
            bucket["skip"] += 1
        else:
            bucket["executed"] += 1
            if status in {"PASS", "NEGATIVE_PASS"}:
                bucket["pass"] += 1
            else:
                bucket["fail"] += 1

        classification = None
        class_record = None
        if status not in {"PASS", "NEGATIVE_PASS", "NOT_RUN_DEPENDENCY"}:
            class_record = _lookup_classification(row_id, registry)
            if class_record:
                classification = class_record.get("classification")
                if classification in by_classification:
                    by_classification[classification] += 1
        rows.append(
            {
                "id": row_id,
                "status": status,
                "priority": row.get("priority"),
                "execution_surface": surface,
                "classification": classification,
                "policy_id": (class_record or {}).get("policy_id"),
                "expected_mode": row.get("expected_mode"),
                "answer_mode": row.get("answer_mode"),
                "reason": row.get("reason"),
                "answer_preview": row.get("answer_preview"),
                "turn_role": row.get("turn_role"),
            }
        )

    core_backlog = [
        {
            "id": case_id,
            **record,
        }
        for case_id, record in (registry.get("classifications") or {}).items()
        if record.get("classification") == "core_bug"
    ]
    pending_owner = [item for item in policy.get("decisions") or [] if item.get("status") == "pending_owner"]

    negative_rows = [r for r in results if r.get("kind") == "negative"]
    negative_gate = {
        "executed": len(negative_rows),
        "pass": sum(1 for r in negative_rows if r.get("status") == "NEGATIVE_PASS"),
        "fail": sum(1 for r in negative_rows if r.get("status") == "NEGATIVE_FAIL"),
    }

    return {
        "run_id": run_id,
        "source_http_report_run_id": run_id,
        "status": http_report.get("status"),
        "run_phase": http_report.get("run_phase"),
        "target": http_report.get("target"),
        "fixture_parity": http_report.get("fixture_parity"),
        "summary": {
            "http_counters": counters,
            "by_execution_surface": by_surface,
            "by_classification": by_classification,
            "negative_safety_gate": negative_gate,
        },
        "rows": sorted(rows, key=lambda r: (str(r.get("priority") or "P9"), str(r.get("id")))),
        "core_bug_backlog": core_backlog,
        "pending_owner_decisions": pending_owner,
        "registry_version": registry.get("version"),
        "policy_version": policy.get("version"),
    }


def render_triage_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    counters = summary.get("http_counters") or {}
    by_surface = summary.get("by_execution_surface") or {}
    by_class = summary.get("by_classification") or {}
    negative = summary.get("negative_safety_gate") or {}
    fixture = payload.get("fixture_parity") or {}
    target = payload.get("target") or {}

    lines = [
        "# Golden Triage Report",
        "",
        f"- **Run ID:** `{payload.get('run_id')}`",
        f"- **HTTP phase:** `{payload.get('run_phase')}`",
        f"- **Target:** `{target.get('base_url')}{target.get('advisor_path')}`",
        "",
    ]
    if fixture:
        lines.extend(
            [
                "## Fixture parity",
                "",
                f"- Snapshot: `{fixture.get('generated_from')}`",
                f"- Fixture SQL sha256: `{fixture.get('fixture_sql_sha256')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## HTTP counts",
            "",
            f"- Executed: {counters.get('executed', 0)}",
            f"- Pass: {counters.get('pass', 0)}",
            f"- Fail: {counters.get('fail', 0)}",
            f"- Skip surface: {counters.get('skip_surface', 0)}",
            f"- Not run (dependency): {counters.get('not_run_dependency', 0)}",
            "",
            "## By execution_surface",
            "",
        ]
    )
    for surface, stats in sorted(by_surface.items()):
        lines.append(f"- `{surface}`: {stats}")
    lines.extend(["", "## By classification", ""])
    for name, count in sorted(by_class.items()):
        if count:
            lines.append(f"- `{name}`: {count}")
    lines.extend(
        [
            "",
            "## Negative safety gate",
            "",
            f"- pass/fail: {negative.get('pass', 0)}/{negative.get('fail', 0)}",
            "",
            "## Core bug backlog",
            "",
        ]
    )
    backlog = payload.get("core_bug_backlog") or []
    if not backlog:
        lines.append("_None registered_")
    else:
        for item in backlog:
            lines.append(f"- `{item.get('id')}` — {item.get('evidence')} repro: `{item.get('repro')}`")
    lines.extend(["", "## Pending owner decisions", ""])
    for item in payload.get("pending_owner_decisions") or []:
        lines.append(f"- `{item.get('policy_id')}` ({item.get('status')}): {item.get('question')}")
    lines.extend(["", "## Case table (P0 first)", ""])
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    for row in sorted(payload.get("rows") or [], key=lambda r: (priority_order.get(str(r.get("priority")), 9), r.get("id"))):
        if row.get("status") in {"PASS", "NEGATIVE_PASS"}:
            continue
        lines.append(
            f"- `{row.get('id')}` [{row.get('status')}] surface={row.get('execution_surface')} "
            f"class={row.get('classification') or 'unclassified'}: {row.get('reason')}"
        )
    lines.append("")
    return "\n".join(lines)


def write_triage_reports(payload: dict[str, Any], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or "unknown")
    json_path = reports_dir / f"GOLDEN_TRIAGE_{run_id}.json"
    md_path = reports_dir / f"GOLDEN_TRIAGE_{run_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_triage_markdown(payload), encoding="utf-8")
    latest = reports_dir / "triage_latest.json"
    latest.write_text(json.dumps({"run_id": run_id, "json": json_path.name, "md": md_path.name}, indent=2), encoding="utf-8")
    return json_path, md_path
