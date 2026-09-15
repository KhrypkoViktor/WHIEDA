"""Replay the Telegram golden corpus through Core's advisor in-process.

Run inside the staging container (shared DB, sessions are golden-*, nothing
human is touched):

    scp live_replay.py + the two .jsonl files to /tmp on the server
    docker cp … staging-api-1:/tmp/ ; docker exec -w /app staging-api-1 python /tmp/live_replay.py

Prints TOTAL/FAILED and per-class counts; details in /tmp/golden_results.json.
Baseline 15.09.2026: 123/274 failed before the advisor fixes, 70–78 after
(the rest is fallback wording — gap.py — and corpus artefacts)."""
import asyncio, json, sys, time
sys.path.insert(0, "/app")
from app.db import close_pool, init_pool
from app.advisor.service import handle_structured_query
from app.advisor.sql.context import merge_session_context
from app.db import tenant_connection
from app.tenancy import TenantContext

TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False})


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def check(case, resp):
    exp = case["expected"]
    text = str(resp.get("answer_text") or "")
    mode = str(resp.get("answer_mode") or "")
    problems = []
    if exp.get("mode") and mode != exp["mode"]:
        problems.append(f"mode {mode!r} != {exp['mode']!r}")
    for s in exp.get("must_contain") or []:
        if s.lower() not in text.lower():
            problems.append(f"missing {s!r}")
    for s in exp.get("must_not_contain") or []:
        if s.lower() in text.lower():
            problems.append(f"forbidden {s!r}")
    return problems, mode, text


async def run_case(case, session=None):
    inp = case["input"]
    body = {"session": session or inp["session"], "question": inp["user_text"], "surface": inp.get("surface", "telegram"),
            "country": inp.get("country", "BY"), "language": inp.get("language", "ru")}
    t0 = time.perf_counter()
    try:
        resp = await handle_structured_query(TENANT, body, f"golden-{case['case_id']}")
    except Exception as exc:  # noqa: BLE001
        resp = {"answer_mode": f"EXCEPTION:{type(exc).__name__}", "answer_text": str(exc)[:200]}
    return resp, round((time.perf_counter() - t0) * 1000)


async def main():
    await init_pool()
    results = []
    try:
        for case in load("/tmp/whieda_telegram_golden_cases_v1.jsonl"):
            before = case.get("context_before") or {}
            if before:
                # The corpus seeds «what the bot already knows» (last product) for
                # single-turn follow-up cases; mirror it in the session store.
                case["input"]["session"] = f"{case['input']['session']}-{int(time.time())}"
                async with tenant_connection("whieda") as conn:
                    await merge_session_context(conn, "whieda", case["input"]["session"], before)
            resp, ms = await run_case(case)
            problems, mode, text = check(case, resp)
            results.append({"id": case["case_id"], "class": case["class"], "prio": case["priority"], "q": case["input"]["user_text"],
                            "expected": case["expected"]["mode"], "got": mode, "ms": ms, "problems": problems, "text": text[:160]})
        for flow in load("/tmp/whieda_telegram_golden_flows_v1.jsonl"):
            session = f"golden-flow-{flow['flow_id'].lower()}-{int(time.time())}"
            for turn in flow["turns"]:
                resp, ms = await run_case(turn, session=session)
                problems, mode, text = check(turn, resp)
                results.append({"id": turn["case_id"], "class": turn["class"], "prio": turn["priority"], "q": turn["input"]["user_text"],
                                "expected": turn["expected"]["mode"], "got": mode, "ms": ms, "problems": problems, "text": text[:160], "flow": flow["flow_id"]})
    finally:
        await close_pool()
    json.dump(results, open("/tmp/golden_results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    failed = [r for r in results if r["problems"]]
    print(f"TOTAL {len(results)} FAILED {len(failed)}")
    by_class = {}
    for r in results:
        c = by_class.setdefault(r["class"], [0, 0]); c[0] += 1; c[1] += bool(r["problems"])
    print("BY_CLASS", by_class)


asyncio.run(main())
