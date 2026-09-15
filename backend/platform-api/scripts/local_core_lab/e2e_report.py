"""E2E run report builder for local Core lab."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

LabStatus = Literal["PASS", "FAIL", "NOT_RUN"]


@dataclass
class E2EReport:
    status: LabStatus
    started_at: str
    finished_at: str | None = None
    docker_version: str | None = None
    compose_version: str | None = None
    containers: list[str] = field(default_factory=list)
    sql_files: list[str] = field(default_factory=list)
    seed_file: str | None = None
    health: dict[str, Any] = field(default_factory=dict)
    check_target: dict[str, Any] = field(default_factory=dict)
    p0_acceptance: dict[str, Any] = field(default_factory=dict)
    preflight_smoke: dict[str, Any] = field(default_factory=dict)
    verify_e2e: dict[str, Any] = field(default_factory=dict)
    parity_run: dict[str, Any] = field(default_factory=dict)
    no_blind_zone_run: dict[str, Any] = field(default_factory=dict)
    no_blind_zone_db_proof: dict[str, Any] = field(default_factory=dict)
    cleanup_status: str = "NOT_RUN"
    steps: list[dict[str, Any]] = field(default_factory=list)
    failure_stage: str | None = None
    failure_message: str | None = None
    container_logs: dict[str, str] = field(default_factory=dict)
    report_paths: dict[str, str] = field(default_factory=dict)
    telegram_canary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_report(report: E2EReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.replace(":", "").replace("+00:00", "Z")
    json_path = reports_dir / f"LOCAL_CORE_E2E_{stamp}.json"
    md_path = reports_dir / f"LOCAL_CORE_E2E_{stamp}.md"
    latest_json = reports_dir / "latest_run.json"
    latest_md = reports_dir / "latest_run.md"

    payload = report.to_dict()
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")

    md_path.write_text(render_markdown(report), encoding="utf-8")
    latest_md.write_text(render_markdown(report), encoding="utf-8")

    report.report_paths = {
        "json": str(json_path),
        "md": str(md_path),
        "latest_json": str(latest_json),
        "latest_md": str(latest_md),
    }
    return json_path, md_path


def render_markdown(report: E2EReport) -> str:
    lines = [
        "# Local Core E2E Lab Run",
        "",
        f"- **Status:** `{report.status}`",
        f"- **Started:** {report.started_at}",
        f"- **Finished:** {report.finished_at or 'n/a'}",
        "",
        "## Environment",
        "",
        f"- Docker: {report.docker_version or 'n/a'}",
        f"- Compose: {report.compose_version or 'n/a'}",
        f"- Containers: {', '.join(report.containers) if report.containers else 'n/a'}",
        "",
        "## Schema",
        "",
        f"- SQL files ({len(report.sql_files)}):",
    ]
    for name in report.sql_files:
        lines.append(f"  - `{name}`")
    if report.seed_file:
        lines.append(f"- Seed: `{report.seed_file}`")

    lines.extend(["", "## Health", ""])
    if report.health:
        for key, value in report.health.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- not recorded")

    lines.extend(["", "## Acceptance", ""])
    ct = report.check_target.get("status", "NOT_RUN")
    lines.append(f"- check-target: `{ct}`")
    if report.check_target.get("errors"):
        for err in report.check_target["errors"]:
            lines.append(f"  - {err}")
    p0 = report.p0_acceptance.get("status", "NOT_RUN")
    lines.append(f"- P0 run: `{p0}`")
    if report.p0_acceptance.get("summary"):
        lines.append(f"  - summary: {report.p0_acceptance['summary']}")
    if report.p0_acceptance.get("report_path"):
        lines.append(f"  - report: `{report.p0_acceptance['report_path']}`")

    preflight = report.preflight_smoke or report.p0_acceptance
    if preflight.get("summary") or preflight.get("status") not in ("", "NOT_RUN", None):
        lines.extend(["", "## Preflight smoke", ""])
        lines.append(f"- status: `{preflight.get('status', 'NOT_RUN')}`")
        if preflight.get("summary"):
            lines.append(f"- summary: {preflight['summary']}")

    parity = report.parity_run
    if parity:
        lines.extend(["", "## Parity corpus", ""])
        lines.append(f"- status: `{parity.get('status', 'NOT_RUN')}`")
        if parity.get("summary_line"):
            lines.append(f"- summary: {parity['summary_line']}")
        if parity.get("not_run") is not None:
            lines.append(f"- not_run: {parity['not_run']}")
        if parity.get("timeout"):
            lines.append(f"- timeout: `{parity['timeout']}`")

    nbz = report.no_blind_zone_run
    if nbz:
        lines.extend(["", "## No blind zone corpus", ""])
        lines.append(f"- status: `{nbz.get('status', 'NOT_RUN')}`")
        if nbz.get("total_line"):
            lines.append(f"- summary: {nbz['total_line']}")
        if nbz.get("p0_line"):
            lines.append(f"- {nbz['p0_line']}")
        if nbz.get("p1_line"):
            lines.append(f"- {nbz['p1_line']}")

    nbz_db = report.no_blind_zone_db_proof
    if nbz_db:
        lines.extend(["", "## No blind zone DB proof", ""])
        lines.append(f"- status: `{nbz_db.get('status', 'NOT_RUN')}`")
        if nbz_db.get("summary"):
            lines.append(f"- summary: {nbz_db['summary']}")

    if report.telegram_canary:
        lines.extend(["", "## Tenant Telegram canary", ""])
        lines.append(f"- status: `{report.telegram_canary.get('status', 'NOT_RUN')}`")
        lines.append(f"- checks: {report.telegram_canary.get('checks', 'n/a')}")
        for case in report.telegram_canary.get("cases") or []:
            lines.append(
                f"  - `{case.get('case_id')}`: {case.get('status')} "
                f"inbox={case.get('inbox_actual', 'n/a')} "
                f"outbox={case.get('outbox_actual', 'n/a')}"
            )

    lines.extend(["", "## Cleanup", ""])
    lines.append(f"- status: `{report.cleanup_status}`")

    lines.extend(["", "## verify_local_core_e2e", ""])
    ve = report.verify_e2e.get("status", "NOT_RUN")
    lines.append(f"- status: `{ve}`")
    for check in report.verify_e2e.get("checks") or []:
        lines.append(f"  - [{check.get('status')}] {check.get('name')}: {check.get('message')}")

    if report.failure_stage:
        lines.extend(
            [
                "",
                "## Failure",
                "",
                f"- Stage: **{report.failure_stage}**",
                f"- Message: {report.failure_message or 'n/a'}",
            ]
        )

    if report.container_logs:
        lines.extend(["", "## Container logs (tail)", ""])
        for name, log in report.container_logs.items():
            lines.append(f"### {name}")
            lines.append("```")
            lines.append(log or "(empty)")
            lines.append("```")

    if report.steps:
        lines.extend(["", "## Steps", ""])
        for step in report.steps:
            status = "OK" if step.get("returncode") == 0 else "FAIL"
            lines.append(f"- [{status}] {step.get('name')}: exit {step.get('returncode')}")

    return "\n".join(lines) + "\n"
