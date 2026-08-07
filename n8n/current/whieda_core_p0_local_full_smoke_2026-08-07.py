"""Full P0 TSV smoke locally with media/product assertions — no SSH/prod.

Usage (repo root):
  python n8n/current/whieda_core_p0_local_full_smoke_2026-08-07.py
  python n8n/current/whieda_core_p0_local_full_smoke_2026-08-07.py --base-url http://127.0.0.1:8080 --all
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))

try:
    import httpx
except ImportError:
    print("httpx required", file=sys.stderr)
    raise

from app.advisor.parity.media_assertions import evaluate_media_for_case, media_is_empty

TSV_PATH = Path(__file__).resolve().parent / "source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv"
OUT_PATH = Path(__file__).resolve().parent / "_core_p0_local_full_smoke_report.json"

SKIP_SUITES = {"review"}
OUT_OF_SCOPE = {"review_command"}


def load_rows(path: Path, all_rows: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if str(row.get("enabled", "TRUE")).upper() not in {"TRUE", "1", "YES"}:
                continue
            if row.get("suite") in SKIP_SUITES:
                continue
            if row.get("expected_intent") in OUT_OF_SCOPE:
                continue
            if not all_rows and row.get("priority") != "P0":
                continue
            rows.append(row)
    return rows


def mode_ok(row: dict[str, str], mode: str | None) -> bool:
    suite = str(row.get("suite") or "")
    if suite == "clarification":
        return str(mode or "") == "clarification"
    intent = row.get("expected_intent", "")
    mapping = {
        "product_overview": ("structured_card", "structured_price", "structured_product_detail"),
        "product_price": ("structured_price",),
        "product_photo": ("structured_photo",),
        "product_video": ("structured_video", "clarification"),
        "product_document": ("structured_certificate", "structured_video", "clarification"),
        "product_compare": ("structured_comparison", "structured_comparison_layer"),
        "clarify": ("clarification",),
        "business_faq": ("structured_business_faq",),
        "business_objection": ("structured_business_objection",),
        "greeting": ("structured_business",),
        "service_greeting": ("structured_business",),
    }
    allowed = mapping.get(intent)
    if not allowed:
        return str(mode or "") not in ("fallback",)
    return any(str(mode or "") == item or str(mode or "").startswith(item) for item in allowed)


def text_ok(row: dict[str, str], text: str) -> list[str]:
    errors: list[str] = []
    contains = str(row.get("expected_answer_contains") or "").strip().lower()
    if contains:
        aliases = {contains}
        if contains == "розничная цена":
            aliases.add("розница")
        if contains == "для партнера":
            aliases.add("для партнёра")
        if not any(alias in text.lower() for alias in aliases):
            errors.append(f"missing={contains}")
    forbid = str(row.get("expected_answer_not_contains") or "").strip().lower()
    if forbid and forbid in text.lower():
        errors.append(f"forbidden={forbid}")
    return errors


def query(client: httpx.Client, base_url: str, question: str, session: str, timeout: float) -> dict:
    payload = {"question": question, "session": session, "tenant": "whieda", "country": "BY"}
    try:
        resp = client.post(
            f"{base_url.rstrip('/')}/v1/advisor/query",
            json=payload,
            headers={"host": "wwc.best"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "ok": resp.status_code == 200,
        "status": resp.status_code,
        "answer_mode": body.get("answer_mode"),
        "answer_text": str(body.get("answer_text") or ""),
        "media": body.get("media"),
        "product": body.get("product"),
        "context": body.get("context"),
        "trace_id": body.get("trace_id"),
    }


def evaluate(row: dict[str, str], resp: dict) -> list[str]:
    errors: list[str] = []
    if not resp.get("ok"):
        errors.append(f"http_{resp.get('status')}")
        return errors
    mode = resp.get("answer_mode")
    if not mode_ok(row, mode):
        errors.append(f"mode={mode}")
    errors.extend(text_ok(row, resp.get("answer_text") or ""))
    intent = row.get("expected_intent", "")
    if intent in {"greeting", "service_greeting"} and not media_is_empty(resp.get("media")):
        errors.append("SERVICE-GREETING-NO-MEDIA")
    errors.extend(
        evaluate_media_for_case(
            expected_intent=intent,
            answer_mode=mode,
            media=resp.get("media"),
            product=resp.get("product"),
            context=resp.get("context"),
        )
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--all", action="store_true", help="Run all enabled rows, not just P0")
    parser.add_argument("--tsv", type=Path, default=TSV_PATH)
    args = parser.parse_args()

    rows = load_rows(args.tsv, args.all)
    grouped: dict[str, list[dict]] = defaultdict(list)
    singles: list[dict] = []
    for row in rows:
        conv = str(row.get("conversation_id") or "").strip()
        if conv:
            grouped[conv].append(row)
        else:
            singles.append(row)
    for conv in grouped:
        grouped[conv].sort(key=lambda item: int(item.get("sequence_no") or 0))

    results: list[dict] = []
    with httpx.Client() as client:
        for row in singles:
            case_id = row["case_id"]
            session = f"p0local-{case_id}"
            resp = query(client, args.base_url, row["input_text"], session, args.timeout)
            errors = evaluate(row, resp)
            results.append({"case_id": case_id, "ok": not errors, "errors": errors, **{k: resp.get(k) for k in ("answer_mode", "trace_id")}})
            time.sleep(args.delay)

        for conv_id, conv_rows in grouped.items():
            session = f"p0conv-{conv_id}"
            for row in conv_rows:
                resp = query(client, args.base_url, row["input_text"], session, args.timeout)
                errors = evaluate(row, resp)
                results.append(
                    {
                        "case_id": row["case_id"],
                        "conversation_id": conv_id,
                        "ok": not errors,
                        "errors": errors,
                        "answer_mode": resp.get("answer_mode"),
                    }
                )
                time.sleep(args.delay)

    passed = sum(1 for r in results if r["ok"])
    report = {
        "base_url": args.base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(100 * passed / len(results), 1) if results else 0,
        "cases": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"P0 local full smoke: {passed}/{len(results)} ({report['pass_rate']}%) -> {OUT_PATH}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
