"""Advisor shadow smoke: core path in shadow mode, log comparison fields."""

from __future__ import annotations

import argparse
import json
import time
import uuid

import requests

CASES = [
    {"question": "Привет", "label": "greeting", "service": True},
    {"question": "Что ты умеешь?", "label": "capability", "service": True},
    {"question": "Сколько стоит активатор клеток?", "label": "price", "sku": "M015-00"},
]


def media_summary(body: dict) -> dict:
    media = body.get("media") or {}
    videos = media.get("videos") or []
    documents = media.get("documents") or []
    return {
        "photo": bool(media.get("photo_url")),
        "videos": len(videos),
        "documents": len(documents),
        "video_skus": sorted({item.get("sku") for item in videos if item.get("sku")}),
        "document_skus": sorted({item.get("sku") for item in documents if item.get("sku")}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://wwc.best")
    parser.add_argument("--ref", default="ladnaya")
    args = parser.parse_args()
    errors: list[str] = []
    results: list[dict] = []

    for case in CASES:
        session_id = f"shadow-smoke-{uuid.uuid4().hex[:8]}"
        payload = {
            "question": case["question"],
            "session_id": session_id,
            "ref": args.ref,
        }
        if case.get("sku"):
            payload["sku"] = case["sku"]
        started = time.perf_counter()
        response = requests.post(
            f"{args.base.rstrip('/')}/api/advisor/query",
            json=payload,
            timeout=90,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        body = response.json() if response.text else {}
        ok = response.status_code == 200 and body.get("ok") is True
        media = media_summary(body)
        if not ok:
            errors.append(f"{case['label']}: status={response.status_code}")
        if case.get("service") and (media["photo"] or media["videos"] or media["documents"]):
            errors.append(f"{case['label']}: service response contains media")
        if case.get("sku"):
            foreign_skus = set(media["video_skus"] + media["document_skus"]) - {case["sku"]}
            if foreign_skus:
                errors.append(f"{case['label']}: foreign media SKU(s) {sorted(foreign_skus)}")
        results.append(
            {
                "label": case["label"],
                "status": response.status_code,
                "ok": body.get("ok"),
                "answer_mode": body.get("answer_mode"),
                "route": body.get("route"),
                "latency_ms": latency_ms,
                "product_sku": (body.get("product") or {}).get("sku"),
                "media": media,
            }
        )

    print(json.dumps({"results": results, "errors": errors, "ok": not errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
