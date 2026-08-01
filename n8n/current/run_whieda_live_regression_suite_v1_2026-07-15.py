"""Run product, feature, performance and safety smoke packs into one report."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
PACKS = [
    ("p0", BASE_DIR / "whieda_live_p0_smoke_2026-07-13.py", EXPORT_DIR / "WHIEDA_live_p0_smoke_report.json"),
    ("products", BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py", EXPORT_DIR / "WHIEDA_live_demo_smoke_v2.json"),
    ("features", BASE_DIR / "whieda_live_feature_smoke_v1_2026-07-15.py", EXPORT_DIR / "WHIEDA_live_feature_smoke_v1.json"),
]
PERFORMANCE = ("performance", BASE_DIR / "whieda_live_sql_performance_smoke_2026-07-26.py", EXPORT_DIR / "WHIEDA_live_sql_performance_smoke.json")
SAFETY = ("safety", BASE_DIR / "whieda_live_corpus_safety_regression_2026-07-26.py", EXPORT_DIR / "WHIEDA_live_corpus_safety_regression.json")
OUT_PATH = EXPORT_DIR / "WHIEDA_live_regression_suite_v1.json"


def main():
    packs = []
    for name, script, report_path in PACKS:
        completed = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=900)
        if completed.returncode != 0:
            raise RuntimeError(f"{name} smoke failed to run: {completed.stderr[-1000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        meta = report["meta"]
        packs.append({"name": name, "report_path": str(report_path), "meta": meta, "total": meta.get("total", meta.get("cases_total", 0)), "passed": meta.get("passed", 0)})

    name, script, performance_path = PERFORMANCE
    completed = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} smoke failed to run: {completed.stderr[-1000:]}")
    performance = json.loads(performance_path.read_text(encoding="utf-8"))
    if not performance.get("pass"):
        raise RuntimeError("performance smoke failed")

    name, script, safety_path = SAFETY
    completed = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=900)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} smoke failed to run: {completed.stderr[-1000:]}")
    safety = json.loads(safety_path.read_text(encoding="utf-8"))
    if not safety.get("pass"):
        raise RuntimeError("safety corpus regression failed")

    total = sum(pack["total"] for pack in packs) + safety["tested"]
    passed = sum(pack["passed"] for pack in packs) + safety["passed"]
    result = {
        "meta": {
            "date": date.today().isoformat(),
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(100 * passed / total, 1) if total else 0,
        },
        "packs": packs,
        "performance": {"report_path": str(performance_path), "metrics": performance["metrics"], "pass": performance["pass"]},
        "safety": {"report_path": str(safety_path), "tested": safety["tested"], "passed": safety["passed"], "pass": safety["pass"]},
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": result["meta"], "report_path": str(OUT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
