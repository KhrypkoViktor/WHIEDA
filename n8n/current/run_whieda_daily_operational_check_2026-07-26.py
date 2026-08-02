"""One-command operational check. It never alters n8n or master data; smoke uses the test account only."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE.parent / "live-exports" / datetime.now().date().isoformat()


def run(script: str, timeout: int = 180):
    result = subprocess.run([sys.executable, str(BASE / script)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return {"script": script, "exit_code": result.returncode, "stdout_tail": result.stdout[-1200:], "stderr_tail": result.stderr[-1200:]}


def main():
    health = run("run_whieda_health_check.py")
    canary = run("whieda_external_canary_2026-08-02.py", timeout=240)
    fast_smoke = run("whieda_fast_smoke_2026-08-01.py", timeout=240)
    unified_smoke = run("whieda_unified_smoke_pack_2026-08-01.py", timeout=600)
    sheet_smoke = run("whieda_live_sheet_smoke_2026-07-26.py", timeout=360)
    regression_path = OUT / "WHIEDA_live_regression_suite_v1.json"
    regression = json.loads(regression_path.read_text(encoding="utf-8")) if regression_path.exists() else None
    report = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "master_data_changed": False,
        "health": health,
        "canary": canary,
        "fast_smoke": fast_smoke,
        "unified_smoke": unified_smoke,
        "sheet_smoke": sheet_smoke,
        "latest_regression": regression.get("meta") if regression else None,
        "alerts": [],
    }
    if health["exit_code"] != 0: report["alerts"].append("health_check_failed")
    if canary["exit_code"] != 0: report["alerts"].append("canary_failed")
    if fast_smoke["exit_code"] != 0: report["alerts"].append("fast_smoke_failed")
    if unified_smoke["exit_code"] != 0: report["alerts"].append("unified_smoke_failed")
    if sheet_smoke["exit_code"] != 0: report["alerts"].append("sheet_smoke_failed")
    if regression and regression.get("meta", {}).get("failed", 0): report["alerts"].append("regression_failures_present")
    report["status"] = "green" if not report["alerts"] else "attention"
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "WHIEDA_daily_operational_check.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "target": str(target), "alerts": report["alerts"]}, ensure_ascii=False))


if __name__ == "__main__": main()
