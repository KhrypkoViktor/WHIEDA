"""Focused live smoke for objections, SQL coach and the Deep_corpus route."""
import importlib.util
import json
import time
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
RUNNER_PATH = BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py"
OUT_PATH = BASE_DIR.parent / "live-exports" / date.today().isoformat() / "WHIEDA_live_feature_smoke_v1.json"


def load_runner():
    spec = importlib.util.spec_from_file_location("whieda_demo_smoke", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CASES = [
    {
        "id": "COMMUNITY-WHIEDA-WORLD-CLUB",
        "prompt": "дай официальный канал WHIEDA",
        "answer_mode": "direct_structured_community",
        "dify_called": False,
        "contains": "t.me/Whieda_world_club",
    },
    {
        "id": "EVENT-MINSK-THURSDAY",
        "prompt": "какие ближайшие мероприятия WHIEDA",
        "answer_mode": "direct_structured_event",
        "dify_called": False,
        "contains": "Кальварийская, 4",
    },
    {
        "id": "BUSINESS-FAQ-PV",
        "prompt": "PV это деньги?",
        "answer_mode": "direct_structured_business_faq",
        "dify_called": False,
        "contains": "не одно и то же",
    },
    {
        "id": "BUSINESS-FAQ-CASHBACK",
        "prompt": "кэшбэк можно вывести?",
        "answer_mode": "direct_structured_business_faq",
        "dify_called": False,
        "contains": "повторных покупок",
    },
    {
        "id": "OBJECTION-MLM",
        "prompt": "это млм",
        "answer_mode": "direct_structured_business_objection",
        "dify_called": False,
        "contains": "неприятный опыт",
    },
    {
        "id": "OBJECTION-THINK",
        "prompt": "я подумаю",
        "answer_mode": "direct_structured_business_objection",
        "dify_called": False,
        "contains": "над чем именно",
    },
    {
        "id": "COACH-MENU",
        "prompt": "коуч",
        "answer_mode": "direct_coach_objections_menu",
        "dify_called": False,
        "contains": "Выбери ситуацию",
    },
    {
        "id": "COACH-FIRST-WEEK-MENU",
        "prompt": "коуч старт",
        "answer_mode": "direct_coach_first_week_start",
        "dify_called": False,
        "contains": "День 1. Твоя опора",
    },
    {
        "id": "COACH-FIRST-WEEK-COMPLETE-DAY-1",
        "prompt": "коуч готово",
        "answer_mode": "direct_coach_first_week_complete",
        "dify_called": False,
        "contains": "День 2. Короткое знакомство",
    },
    {
        "id": "COACH-FIRST-WEEK-CONTINUE",
        "prompt": "коуч продолжить",
        "answer_mode": "direct_coach_first_week_continue",
        "dify_called": False,
        "contains": "День 2. Короткое знакомство",
    },
    {
        "id": "COACH-PRACTICE",
        "prompt": "коуч 4",
        "answer_mode": "direct_coach_objection_practice",
        "dify_called": False,
        "contains": "Это МЛМ / пирамида",
    },
    {
        "id": "COACH-FEEDBACK",
        "prompt": "коуч ответ 4 Понимаю, у многих был неприятный опыт. Что именно тебя беспокоит: продукт или правила выплат?",
        "answer_mode": "direct_coach_objection_feedback",
        "dify_called": False,
        "contains": "✓ Сначала признал сомнение человека",
    },
    {
        "id": "DEEP-ACTIVATOR",
        "prompt": "сделай глубокий разбор: как работает активатор клеток",
        "answer_mode": "deep_internal_library",
        "dify_called": True,
        "contains": "Источник:",
        "max_occurrences": {"Источник:": 1},
    },
    {
        "id": "FALLBACK-RUSSIAN",
        "prompt": "потом",
        "answer_mode": "fallback",
        "dify_called": True,
        "contains_any": ["не играть в угадайку", "нет подтвержденного ответа", "не фантазировать", "нужна точная проверка", "помечу на доработку", "лучше подтвердить отдельно", "не хочу здесь гадать"],
        "forbid": ["This needs human review", "I have flagged", "I do not have confirmed"],
    },
]


def main():
    smoke = load_runner()
    session = smoke.login()
    previous_id = max(smoke.execution_ids(session, limit=25), default=0)
    results = []

    for case in CASES:
        started = time.monotonic()
        message_id = smoke.send(session, case["prompt"])
        execution_id, summary = smoke.wait_for(session, message_id, previous_id)
        previous_id = max(previous_id, execution_id or previous_id)
        reply = str((summary or {}).get("reply_text") or "")
        errors = []
        if not summary:
            errors.append("execution_not_found")
        else:
            if summary.get("execution_status") != "success":
                errors.append("execution_status=" + str(summary.get("execution_status")))
            if summary.get("answer_mode") != case["answer_mode"]:
                errors.append("answer_mode=" + str(summary.get("answer_mode")))
            if summary.get("dify_called") is not case["dify_called"]:
                errors.append("dify_called=" + str(summary.get("dify_called")))
            if "contains" in case and case["contains"].lower() not in reply.lower():
                errors.append("missing_text=" + case["contains"])
            if "contains_any" in case and not any(phrase.lower() in reply.lower() for phrase in case["contains_any"]):
                errors.append("missing_any_text")
            for phrase in case.get("forbid", []):
                if phrase.lower() in reply.lower():
                    errors.append("forbidden_text=" + phrase)
            for phrase, maximum in case.get("max_occurrences", {}).items():
                if reply.lower().count(str(phrase).lower()) > int(maximum):
                    errors.append("too_many_occurrences=" + str(phrase))
        results.append({
            "id": case["id"],
            "prompt": case["prompt"],
            "execution_id": execution_id,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "status": "pass" if not errors else "fail",
            "errors": errors,
            "summary": summary,
        })
        time.sleep(1)

    passed = sum(item["status"] == "pass" for item in results)
    report = {
        "meta": {
            "date": date.today().isoformat(),
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(100 * passed / len(results), 1),
        },
        "results": results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": report["meta"], "report_path": str(OUT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
