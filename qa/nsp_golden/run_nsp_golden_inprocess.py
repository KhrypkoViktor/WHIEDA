#!/usr/bin/env python3
"""Replay NSP golden cases in-process against the configured database (staging).

    PLATFORM_DATABASE_URL=... python /src/qa/nsp_golden/run_nsp_golden_inprocess.py \
        --cases /src/qa/nsp_golden/nsp_telegram_golden_cases_v1.jsonl --out /reports/nsp_golden_run.tsv

No Telegram traffic: calls run_structured_query() directly for tenant nsp-maxim.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "platform-api"))

from app.advisor.sql.engine import run_structured_query  # noqa: E402
from app.db import close_pool, init_pool
from app.tenancy import TenantContext

TENANT = TenantContext(
    tenant_id="nsp-maxim",
    status="active",
    display_name="NSP",
    entitlements={"structure_basic": True},
)


def check(case: dict, resp: dict) -> tuple[bool, list[str]]:
    exp = case["expected"]
    text = str(resp.get("answer_text") or "")
    problems: list[str] = []
    if exp.get("mode") and resp.get("answer_mode") != exp["mode"]:
        problems.append(f"mode {resp.get('answer_mode')}!={exp['mode']}")
    if exp.get("gap_kind") and resp.get("gap_kind") != exp["gap_kind"]:
        problems.append(f"gap {resp.get('gap_kind')}!={exp['gap_kind']}")
    for frag in exp.get("must_contain", []):
        if frag.lower() not in text.lower():
            problems.append(f"missing «{frag}»")
    for frag in exp.get("must_not_contain", []):
        if frag.lower() in text.lower():
            problems.append(f"forbidden «{frag}»")
    photo = (exp.get("expected_media") or {}).get("photo")
    media = resp.get("media") if isinstance(resp.get("media"), dict) else {}
    has_photo = bool(media.get("photo_url") or media.get("filename") or media.get("url"))
    if photo == "required" and not has_photo:
        problems.append("photo missing")
    if photo == "none" and has_photo and exp.get("mode") not in ("structured_card",):
        problems.append("unexpected photo")
    return (not problems, problems)


async def main_async(cases_path: Path, out_path: Path) -> int:
    cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    await init_pool()
    rows = []
    try:
        for case in cases:
            body = {
                "question": case["input"]["user_text"],
                "session": case["input"].get("session") or f"golden-{case['case_id']}",
                "surface": "telegram",
                "country": "BY",
                "language": "ru",
            }
            try:
                resp = await run_structured_query(TENANT, body, f"golden-{case['case_id']}")
            except Exception as exc:  # noqa: BLE001
                resp = {"answer_mode": "error", "answer_text": f"EXC {type(exc).__name__}: {exc}"}
            ok, problems = check(case, resp)
            rows.append(
                {
                    "case_id": case["case_id"],
                    "class": case["class"],
                    "question": case["input"]["user_text"],
                    "ok": "ok" if ok else "FAIL",
                    "mode": resp.get("answer_mode"),
                    "gap_kind": resp.get("gap_kind") or "",
                    "problems": "; ".join(problems),
                    "answer": str(resp.get("answer_text") or "")[:160].replace("\n", " "),
                }
            )
    finally:
        await close_pool()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    total = len(rows)
    passed = sum(r["ok"] == "ok" for r in rows)
    by_class: dict[str, list[int]] = {}
    for r in rows:
        by_class.setdefault(r["class"], [0, 0])
        by_class[r["class"]][1] += 1
        if r["ok"] == "ok":
            by_class[r["class"]][0] += 1
    print(json.dumps({"total": total, "passed": passed, "by_class": by_class, "out": str(out_path)}, ensure_ascii=False))
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    return asyncio.run(main_async(args.cases, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
