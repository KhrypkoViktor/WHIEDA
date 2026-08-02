"""Lead/ref pilot smoke: attribution, isolation, invalid ref, delivery shape."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from whieda_runtime_env import n8n_base_url, tls_verify
from whieda_runtime_pg_bootstrap import ensure_pgpassword
from whieda_runtime_read import query_rows

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat() / "WHIEDA_lead_ref_pilot_smoke.json"
PUBLIC_REF = f"{n8n_base_url()}/webhook/whieda-public-ref-v1"
PUBLIC_API = f"{n8n_base_url()}/webhook/wwc-advisor-public-v1"

FOCUS_REFS = ("ladnaya", "mariam", "test_pilot")


def case(case_id: str, *, pass_: bool, errors: list[str], detail: dict | None = None) -> dict:
    return {"id": case_id, "pass": pass_, "errors": errors, "detail": detail or {}}


def smoke_known_refs() -> dict:
    errors = []
    for ref in FOCUS_REFS:
        response = requests.get(PUBLIC_REF, params={"ref": ref}, verify=tls_verify(), timeout=30)
        body = response.json() if response.text else {}
        if response.status_code != 200 or body.get("ok") is not True:
            errors.append(f"known_fail={ref}")
    return case("known_refs_public", pass_=not errors, errors=errors)


def smoke_invalid_ref() -> dict:
    response = requests.get(PUBLIC_REF, params={"ref": "invalid-ref-smoke-xyz"}, verify=tls_verify(), timeout=30)
    body = response.json() if response.text else {}
    ok = response.status_code == 404 and body.get("error") == "ref_not_found"
    return case(
        "invalid_ref_404",
        pass_=ok,
        errors=[] if ok else [f"status={response.status_code}", f"body={body}"],
    )


def smoke_runtime_registry() -> dict:
    ensure_pgpassword()
    rows = query_rows(
        """
        SELECT ref_code, enabled, owner_id, display_mode,
               public_profile->>'public_site_url' AS public_site_url
        FROM referral_profiles
        WHERE tenant_id = 'whieda'
          AND ref_code IN ('ladnaya', 'mariam', 'test_pilot')
        ORDER BY ref_code
        """
    )
    errors = []
    if len(rows) < 3:
        errors.append("missing_pilot_refs")
    for row in rows:
        if not row.get("enabled"):
            errors.append(f"disabled={row.get('ref_code')}")
        if not str(row.get("public_site_url") or "").startswith("https://"):
            errors.append(f"bad_url={row.get('ref_code')}")
    return case("runtime_registry", pass_=not errors, errors=errors, detail={"rows": rows})


def smoke_advisor_with_ref() -> dict:
    errors = []
    for ref in ("ladnaya", "mariam"):
        payload = {
            "tenant": "whieda",
            "question": "Привет",
            "session_id": f"lead-smoke-{ref}-{uuid.uuid4().hex[:6]}",
            "ref": ref,
        }
        response = requests.post(PUBLIC_API, json=payload, verify=tls_verify(), timeout=90)
        body = response.json() if response.text else {}
        if response.status_code != 200 or body.get("ok") is not True:
            errors.append(f"api_fail={ref}")
    return case("advisor_ref_context", pass_=not errors, errors=errors)


def smoke_lead_tables_present() -> dict:
    ensure_pgpassword()
    rows = query_rows(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN (
            'website_leads', 'lead_actors', 'lead_actor_roles',
            'referral_profiles', 'lead_delivery_attempts', 'website_lead_status_history'
          )
        ORDER BY table_name
        """
    )
    names = {row["table_name"] for row in rows}
    required = {
        "website_leads",
        "lead_actors",
        "lead_actor_roles",
        "referral_profiles",
        "lead_delivery_attempts",
        "website_lead_status_history",
    }
    missing = sorted(required - names)
    return case("lead_schema_tables", pass_=not missing, errors=missing)


def main() -> None:
    cases = [
        smoke_known_refs(),
        smoke_invalid_ref(),
        smoke_runtime_registry(),
        smoke_advisor_with_ref(),
        smoke_lead_tables_present(),
    ]
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "meta": {
            "total": len(cases),
            "passed": sum(1 for item in cases if item["pass"]),
            "failed": sum(1 for item in cases if not item["pass"]),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": report["meta"], "report_path": str(OUT)}, ensure_ascii=False, indent=2))
    if report["meta"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
