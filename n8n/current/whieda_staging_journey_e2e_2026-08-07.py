"""Staging E2E: visitor session → events → link token → exchange → onboarding → report.

Local/staging only — no prod, no n8n webhook changes.

Usage:
  python whieda_staging_journey_e2e_2026-08-07.py --base-url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx required", file=sys.stderr)
    raise

OUT_PATH = Path(__file__).resolve().parent / "_staging_journey_e2e_report.json"
HOST = "wwc.best"


def step(name: str, ok: bool, detail: dict | None = None) -> dict:
    return {"step": name, "ok": ok, "detail": detail or {}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--ref", default="ladnaya")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    headers = {"host": HOST, "content-type": "application/json"}

    session_id = str(uuid.uuid4())
    telegram_user = 900000000 + int(uuid.uuid4().int % 999999)
    results: list[dict] = []

    with httpx.Client(timeout=20.0) as client:
        # 1. Upsert visitor session
        r = client.put(
            f"{base}/api/v1/visitor-sessions",
            headers=headers,
            json={
                "visitor_session_id": session_id,
                "ref": args.ref,
                "journey_type": "product",
                "context": {"topic": "spirulina", "last_product_sku": "SKU-DEMO"},
            },
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        results.append(step("visitor_session_upsert", r.status_code == 200 and body.get("ok"), body))
        first_ref = body.get("first_ref")

        # 2. Immutable first_ref
        r2 = client.put(
            f"{base}/api/v1/visitor-sessions",
            headers=headers,
            json={"visitor_session_id": session_id, "ref": "evilref", "journey_type": "product"},
        )
        b2 = r2.json()
        results.append(
            step(
                "first_ref_immutable",
                b2.get("first_ref") == first_ref and first_ref == args.ref,
                b2,
            )
        )

        # 3. Interaction event idempotency
        idem = f"e2e-route-{session_id[:8]}"
        ev_payload = {"event_type": "route_opened", "idempotency_key": idem, "visitor_session_id": session_id, "payload": {"route": "product"}}
        r3a = client.post(f"{base}/api/v1/interaction-events", headers=headers, json=ev_payload)
        r3b = client.post(f"{base}/api/v1/interaction-events", headers=headers, json=ev_payload)
        b3a, b3b = r3a.json(), r3b.json()
        results.append(step("event_created", b3a.get("created") is True, b3a))
        results.append(step("event_idempotent", b3b.get("created") is False, b3b))

        # 4. Link token (needs PLATFORM_TELEGRAM_BOT_USERNAME on Core)
        r4 = client.post(
            f"{base}/api/v1/telegram-link-tokens",
            headers=headers,
            json={"visitor_session_id": session_id, "ref": args.ref, "journey_type": "product"},
        )
        b4 = r4.json() if r4.headers.get("content-type", "").startswith("application/json") else {}
        token = b4.get("link_token")
        results.append(step("link_token_create", r4.status_code == 200 and bool(token), {"status": r4.status_code, "error": b4.get("error")}))

        # 5. Exchange
        if token:
            r5 = client.post(
                f"{base}/v1/telegram-link-tokens/exchange",
                headers=headers,
                json={"token": token, "telegram_user_id": telegram_user},
            )
            b5 = r5.json()
            results.append(step("link_token_exchange", r5.status_code == 200, b5))

            r5b = client.post(
                f"{base}/v1/telegram-link-tokens/exchange",
                headers=headers,
                json={"token": token, "telegram_user_id": telegram_user + 1},
            )
            results.append(step("link_token_reuse_blocked", r5b.status_code == 409, {"status": r5b.status_code}))
        else:
            results.append(step("link_token_exchange", False, {"skipped": "no_token"}))
            results.append(step("link_token_reuse_blocked", False, {"skipped": "no_token"}))

        # 6. Onboarding enroll + command
        r6 = client.post(
            f"{base}/v1/onboarding/enroll",
            headers=headers,
            json={"telegram_user_id": telegram_user, "first_ref": args.ref, "idempotency_key": f"e2e-{telegram_user}"},
        )
        b6 = r6.json()
        results.append(step("onboarding_enroll", r6.status_code == 200, b6))

        r6b = client.post(
            f"{base}/v1/onboarding/enroll",
            headers=headers,
            json={"telegram_user_id": telegram_user, "first_ref": args.ref, "idempotency_key": f"e2e-{telegram_user}"},
        )
        b6b = r6b.json()
        results.append(step("onboarding_enroll_idempotent", b6b.get("created") is False, b6b))

        r7 = client.post(
            f"{base}/v1/onboarding/command",
            headers=headers,
            json={"telegram_user_id": telegram_user, "text": "мой план"},
        )
        results.append(step("onboarding_plan", r7.status_code == 200 and "План" in r7.json().get("answer_text", ""), r7.json()))

        # 7. Leader digest
        r8 = client.get(f"{base}/v1/reports/leader-digest?days=7", headers=headers)
        b8 = r8.json() if r8.headers.get("content-type", "").startswith("application/json") else {}
        results.append(step("leader_digest", r8.status_code == 200 and b8.get("ok"), {"metrics_keys": list((b8.get("metrics") or {}).keys())}))

        # 8. Service greeting no media (advisor)
        r9 = client.post(
            f"{base}/v1/advisor/query",
            headers=headers,
            json={"question": "привет", "session": f"e2e-{session_id[:8]}", "country": "BY"},
        )
        b9 = r9.json() if r9.headers.get("content-type", "").startswith("application/json") else {}
        media = b9.get("media") or {}
        polluted = bool(media.get("photo_url") or media.get("videos") or media.get("documents"))
        results.append(step("SERVICE-GREETING-NO-MEDIA", r9.status_code == 200 and not polluted, {"media": media}))

    passed = sum(1 for item in results if item["ok"])
    report = {
        "base_url": base,
        "session_id": session_id,
        "telegram_user_id": telegram_user,
        "passed": passed,
        "total": len(results),
        "steps": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Staging E2E: {passed}/{len(results)} -> {OUT_PATH}")
    for item in results:
        mark = "OK" if item["ok"] else "FAIL"
        print(f"  [{mark}] {item['step']}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
