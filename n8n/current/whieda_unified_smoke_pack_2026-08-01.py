"""Unified WHIEDA smoke: website API, ref profiles, structured sync, Dify-off checks.

Read-only against live except triggering structured sync webhook (idempotent).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from whieda_runtime_env import n8n_base_url, n8n_login, tls_verify
from whieda_runtime_pg_bootstrap import ensure_pgpassword
from whieda_runtime_read import query_rows as runtime_query_rows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
BASE_URL = n8n_base_url()
PUBLIC_API = f"{BASE_URL}/webhook/wwc-advisor-public-v1"
CONTRACT_API = f"{BASE_URL}/webhook/whieda-advisor-api-v1"
PUBLIC_REF_API = f"{BASE_URL}/webhook/whieda-public-ref-v1"
SYNC_WEBHOOK = f"{BASE_URL}/webhook/whieda-structured-sync-v1"
SYNC_WORKFLOW_ID = "9roEvXNsDpnwqjzH"
ADVISOR_WORKFLOW_ID = "advisor-whieda-phase1"
TELEGRAM_WEBHOOK = f"{BASE_URL}/webhook/advisor-whieda-v0"
OUT_PATH = BASE_DIR.parent / "live-exports" / date.today().isoformat() / "WHIEDA_unified_smoke_pack_2026-08-01.json"

POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}
FOCUS_REFS = ("ladnaya", "mariam")
CHAT_ID = 1147735602
USERNAME = "Khrypko_pro"
FIRST_NAME = "Viktor Test"

PARTNERS_QUERY = """
SELECT ref_code,
       owner_id,
       enabled,
       public_profile->>'public_site_url' AS public_site_url,
       public_profile->>'site_type' AS site_type,
       (public_profile->>'focus_group')::boolean AS focus_group,
       public_profile->>'access_tier' AS access_tier
FROM referral_profiles
WHERE tenant_id = 'whieda'
  AND ref_code IN ('ladnaya', 'mariam')
