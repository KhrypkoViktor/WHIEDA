"""Read-only WHIEDA operational health check.

The report is deliberately small and factual: it checks whether the advisor is
active, whether the Sheet -> SQL sync is fresh, whether the latest P0 passed,
and whether recent advisor executions contain errors. It never changes n8n,
Sheets, runtime SQL, or Telegram.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EXPORT_ROOT = BASE_DIR.parent / "live-exports" / datetime.now().date().isoformat()
ADVISOR_WORKFLOW_ID = "advisor-whieda-phase1"
SYNC_WORKFLOW_ID = "9roEvXNsDpnwqjzH"
P0_REPORT = EXPORT_ROOT / "WHIEDA_live_p0_smoke_report.json"


def load_publisher():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def recent_executions(session, base_url: str, workflow_id: str, limit: int = 100) -> list[dict]:
    response = session.get(
        f"{base_url}/rest/executions?limit={limit}&workflowId={workflow_id}",
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json().get("data", {})
    rows = payload.get("results", payload if isinstance(payload, list) else [])
    # Current n8n REST may return a mixed page despite workflowId in the URL.
    # Filter locally so advisor and sync health can never be mixed.
    return [row for row in rows if row.get("workflowId") == workflow_id]


def main() -> None:
    publisher = load_publisher()
    session = publisher.login_session()
    advisor = session.get(
        f"{publisher.BASE_URL}/rest/workflows/{ADVISOR_WORKFLOW_ID}", verify=False, timeout=30
    )
    advisor.raise_for_status()
    advisor_data = advisor.json().get("data", advisor.json())
    advisor_runs = recent_executions(session, publisher.BASE_URL, ADVISOR_WORKFLOW_ID)
    # n8n may ignore workflowId server-side. A P0 run can fill the newest 100
    # records, so scan a wider page and filter locally for the sync workflow.
    sync_runs = recent_executions(session, publisher.BASE_URL, SYNC_WORKFLOW_ID, limit=500)
    now = datetime.now(timezone.utc)
    latest_sync = sync_runs[0] if sync_runs else None
    latest_sync_time = parse_time((latest_sync or {}).get("stoppedAt") or (latest_sync or {}).get("startedAt"))
    sync_age_minutes = round((now - latest_sync_time.astimezone(timezone.utc)).total_seconds() / 60, 1) if latest_sync_time else None

    p0 = None
    if P0_REPORT.exists():
        p0 = json.loads(P0_REPORT.read_text(encoding="utf-8"))
    p0_meta = (p0 or {}).get("meta", {})
    p0_ok = bool(p0_meta.get("cases_total")) and p0_meta.get("failed", 1) == 0

    errors = [item for item in advisor_runs if item.get("status") == "error"]
    critical = []
    if not advisor_data.get("active"):
        critical.append("advisor_workflow_inactive")
    if not latest_sync or latest_sync.get("status") != "success":
        critical.append("sync_not_success")
    if sync_age_minutes is None or sync_age_minutes > 30:
        critical.append("sync_stale_over_30m")
    if not p0_ok:
        critical.append("p0_not_green")

    status = "green" if not critical else "red"
    report = {
        "checked_at": now.isoformat(),
        "status": status,
        "critical_alerts": critical,
        "advisor": {
            "workflow_id": ADVISOR_WORKFLOW_ID,
            "active": bool(advisor_data.get("active")),
            "recent_execution_count": len(advisor_runs),
            "recent_error_count": len(errors),
            "latest_execution": {
                "id": advisor_runs[0].get("id"),
                "status": advisor_runs[0].get("status"),
                "started_at": advisor_runs[0].get("startedAt"),
            } if advisor_runs else None,
        },
        "sync": {
            "workflow_id": SYNC_WORKFLOW_ID,
            "latest_execution": {
                "id": latest_sync.get("id"),
                "status": latest_sync.get("status"),
                "started_at": latest_sync.get("startedAt"),
                "stopped_at": latest_sync.get("stoppedAt"),
            } if latest_sync else None,
            "age_minutes": sync_age_minutes,
        },
        "p0": {
            "report_path": str(P0_REPORT),
            "cases_total": p0_meta.get("cases_total"),
            "passed": p0_meta.get("passed"),
            "failed": p0_meta.get("failed"),
            "pass_rate": p0_meta.get("pass_rate"),
        },
    }
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_path = EXPORT_ROOT / "WHIEDA_operational_health.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "status": status, "critical_alerts": critical}, ensure_ascii=False))


if __name__ == "__main__":
    main()
