"""External canary: healthz, advisor query, public ref, Telegram SQL path."""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from whieda_runtime_env import n8n_base_url, tls_verify

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat() / "WHIEDA_external_canary.json"


def check_healthz(base: str) -> dict:
    started = time.perf_counter()
    try:
        response = requests.get(f"{base}/healthz", verify=tls_verify(), timeout=15)
        return {
            "id": "healthz",
            "pass": response.ok,
            "status": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except requests.RequestException as exc:
        return {"id": "healthz", "pass": False, "error": str(exc)}


def check_advisor_query(base: str) -> dict:
    started = time.perf_counter()
    payload = {
        "tenant": "whieda",
        "question": "Сколько стоит активатор?",
        "session_id": f"canary-{uuid.uuid4().hex[:8]}",
        "ref": "ladnaya",
        "locale": "ru",
    }
    try:
        response = requests.post(
            f"{base}/webhook/wwc-advisor-public-v1",
            json=payload,
            verify=tls_verify(),
            timeout=90,
        )
        body = response.json() if response.text else {}
        ok = (
            response.status_code == 200
            and isinstance(body, dict)
            and body.get("ok") is True
            and bool(str(body.get("answer_text") or "").strip())
        )
        return {
            "id": "advisor_public_query",
            "pass": ok,
            "status": response.status_code,
            "route": body.get("route") if isinstance(body, dict) else None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except requests.RequestException as exc:
        return {"id": "advisor_public_query", "pass": False, "error": str(exc)}


def check_public_ref(base: str) -> dict:
    started = time.perf_counter()
    try:
        known = requests.get(
            f"{base}/webhook/whieda-public-ref-v1",
            params={"ref": "ladnaya"},
            verify=tls_verify(),
            timeout=30,
        )
        known_body = known.json() if known.text else {}
        missing = requests.get(
            f"{base}/webhook/whieda-public-ref-v1",
            params={"ref": "canary-missing-ref-xyz"},
            verify=tls_verify(),
            timeout=30,
        )
        missing_body = missing.json() if missing.text else {}
        ok = (
            known.status_code == 200
            and isinstance(known_body, dict)
            and known_body.get("ok") is True
            and missing.status_code == 404
            and isinstance(missing_body, dict)
            and missing_body.get("error") == "ref_not_found"
        )
        return {
            "id": "public_ref_api",
            "pass": ok,
            "known_status": known.status_code,
            "missing_status": missing.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except requests.RequestException as exc:
        return {"id": "public_ref_api", "pass": False, "error": str(exc)}


def check_telegram_sql_path(base: str) -> dict:
    """Lightweight synthetic Telegram prompt — capability route without Dify."""
    started = time.perf_counter()
    payload = {
        "whieda_synthetic_test": True,
        "message": {
            "message_id": int(time.time() * 1000) % 2147483647,
            "date": int(time.time()),
            "text": "что ты умеешь",
            "chat": {"id": 1147735602, "type": "private"},
            "from": {"id": 1147735602, "is_bot": False, "first_name": "Canary", "username": "Khrypko_pro"},
        },
    }
    try:
        response = requests.post(
            f"{base}/webhook/advisor-whieda-v0",
            json=payload,
            verify=tls_verify(),
            timeout=90,
        )
        return {
            "id": "telegram_sql_actor",
            "pass": response.status_code in {200, 201, 202, 204},
            "status": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except requests.RequestException as exc:
        return {"id": "telegram_sql_actor", "pass": False, "error": str(exc)}


def main() -> None:
    base = n8n_base_url()
    checks = [
        check_healthz(base),
        check_advisor_query(base),
        check_public_ref(base),
        check_telegram_sql_path(base),
    ]
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "tls_verify": tls_verify(),
        "checks": checks,
        "pass": all(item["pass"] for item in checks),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "report_path": str(OUT), "checks": checks}, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