ORDER BY ref_code;
"""


def case_result(case_id: str, *, status: str, errors: list[str], detail: dict | None = None) -> dict:
    return {
        "id": case_id,
        "status": status,
        "errors": errors,
        "detail": detail or {},
    }


def post_json(url: str, payload: dict, *, timeout: int = 90) -> tuple[int, dict | list | str]:
    response = requests.post(url, json=payload, verify=tls_verify(), timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body


def api_payload(*, question: str, ref: str | None = None, session_suffix: str | None = None) -> dict:
    suffix = session_suffix or uuid.uuid4().hex[:10]
    payload = {
        "tenant": "whieda",
        "question": question,
        "session_id": f"smoke-unified-{suffix}",
        "locale": "ru",
        "product_context": "",
    }
    if ref:
        payload["ref"] = ref
    return payload


def check_api_response(body: dict, *, expect_structured: bool, forbid_dify_route: bool = True) -> list[str]:
    errors: list[str] = []
    if not isinstance(body, dict):
        return ["response_not_json_object"]
    if body.get("ok") is not True:
        errors.append("ok_not_true")
    answer = str(body.get("answer_text") or body.get("text") or "").strip()
    if not answer:
        errors.append("empty_answer")
    route = str(body.get("route") or "").lower()
    if forbid_dify_route and "dify" in route:
        errors.append(f"dify_route={route}")
    if expect_structured:
        mode = str(body.get("answer_mode") or "")
        route_ok = (
            route in {"structured", "direct_structured", "direct_structured_card", "direct_structured_photo", "answer"}
            or mode.startswith("structured")
            or mode.startswith("direct_structured")
            or route == "fallback"
            or any(token in route for token in ("structured", "capability", "greeting", "smalltalk"))
        )
        if not route_ok:
            errors.append(f"unexpected_route={route}")
        if "price" in mode and "structured" not in mode and not mode.startswith("direct_structured"):
            errors.append(f"unexpected_mode={mode}")
    return errors


def smoke_api_public_price() -> dict:
    status_code, body = post_json(
        PUBLIC_API,
        api_payload(question="Сколько стоит активатор?", ref="ladnaya"),
    )
    errors = []
    if status_code != 200:
        errors.append(f"http_{status_code}")
    if isinstance(body, dict):
        errors.extend(check_api_response(body, expect_structured=True))
        answer = str(body.get("answer_text") or "")
        if "активатор" not in answer.lower() and "₽" not in answer and "руб" not in answer.lower() and "byn" not in answer.lower():
            errors.append("price_answer_missing_product_or_currency")
    else:
        errors.append("invalid_body")
    return case_result(
        "api_public_ladnaya_price",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"http_status": status_code, "route": body.get("route") if isinstance(body, dict) else None},
    )


def smoke_api_public_greeting() -> dict:
    status_code, body = post_json(
        PUBLIC_API,
        api_payload(question="Привет", ref="mariam", session_suffix="greet"),
    )
    errors = []
    if status_code != 200:
        errors.append(f"http_{status_code}")
    if isinstance(body, dict):
        errors.extend(check_api_response(body, expect_structured=False, forbid_dify_route=True))
        route = str(body.get("route") or "").lower()
        mode = str(body.get("answer_mode") or "").lower()
        if "dify" in route or "rag" in route:
            errors.append("greeting_went_to_dify")
        if not any(token in route or token in mode for token in ("greeting", "capability", "structured", "smalltalk", "fallback")):
            errors.append(f"greeting_route={route}")
    else:
        errors.append("invalid_body")
    return case_result(
        "api_public_mariam_greeting",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"http_status": status_code, "route": body.get("route") if isinstance(body, dict) else None},
    )


def smoke_api_contract() -> dict:
    session_key = f"contract-{uuid.uuid4().hex[:8]}"
    payload = {
        "tenant": "whieda",
        "session": session_key,
        "question": "Цена активатора",
        "ref": "ladnaya",
        "country": "BY",
        "language": "ru",
        "surface": "website",
    }
    status_code, body = post_json(CONTRACT_API, payload)
    errors = []
    if status_code != 200:
        errors.append(f"http_{status_code}")
    if isinstance(body, dict):
        errors.extend(check_api_response(body, expect_structured=True))
        for field in ("answer_text", "route", "answer_mode"):
            if field not in body:
                errors.append(f"missing_{field}")
    else:
        errors.append("invalid_body")
    return case_result(
        "api_contract_ladnaya_price",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"http_status": status_code},
    )


def latest_execution(session, workflow_id: str, *, after_id: int = 0) -> dict | None:
    response = session.get(
        f"{BASE_URL}/rest/executions?limit=20&workflowId={workflow_id}",
        verify=tls_verify(),
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("results", [])
    for row in rows:
        if row.get("workflowId") != workflow_id:
            continue
        if int(row.get("id") or 0) <= after_id:
            continue
        return row
    return rows[0] if rows and rows[0].get("workflowId") == workflow_id else None


def smoke_structured_sync(session) -> dict:
    before = latest_execution(session, SYNC_WORKFLOW_ID)
    before_id = int((before or {}).get("id") or 0)
    trigger = requests.post(SYNC_WEBHOOK, verify=tls_verify(), timeout=120)
    errors = []
    if trigger.status_code not in {200, 201, 202, 204}:
        errors.append(f"trigger_http_{trigger.status_code}")

    execution = None
    for _ in range(45):
        time.sleep(2)
        execution = latest_execution(session, SYNC_WORKFLOW_ID, after_id=before_id)
        if execution and execution.get("status") in {"success", "error", "crashed"}:
            break
    if not execution:
        errors.append("no_sync_execution")
    elif execution.get("status") != "success":
        errors.append(f"sync_status={execution.get('status')}")
    return case_result(
        "structured_sync_webhook",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={
            "trigger_status": trigger.status_code,
            "execution_id": (execution or {}).get("id"),
            "execution_status": (execution or {}).get("status"),
        },
    )


def smoke_public_ref_api() -> dict:
    errors: list[str] = []
    try:
        known = requests.get(f"{PUBLIC_REF_API}", params={"ref": "ladnaya"}, verify=tls_verify(), timeout=30)
        missing = requests.get(
            f"{PUBLIC_REF_API}",
            params={"ref": "smoke-missing-ref-xyz"},
            verify=tls_verify(),
            timeout=30,
        )
    except requests.RequestException as exc:
        return case_result("public_ref_api", status="fail", errors=[f"request_failed:{exc}"])

    known_body = known.json() if known.text else {}
    missing_body = missing.json() if missing.text else {}
    if known.status_code != 200:
        errors.append(f"known_http_{known.status_code}")
    if not isinstance(known_body, dict) or known_body.get("ok") is not True:
        errors.append("known_not_ok")
    if missing.status_code != 404:
        errors.append(f"missing_http_{missing.status_code}")
    if not isinstance(missing_body, dict) or missing_body.get("error") != "ref_not_found":
        errors.append("missing_not_ref_not_found")
    return case_result(
        "public_ref_api",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"known_status": known.status_code, "missing_status": missing.status_code},
    )


def query_runtime_rows(query: str) -> list[dict]:
    ensure_pgpassword()
    return runtime_query_rows(query)


def smoke_partners_runtime(_session=None) -> dict:
    errors: list[str] = []
    try:
        rows = query_runtime_rows(PARTNERS_QUERY)
    except Exception as exc:
        return case_result("partners_runtime_refs", status="fail", errors=[f"query_failed:{exc}"])

    by_ref = {str(row.get("ref_code") or "").lower(): row for row in rows}
    for ref in FOCUS_REFS:
        row = by_ref.get(ref)
        if not row:
            errors.append(f"missing_ref={ref}")
            continue
        if not row.get("enabled"):
            errors.append(f"disabled_ref={ref}")
        if not str(row.get("public_site_url") or "").startswith("https://"):
            errors.append(f"bad_site_url={ref}")
        if row.get("focus_group") is not True:
            errors.append(f"focus_group_false={ref}")
        if not str(row.get("access_tier") or "").strip():
            errors.append(f"missing_access_tier={ref}")
    return case_result(
        "partners_runtime_refs",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"rows": rows},
    )


def decode_graph(graph):
    memo, busy = {}, set()

    def resolve(value):
        if isinstance(value, str) and value.isdigit() and int(value) < len(graph):
            return index(int(value))
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    def index(i):
        if i in memo:
            return memo[i]
        if i in busy:
            return f"[Circular:{i}]"
        busy.add(i)
        memo[i] = resolve(graph[i])
        busy.remove(i)
        return memo[i]

    return index(0)


def smoke_telegram_greeting_no_dify(session) -> dict:
    try:
        before_rows = session.get(
            f"{BASE_URL}/rest/executions?limit=5&workflowId={ADVISOR_WORKFLOW_ID}",
            verify=tls_verify(),
            timeout=60,
        ).json().get("data", {}).get("results", [])
    except requests.RequestException as exc:
        return case_result("telegram_capability_no_dify", status="fail", errors=[f"prefetch_failed:{exc}"])

    before_id = max(int(row.get("id") or 0) for row in before_rows) if before_rows else 0
    prompt = "что ты умеешь"
    message_id = int(time.time() * 1000) % 2147483647
    payload = {
        "whieda_synthetic_test": True,
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "text": prompt,
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": FIRST_NAME, "username": USERNAME},
        },
    }
    try:
        requests.post(TELEGRAM_WEBHOOK, json=payload, verify=tls_verify(), timeout=90).raise_for_status()
    except requests.RequestException as exc:
        return case_result("telegram_capability_no_dify", status="fail", errors=[f"webhook_failed:{exc}"])

    execution_id = None
    run_data = {}
    errors = []
    for _ in range(45):
        time.sleep(1)
        try:
            rows = session.get(
                f"{BASE_URL}/rest/executions?limit=30&workflowId={ADVISOR_WORKFLOW_ID}",
                verify=tls_verify(),
                timeout=60,
            ).json().get("data", {}).get("results", [])
        except requests.RequestException:
            continue
        for row in rows:
            exec_id = int(row.get("id") or 0)
            if exec_id <= before_id:
                continue
            try:
                detail = session.get(
                    f"{BASE_URL}/rest/executions/{exec_id}?includeData=true",
                    verify=tls_verify(),
                    timeout=90,
                ).json()
            except requests.RequestException:
                continue
            data = detail.get("data", {}).get("data")
            if not isinstance(data, str):
                continue
            run_data = decode_graph(json.loads(data)).get("resultData", {}).get("runData", {})
            normalized = None
            for name, runs in run_data.items():
                if "Normalize Payload" in name and runs:
                    normalized = runs[0]["data"]["main"][0][-1]["json"]
                    break
            if str((normalized or {}).get("message_text") or "").strip() == prompt:
                execution_id = exec_id
                break
        if execution_id:
            break

    if not execution_id:
        errors.append("execution_not_found")
    else:
        dify_called = any("Dify advisor" in name for name in run_data)
        if dify_called:
            errors.append("dify_called_for_capability")
        validated = {}
        for name, runs in run_data.items():
            if "Validate Dify Response" in name and runs:
                validated = runs[0]["data"]["main"][0][-1]["json"]
                break
        route = str(validated.get("route") or "")
        if route and "dify" in route.lower():
            errors.append(f"route={route}")
    return case_result(
        "telegram_capability_no_dify",
        status="pass" if not errors else "fail",
        errors=errors,
        detail={"execution_id": execution_id},
    )


def login_session():
    email, password = n8n_login()
    deadline = time.time() + 120
    last_error = None
    while time.time() < deadline:
        session = requests.Session()
        try:
            response = session.post(
                f"{BASE_URL}/rest/login",
                json={"emailOrLdapLoginId": email, "password": password},
                verify=tls_verify(),
                timeout=30,
            )
            if response.ok:
                return session
            last_error = f"login={response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(3)
    raise requests.RequestException(f"n8n login failed: {last_error}")


def main() -> None:
    try:
        import subprocess

        subprocess.run(
            [sys.executable, str(BASE_DIR / "run_whieda_deactivate_temp_workflows_2026-08-01.py")],
            check=False,
            timeout=120,
        )
    except Exception:
        pass

    try:
        session = login_session()
    except requests.RequestException as exc:
        report = {
            "meta": {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "total": 0,
                "passed": 0,
                "failed": 1,
                "blocked": True,
                "block_reason": f"n8n_unreachable:{exc}",
            },
            "cases": [],
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"meta": report["meta"], "report_path": str(OUT_PATH)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc

    cases = [
        smoke_api_public_price(),
        smoke_api_public_greeting(),
        smoke_api_contract(),
        smoke_public_ref_api(),
        smoke_structured_sync(session),
        smoke_partners_runtime(session),
        smoke_telegram_greeting_no_dify(session),
    ]
    passed = sum(case["status"] == "pass" for case in cases)
    report = {
        "meta": {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
        },
        "cases": cases,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "meta": report["meta"],
        "failures": [{"id": c["id"], "errors": c["errors"]} for c in cases if c["status"] == "fail"],
        "report_path": str(OUT_PATH),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if passed != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
