"""Document root cause for site bot 'temporarily unavailable' incident (2026-08-01/02)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat() / "WHIEDA_site_bot_incident_2026-08-02.json"

REPORT = {
    "incident_id": "WHIEDA-2026-08-01-site-bot-unavailable",
    "window": {"first_seen": "2026-08-01T18:00:00+03:00", "resolved": "2026-08-02T12:00:00+03:00"},
    "symptom": "Site advisor widget returned 'временно недоступен' while Telegram path degraded",
    "chain": [
        "wwc.best widget -> nginx proxy -> /webhook/wwc-advisor-public-v1",
        "n8n WHIEDA Public Advisor workflow",
        "Code: Structured Sheet Lookup / Resource Lookup",
        "Supabase advisor runtime",
    ],
    "root_causes": [
        {
            "id": "RC1",
            "title": "Website envelope merge referenced Telegram-only node",
            "detail": (
                "Code: Structured Sheet Lookup and Code: Structured Resource Lookup read "
                "$('Code: Merge Envelope + Session') which is not executed on the website API path. "
                "Execution stopped before answer assembly, yielding empty/error response surfaced as unavailable."
            ),
            "fix": "publish_fix_website_api_structured_lookup_2026-08-01.py + structured_sheet_lookup_current.js fallback to Merge Website API Session",
            "commit": "383f53c",
        },
        {
            "id": "RC2",
            "title": "TEMP workflow sprawl overloaded n8n",
            "detail": "Smoke/ops scripts created dozens of TEMP workflows; n8n healthz/API became unresponsive.",
            "fix": "run_whieda_deactivate_temp_workflows_2026-08-01.py + preflight in smoke + direct Supabase reads",
            "commit": "6e662ea",
        },
        {
            "id": "RC3",
            "title": "Public partner-admin webhook exposed with static token",
            "detail": "whieda-partner-profile-admin-v1 published with hardcoded token; forbidden branch still wired to SQL upsert.",
            "fix": "publish_whieda_public_ref_api_readonly_2026-08-02.py removes admin path; partner edits via Partners_Ref sync only",
            "status": "pending_live_publish",
        },
    ],
    "evidence": {
        "api_fix_verification": "POST wwc-advisor-public-v1 returned ok=true with structured price answer after publish",
        "fast_smoke": "whieda_fast_smoke_2026-08-01.py 3/3 pass after recovery",
        "execution_ids": "not captured in automated export; check n8n executions for advisor-whieda-phase1 on 2026-08-01 evening",
    },
    "prevention": [
        "release gate: run_whieda_release_gate_2026-08-02.py",
        "external canary: whieda_external_canary_2026-08-02.py",
        "no TEMP workflows in smoke (whieda_runtime_read.py)",
        "secrets via WHIEDA_* env vars (whieda_runtime_env.py)",
    ],
    "publication": "operator_report_only",
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(OUT), "root_causes": len(REPORT["root_causes"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
