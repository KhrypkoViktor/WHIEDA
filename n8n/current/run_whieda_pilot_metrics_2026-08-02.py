"""Pilot metrics aggregator for 2-week partner pilot (no PII in public export)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from whieda_runtime_pg_bootstrap import ensure_pgpassword
from whieda_runtime_read import query_rows

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat() / "WHIEDA_pilot_metrics.json"


def main() -> None:
    ensure_pgpassword()
    partners = query_rows(
        """
        SELECT count(*)::int AS total,
               count(*) FILTER (WHERE enabled = true)::int AS enabled,
               count(*) FILTER (
                 WHERE enabled = true
                   AND coalesce(public_profile->>'focus_group', 'false') = 'true'
               )::int AS focus_group
        FROM referral_profiles
        WHERE tenant_id = 'whieda'
        """
    )[0]

    leads = query_rows(
        """
        SELECT count(*)::int AS total,
               count(*) FILTER (WHERE created_at >= now() - interval '7 days')::int AS last_7d,
               count(*) FILTER (WHERE status = 'new')::int AS status_new,
               count(*) FILTER (WHERE status IN ('in_progress', 'contacted', 'qualified'))::int AS status_active
        FROM website_leads
        WHERE tenant_id = 'whieda'
        """
    )[0]

    delivery = query_rows(
        """
        SELECT count(*)::int AS attempts,
               count(*) FILTER (WHERE status = 'sent')::int AS sent,
               count(*) FILTER (WHERE status = 'failed')::int AS failed,
               count(*) FILTER (WHERE status = 'pending')::int AS pending
        FROM lead_delivery_attempts
        WHERE created_at >= now() - interval '14 days'
        """
    )[0]

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "window_days": 14,
        "partners": partners,
        "leads": leads,
        "delivery": delivery,
        "derived": {
            "activated_partners": partners.get("focus_group", 0),
            "lead_submit_rate_7d": leads.get("last_7d", 0),
            "delivery_success_rate": round(
                (delivery.get("sent", 0) / delivery.get("attempts", 1)) * 100, 2
            )
            if delivery.get("attempts")
            else None,
        },
        "publication": "aggregate_only_no_pii",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(OUT), "derived": report["derived"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
